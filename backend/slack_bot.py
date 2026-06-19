import logging
import time
from datetime import datetime, timedelta

from slack_bolt.adapter.socket_mode import SocketModeHandler

from . import config
from .ca_reviews import ca_ts, ca_ts_expirados, manejar_mensaje_ca
from .clients import slack_app
from .hierarchy import comparar_jerarquia, sufijo_preguntas
from .notion_service import buscar_empleado_en_lista, buscar_empleado_y_cargo, guardar_en_notion, obtener_cargo_por_slack_id, obtener_nombre_por_id_usuario, sugerir_empleados_parecidos
from .state import avisos_responder_en_hilo, conversaciones, evaluacion_hora, evaluacion_ultimo_recordatorio, evaluacion_ts, evaluacion_ts_expirados, lock
from .utils import normalizar_nombre


def enviar_una_evaluacion():
    try:
        resp = slack_app.client.chat_postMessage(
            channel=config.CHANNEL_ID,
            text=(
                "📍 ¿En qué proyecto estás trabajando ahora? "
                "Si estás en más de uno, elige solo uno y escribe el nombre del proyecto.\n"
                "_Si en algún momento quieres cancelar la evaluación, escribe SOS en el hilo._"
                f"{config.INSTRUCCIONES_RESPONDER_EN_HILO}"
            ),
        )
        with lock:
            evaluacion_ts_expirados.update(evaluacion_ts)
            evaluacion_ts.clear()
            evaluacion_ts.add(resp["ts"])
            evaluacion_hora[resp["ts"]] = time.time()
        logging.info(f"Evaluación iniciada, ts={resp['ts']}")
    except Exception:
        logging.exception("Error enviando mensaje de evaluación")


def enviar_o_crear_revision(origen):
    enviar_una_evaluacion()


def enviar_evaluaciones_modo_prueba():
    enviar_o_crear_revision("modo prueba")
    while True:
        time.sleep(config.INTERVALO_PRUEBA_SEGUNDOS)
        enviar_o_crear_revision("modo prueba")


def siguiente_envio_produccion(ahora=None):
    ahora = ahora or datetime.now(config.ZONA_HORARIA_MADRID)
    objetivo = datetime.combine(ahora.date(), config.HORA_ENVIO_PRODUCCION, tzinfo=config.ZONA_HORARIA_MADRID)
    dias_hasta_viernes = (config.DIA_ENVIO_PRODUCCION - ahora.weekday()) % 7
    objetivo = objetivo + timedelta(days=dias_hasta_viernes)
    if objetivo < ahora:
        objetivo = objetivo + timedelta(days=7)
    return objetivo


def enviar_evaluaciones_programadas():
    if config.APP_MODE != "produccion":
        enviar_evaluaciones_modo_prueba()
        return
    while True:
        objetivo = siguiente_envio_produccion()
        espera = max(1, (objetivo - datetime.now(config.ZONA_HORARIA_MADRID)).total_seconds())
        logging.info(f"Próxima evaluación programada: {objetivo.isoformat()}")
        time.sleep(espera)
        enviar_o_crear_revision("modo producción")


def resumen_respuestas(respuestas):
    return (
        "Resumen de tus respuestas:\n"
        f"- Persona evaluada: {respuestas.get('evaluado', '')}\n"
        f"- Proyecto: {respuestas.get('proyecto', '')}\n"
        f"- Satisfacción: {respuestas.get('satisfaccion', '')}\n"
        f"- Mejor aspecto: {respuestas.get('mejor_aspecto', '')}\n"
        f"- Peor aspecto: {respuestas.get('peor_aspecto', '')}\n\n"
        "¿Estás satisfecho con tus respuestas?\n"
        "Responde `sí` para guardar en Notion o `modificar` para cambiar una respuesta concreta."
    )


OPCIONES_MODIFICACION = {
    "1": "evaluado", "persona": "evaluado", "persona evaluada": "evaluado", "evaluado": "evaluado",
    "2": "proyecto", "proyecto": "proyecto",
    "3": "satisfaccion", "satisfaccion": "satisfaccion", "satisfacción": "satisfaccion",
    "4": "mejor_aspecto", "mejor": "mejor_aspecto", "mejor aspecto": "mejor_aspecto",
    "5": "peor_aspecto", "peor": "peor_aspecto", "peor aspecto": "peor_aspecto",
}


def texto_menu_modificacion():
    return (
        "¿Qué respuesta quieres modificar?\n"
        "1. Persona evaluada\n2. Proyecto\n3. Satisfacción\n4. Mejor aspecto\n5. Peor aspecto\n\n"
        "Responde con el número o el nombre del campo."
    )


def clave_modificacion(texto):
    return OPCIONES_MODIFICACION.get(normalizar_nombre(texto))


def texto_pregunta_por_clave(clave, sufijo=""):
    for pregunta in config.PREGUNTAS:
        if pregunta["clave"] == clave:
            texto = pregunta["texto"]
            if sufijo and clave in ("satisfaccion", "mejor_aspecto", "peor_aspecto"):
                texto += sufijo
            return texto
    return "Escribe la nueva respuesta."


def respuesta_es_confirmacion(texto):
    return normalizar_nombre(texto) in {"si", "sí", "s", "ok", "okay", "confirmar", "guardar", "correcto"}


def respuesta_es_modificacion(texto):
    return normalizar_nombre(texto) in {"modificar", "cambiar", "editar", "repetir"}


def _es_si(texto):
    return normalizar_nombre(texto) in {"si", "sí", "s", "yes", "y", "ok", "okay", "claro", "vale"}


def _es_no(texto):
    return normalizar_nombre(texto) in {"no", "n", "nope", "nel"}


def _es_valor_satisfaccion(texto):
    try:
        return int(texto) in {1, 2, 3, 4, 5}
    except Exception:
        return False


def _parece_saludo(texto):
    return normalizar_nombre(texto).strip(" ?!¡¿.") in {"hola", "buenas", "hey", "ei"}


def _debe_avisar_responder_en_hilo(channel, user_id):
    ahora = time.time()
    clave = (channel, user_id)
    ultimo = avisos_responder_en_hilo.get(clave, 0)
    if ahora - ultimo < 60:
        return False
    avisos_responder_en_hilo[clave] = ahora
    return True


def _mensaje_empleado_no_encontrado(texto):
    sugerencias = sugerir_empleados_parecidos(texto)
    if sugerencias:
        opciones = "\n".join(f"- {nombre}" for nombre in sugerencias)
        return (
            f"*{texto}* no aparece tal cual en la lista de empleados.\n"
            "¿Querías decir alguno de estos nombres? Responde copiando el nombre exacto:\n"
            f"{opciones}"
        )
    return (
        f"*{texto}* no aparece tal cual en la lista de empleados. "
        "Escribe nombre y apellido como aparece en la lista."
    )


def _nombre_real(user_id: str, logger) -> str:
    nombre = obtener_nombre_por_id_usuario(user_id)
    if nombre:
        return nombre
    try:
        resp = slack_app.client.users_info(user=user_id)
        user = resp.get("user", {})
        profile = user.get("profile", {})
        nombre = (
            (user.get("real_name") or "").strip()
            or (profile.get("real_name") or "").strip()
            or (profile.get("display_name") or "").strip()
            or (user.get("name") or "").strip()
        )
        return nombre if nombre else user_id
    except Exception:
        logger.warning(f"No se pudo obtener el nombre del usuario {user_id}")
        return user_id


@slack_app.event("message")
def handle_message_events(event, logger):
    if event.get("bot_id"):
        return
    thread_ts = event.get("thread_ts")
    channel = event.get("channel")
    if thread_ts in ca_ts or thread_ts in ca_ts_expirados:
        manejar_mensaje_ca(event, logger)
        return

    # Solo procesa respuestas dentro del hilo de una evaluacion enviada por el bot.
    if not thread_ts:
        if channel == config.CHANNEL_ID:
            with lock:
                debe_avisar = _debe_avisar_responder_en_hilo(channel, event.get("user"))
            if debe_avisar:
                slack_app.client.chat_postMessage(
                    channel=config.CHANNEL_ID,
                    text=(
                        "Muchas gracias por tu respuesta, pero por favor responde en el hilo de la notificacion "
                        "y no en el canal principal. Aqui solo mando yo notificaciones cuando toca evaluar. "
                        "No soy un bot inteligente: solo registro respuestas simples."
                    ),
                )
        return
    with lock:
        es_activo = thread_ts in evaluacion_ts
        es_expirado = thread_ts in evaluacion_ts_expirados
    if es_expirado and not es_activo:
        slack_app.client.chat_postMessage(
            channel=channel, thread_ts=thread_ts,
            text="⏰ Esta evaluación ha caducado porque ya hay una más reciente activa. Responde en el hilo nuevo.",
        )
        return
    if not es_activo:
        return
    clave_conv = (thread_ts, event.get("user"))

    user_id = event.get("user")
    texto = (event.get("text") or "").strip()

    if normalizar_nombre(texto) == "sos":
        with lock:
            conversaciones.pop(clave_conv, None)
        slack_app.client.chat_postMessage(
            channel=channel, thread_ts=thread_ts,
            text="Evaluación cancelada. Si quieres volver a empezar, envía otro mensaje en el hilo.",
        )
        return

    with lock:
        estado = conversaciones.get(clave_conv)
        if estado is None:
            estado = {
                "modo": "esperando_proyecto",
                "respuestas": {},
                "proyecto_actual": None,
                "evaluados_en_sesion": set(),
            }
            conversaciones[clave_conv] = estado

        modo = estado.get("modo")
        accion = None
        pregunta = None

        if modo == "esperando_proyecto":
            if texto:
                estado["respuestas"]["proyecto"] = texto
                estado["proyecto_actual"] = texto
                estado["modo"] = "esperando_persona"
                accion = "pedir_persona"
                pregunta = (
                    f"Perfecto, vamos con el proyecto *{texto}*. "
                    "Evalúa a los miembros de este proyecto. "
                    "Dime el nombre del miembro."
                )
            else:
                accion = "pedir_proyecto"
                pregunta = (
                    "¿En qué proyecto estás trabajando ahora? "
                    "Si estás en más de uno, elige solo uno y escribe el nombre del proyecto."
                )

        elif modo == "esperando_persona":
            if texto:
                if _parece_saludo(texto):
                    empleado, cargo_evaluado = None, None
                else:
                    empleado, cargo_evaluado = buscar_empleado_y_cargo(texto)
                if empleado:
                    clave_evaluacion = (normalizar_nombre(estado.get("proyecto_actual", "")), normalizar_nombre(empleado))
                    if clave_evaluacion in estado.get("evaluados_en_sesion", set()):
                        accion = "pedir_persona"
                        pregunta = (
                            f"Ya has evaluado a *{empleado}* en *{estado.get('proyecto_actual', '?')}* en este hilo. "
                            "Dime el nombre de otro miembro del proyecto."
                        )
                    else:
                        estado["respuestas"]["evaluado"] = empleado
                        if estado.get("cargo_evaluador") is None:
                            estado["cargo_evaluador"] = obtener_cargo_por_slack_id(user_id)
                        relacion = comparar_jerarquia(estado.get("cargo_evaluador") or "", cargo_evaluado or "")
                        estado["relacion_jerarquica"] = relacion
                        estado["sufijo_jerarquico"] = sufijo_preguntas(relacion)
                        estado["modo"] = "esperando_satisfaccion"
                        accion = "pedir_satisfaccion"
                        sufijo = estado["sufijo_jerarquico"]
                        pregunta = (
                            f"¿Cómo de satisfecho estás con *{empleado}* en *{estado['respuestas'].get('proyecto', '?')}*? "
                            f"Responde un número del 1 al 5.{sufijo}"
                        )
                elif _parece_saludo(texto):
                    accion = "pedir_persona"
                    pregunta = "Sigo aquí. Dime el nombre del miembro del proyecto."
                else:
                    accion = "pedir_persona_invalida"
                    pregunta = _mensaje_empleado_no_encontrado(texto)
            else:
                accion = "pedir_persona"
                pregunta = "¿Qué miembro del proyecto quieres evaluar?"

        elif modo == "esperando_satisfaccion":
            sufijo = estado.get("sufijo_jerarquico", "")
            if _es_valor_satisfaccion(texto):
                estado["respuestas"]["satisfaccion"] = texto
                estado["modo"] = "esperando_mejor"
                accion = "pedir_mejor"
                pregunta = f"¿Cuál es el mejor aspecto de esa persona?{sufijo}"
            else:
                accion = "pedir_satisfaccion"
                pregunta = f"Responde un número del 1 al 5 para la satisfacción.{sufijo}"

        elif modo == "esperando_mejor":
            sufijo = estado.get("sufijo_jerarquico", "")
            if texto:
                estado["respuestas"]["mejor_aspecto"] = texto
                estado["modo"] = "esperando_peor"
                accion = "pedir_peor"
                pregunta = f"¿Cuál es el peor aspecto de esa persona?{sufijo}"
            else:
                accion = "pedir_mejor"
                pregunta = f"¿Cuál es el mejor aspecto de esa persona?{sufijo}"

        elif modo == "esperando_peor":
            sufijo = estado.get("sufijo_jerarquico", "")
            if texto:
                estado["respuestas"]["peor_aspecto"] = texto
                estado["modo"] = "confirmacion"
                accion = "mostrar_resumen"
                pregunta = resumen_respuestas(estado["respuestas"])
            else:
                accion = "pedir_peor"
                pregunta = f"¿Cuál es el peor aspecto de esa persona?{sufijo}"

        elif modo == "confirmacion":
            if respuesta_es_confirmacion(texto):
                estado["modo"] = "guardar"
                accion = "guardar"
            elif respuesta_es_modificacion(texto):
                estado["modo"] = "seleccionando_modificacion"
                accion = "pedir_modificacion"
                pregunta = texto_menu_modificacion()
            elif _es_no(texto):
                estado["modo"] = "terminado"
                accion = "terminar"
            else:
                accion = "mostrar_resumen"
                pregunta = resumen_respuestas(estado["respuestas"])

        elif modo == "seleccionando_modificacion":
            clave = clave_modificacion(texto)
            if clave:
                estado["campo_modificando"] = clave
                estado["modo"] = "modificando_respuesta"
                accion = "pedir_valor_modificacion"
                pregunta = texto_pregunta_por_clave(clave, sufijo=estado.get("sufijo_jerarquico", ""))
            else:
                accion = "pedir_modificacion"
                pregunta = texto_menu_modificacion()

        elif modo == "modificando_respuesta":
            clave = estado.get("campo_modificando")
            if clave and texto:
                valor = texto
                if clave == "evaluado":
                    empleado, cargo_evaluado = buscar_empleado_y_cargo(texto)
                    if not empleado:
                        accion = "pedir_valor_modificacion"
                        pregunta = _mensaje_empleado_no_encontrado(texto)
                    else:
                        valor = empleado
                        if estado.get("cargo_evaluador") is None:
                            estado["cargo_evaluador"] = obtener_cargo_por_slack_id(user_id)
                        relacion = comparar_jerarquia(estado.get("cargo_evaluador") or "", cargo_evaluado or "")
                        estado["relacion_jerarquica"] = relacion
                        estado["sufijo_jerarquico"] = sufijo_preguntas(relacion)
                if accion != "pedir_valor_modificacion":
                    estado["respuestas"][clave] = valor
                    if clave == "proyecto":
                        estado["proyecto_actual"] = valor
                    estado.pop("campo_modificando", None)
                    estado["modo"] = "confirmacion"
                    accion = "mostrar_resumen"
                    pregunta = resumen_respuestas(estado["respuestas"])
            else:
                accion = "pedir_valor_modificacion"
                pregunta = texto_pregunta_por_clave(clave, sufijo=estado.get("sufijo_jerarquico", "")) if clave else texto_menu_modificacion()

        elif modo == "guardar":
            accion = "guardar"

        elif modo == "preguntar_mas_personas":
            if _es_si(texto):
                estado["modo"] = "esperando_persona"
                accion = "pedir_persona_mismo_proyecto"
                proyecto = estado.get("proyecto_actual") or ""
                pregunta = (
                    f"Perfecto. ¿Qué otro miembro del proyecto *{proyecto}* quieres evaluar?"
                    if proyecto
                    else "Perfecto. ¿Qué otro miembro quieres evaluar?"
                )
            elif _es_no(texto):
                estado["modo"] = "preguntar_mas_proyectos"
                accion = "pedir_mas_proyectos"
                pregunta = (
                    "Si hay más proyectos en los que estés trabajando, por favor, dímelo. "
                    "¿Hay más proyectos? (`sí` / `no`)"
                )
            else:
                accion = "pedir_mas_personas"
                pregunta = "Responde `sí` o `no` para indicar si hay más personas en este proyecto."

        elif modo == "preguntar_mas_proyectos":
            if _es_si(texto):
                estado["modo"] = "esperando_proyecto"
                estado["proyecto_actual"] = None
                accion = "pedir_proyecto"
                pregunta = (
                    "Perfecto. ¿En qué proyecto estás trabajando ahora? "
                    "Si estás en más de uno, elige solo uno y escribe el nombre del proyecto."
                )
            elif _es_no(texto):
                estado["modo"] = "terminado"
                accion = "terminar"
            else:
                accion = "pedir_mas_proyectos"
                pregunta = "Responde `sí` o `no` para indicar si hay más proyectos."

        elif modo == "terminado":
            accion = "ya_terminado"

    if accion == "pedir_persona":
        slack_app.client.chat_postMessage(
            channel=config.CHANNEL_ID,
            thread_ts=thread_ts,
            text=(pregunta if pregunta else "¿Qué miembro del proyecto quieres evaluar?")
        )
        return
    if accion == "pedir_persona_invalida":
        slack_app.client.chat_postMessage(channel=config.CHANNEL_ID, thread_ts=thread_ts, text=pregunta)
        return
    if accion == "pedir_persona_mismo_proyecto":
        slack_app.client.chat_postMessage(channel=config.CHANNEL_ID, thread_ts=thread_ts, text=pregunta)
        return
    if accion == "pedir_proyecto":
        slack_app.client.chat_postMessage(channel=config.CHANNEL_ID, thread_ts=thread_ts, text=pregunta)
        return
    if accion == "pedir_satisfaccion":
        slack_app.client.chat_postMessage(channel=config.CHANNEL_ID, thread_ts=thread_ts, text=pregunta)
        return
    if accion == "pedir_mejor":
        slack_app.client.chat_postMessage(channel=config.CHANNEL_ID, thread_ts=thread_ts, text=pregunta)
        return
    if accion == "pedir_peor":
        slack_app.client.chat_postMessage(channel=config.CHANNEL_ID, thread_ts=thread_ts, text=pregunta)
        return
    if accion == "pedir_modificacion":
        slack_app.client.chat_postMessage(channel=config.CHANNEL_ID, thread_ts=thread_ts, text=pregunta)
        return
    if accion == "pedir_valor_modificacion":
        slack_app.client.chat_postMessage(channel=config.CHANNEL_ID, thread_ts=thread_ts, text=pregunta)
        return
    if accion == "mostrar_resumen":
        slack_app.client.chat_postMessage(
            channel=config.CHANNEL_ID,
            thread_ts=thread_ts,
            text=(pregunta if isinstance(pregunta, str) else resumen_respuestas(estado["respuestas"])),
        )
        return
    if accion == "guardar":
        nombre = _nombre_real(user_id, logger)
        respuestas_finales = dict(estado["respuestas"])
        relacion = estado.get("relacion_jerarquica", "igual")
        guardado = guardar_en_notion(nombre, respuestas_finales, relacion=relacion)
        if guardado:
            with lock:
                clave_guardada = (normalizar_nombre(respuestas_finales.get("proyecto", "")), normalizar_nombre(respuestas_finales.get("evaluado", "")))
                estado.setdefault("evaluados_en_sesion", set()).add(clave_guardada)
                estado["modo"] = "preguntar_mas_personas"
            slack_app.client.chat_postMessage(
                channel=config.CHANNEL_ID,
                thread_ts=thread_ts,
                text=(
                    "✅ Evaluación guardada en Notion.\n\n"
                    "Si hay más miembros en este proyecto, por favor, dímelo. "
                    "¿Hay más miembros para evaluar aquí? (`sí` / `no`)"
                ),
            )
            return
        slack_app.client.chat_postMessage(
            channel=config.CHANNEL_ID,
            thread_ts=thread_ts,
            text="⚠️ No se pudo guardar en Notion. Revisa permisos/logs.",
        )
        return
    if accion == "pedir_mas_personas":
        slack_app.client.chat_postMessage(channel=config.CHANNEL_ID, thread_ts=thread_ts, text=pregunta)
        return
    if accion == "pedir_mas_proyectos":
        slack_app.client.chat_postMessage(channel=config.CHANNEL_ID, thread_ts=thread_ts, text=pregunta)
        return
    if accion == "terminar":
        slack_app.client.chat_postMessage(channel=config.CHANNEL_ID, thread_ts=thread_ts, text="Perfecto, gracias por tu tiempo. 👋")
        return
    if accion == "ya_terminado":
        slack_app.client.chat_postMessage(channel=config.CHANNEL_ID, thread_ts=thread_ts, text="Esta evaluación ya ha concluido. Puedes salir del hilo. 👋")
        return

    # fallback: keep the conversation alive with the current prompt
    if pregunta:
        slack_app.client.chat_postMessage(channel=config.CHANNEL_ID, thread_ts=thread_ts, text=pregunta)


_RECORDATORIO_PROYECTO_SEGUNDOS = 120


def ciclo_recordatorios_proyecto():
    while True:
        time.sleep(30)
        ahora = time.time()
        with lock:
            pendientes = [
                ts for ts in evaluacion_ts
                if ahora - max(evaluacion_hora.get(ts, ahora), evaluacion_ultimo_recordatorio.get(ts, 0) or evaluacion_hora.get(ts, ahora)) >= _RECORDATORIO_PROYECTO_SEGUNDOS
            ]
        for ts in pendientes:
            try:
                slack_app.client.chat_postMessage(
                    channel=config.CHANNEL_ID,
                    text="*⏰ Recuerda realizar tu evaluación de proyecto.* Si aún no has respondido, entra en el hilo de la notificación y hazlo.",
                )
                with lock:
                    evaluacion_ultimo_recordatorio[ts] = time.time()
            except Exception:
                logging.exception("Error enviando recordatorio de evaluación de proyecto")


def start_socket_mode():
    SocketModeHandler(slack_app, config.SLACK_APP_TOKEN).start()
