import logging
import time
from datetime import datetime, timedelta, timezone

from slack_bolt.adapter.socket_mode import SocketModeHandler

from . import config
from .ca_reviews import ca_dm_activas, ca_dm_ts, manejar_mensaje_ca
from .personal_eval import (
    enviar_pregunta_inicial_personal,
    manejar_mensaje_personal,
    personal_dm_activas,
    personal_dm_ts,
)
from .clients import slack_app
from .hierarchy import comparar_jerarquia, tipo_relacion
from .notion_service import (
    buscar_empleado_y_cargo,
    guardar_en_notion,
    obtener_cargo_por_slack_id,
    obtener_config_calendario,
    obtener_nombre_por_id_usuario,
    obtener_preguntas_desde_notion,
    obtener_preguntas_mo,
    obtener_preguntas_palantir,
    obtener_slack_ids_empleados,
    siguiente_envio_calendario,
    sugerir_empleados_parecidos,
)
from .state import (
    conversaciones,
    evaluacion_dm_canal,
    evaluacion_dm_ts,
    evaluacion_hora,
    evaluacion_ultimo_recordatorio,
    evaluaciones_dm_activas,
    evaluaciones_dm_expiradas,
    lock,
)
from .utils import normalizar_nombre


def enviar_una_evaluacion():
    try:
        if config.APP_MODE != "produccion" and config.SLACK_TEST_USER_ID:
            slack_ids = [config.SLACK_TEST_USER_ID]
            logging.info(f"Modo prueba: enviando solo a {config.SLACK_TEST_USER_ID}")
        else:
            slack_ids = obtener_slack_ids_empleados()
            if not slack_ids:
                logging.warning("No se encontraron Slack IDs en la lista de empleados de Notion")
                return
        with lock:
            evaluaciones_dm_expiradas.update(evaluaciones_dm_activas)
            evaluaciones_dm_activas.clear()
        for user_id in slack_ids:
            try:
                resp_dm = slack_app.client.conversations_open(users=[user_id])
                dm_channel = resp_dm["channel"]["id"]
                resp = slack_app.client.chat_postMessage(
                    channel=dm_channel,
                    text=(
                        "📍 *Tienes una evaluación de proyecto pendiente.*\n"
                        "Responde en el hilo de este mensaje para comenzar.\n"
                        "_Si en algún momento quieres cancelar, escribe SOS en el hilo._"
                    ),
                )
                with lock:
                    evaluaciones_dm_activas.add(user_id)
                    evaluacion_dm_canal[user_id] = dm_channel
                    evaluacion_dm_ts[user_id] = resp["ts"]
                    evaluacion_hora[user_id] = time.time()
                    conversaciones.pop(user_id, None)
                logging.info(f"Evaluación enviada por DM a {user_id}, ts={resp['ts']}")
            except Exception as exc:
                if "user_not_found" in str(exc) or "channel_not_found" in str(exc):
                    logging.warning("Slack ID %s no encontrado en el workspace, omitiendo", user_id)
                else:
                    logging.exception("Error enviando DM de evaluación a %s", user_id)
    except Exception:
        logging.exception("Error en enviar_una_evaluacion")


def enviar_o_crear_revision(origen):
    enviar_una_evaluacion()
    enviar_pregunta_inicial_personal()


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
    # Producción: intervalo fijo de 4 semanas desde la fecha configurada en Notion
    while True:
        cal = obtener_config_calendario()
        fecha = cal.get("proyecto_ca")
        if not fecha:
            logging.info("[Proyecto] Sin 'Proyecto y CA' en Calendario evaluaciones de Notion. Reintentando en 1h.")
            time.sleep(3600)
            continue
        siguiente = siguiente_envio_calendario(fecha, 4)
        espera = max(60, (siguiente - datetime.now(timezone.utc)).total_seconds())
        logging.info(f"[Proyecto] Próximo envío: {siguiente.isoformat()} (en {espera/3600:.1f}h)")
        time.sleep(espera)
        enviar_una_evaluacion()


def resumen_respuestas(respuestas, area="negocio", preguntas_area=None):
    _sufijo = (
        "\n\n¿Estás satisfecho con tus respuestas?\n"
        "Responde `sí` para guardar en Notion o `modificar` para cambiar una respuesta concreta."
    )
    if area == "negocio" or not preguntas_area:
        return (
            "Resumen de tus respuestas:\n"
            f"- Persona evaluada: {respuestas.get('evaluado', '')}\n"
            f"- Proyecto: {respuestas.get('proyecto', '')}\n"
            f"- Satisfacción: {respuestas.get('satisfaccion', '')}\n"
            f"- Mejor aspecto: {respuestas.get('mejor_aspecto', '')}\n"
            f"- Peor aspecto: {respuestas.get('peor_aspecto', '')}"
            + _sufijo
        )
    lineas = [
        "Resumen de tus respuestas:",
        f"- Persona evaluada: {respuestas.get('evaluado', '')}",
        f"- Proyecto: {respuestas.get('proyecto', '')}",
    ]
    for q in preguntas_area:
        lineas.append(f"- {q['texto']}: {respuestas.get(q['clave'], '')}")
    return "\n".join(lineas) + _sufijo


def _texto_menu_modificacion_area(estado):
    preguntas_area = estado.get("preguntas_area", [])
    lineas = ["¿Qué respuesta quieres modificar?", "1. Persona evaluada", "2. Proyecto"]
    for i, q in enumerate(preguntas_area, start=3):
        lineas.append(f"{i}. {q['texto'][:60]}")
    lineas.append("\nResponde con el número.")
    return "\n".join(lineas)


def _clave_modificacion_area(texto, estado):
    preguntas_area = estado.get("preguntas_area", [])
    n = normalizar_nombre(texto)
    if n in {"1", "persona", "persona evaluada", "evaluado"}:
        return "evaluado"
    if n in {"2", "proyecto"}:
        return "proyecto"
    try:
        idx = int(texto) - 3
        if 0 <= idx < len(preguntas_area):
            return preguntas_area[idx]["clave"]
    except (ValueError, IndexError):
        pass
    return None


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


def texto_pregunta_por_clave(clave, preguntas=None):
    if preguntas and clave in ("satisfaccion", "mejor_aspecto", "peor_aspecto"):
        if preguntas.get(clave):
            return preguntas[clave]
    for pregunta in config.PREGUNTAS:
        if pregunta["clave"] == clave:
            return pregunta["texto"]
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

    channel = event.get("channel", "")
    thread_ts = event.get("thread_ts")
    user_id = event.get("user")

    if not channel.startswith("D"):
        return

    if not thread_ts:
        with lock:
            has_eval = user_id in evaluaciones_dm_activas
        has_ca = user_id in ca_dm_activas
        has_personal = user_id in personal_dm_activas
        if has_eval or has_ca or has_personal:
            slack_app.client.chat_postMessage(
                channel=channel,
                text="Por favor, responde en el hilo del mensaje de evaluación, no aquí directamente.",
            )
        return

    if thread_ts == ca_dm_ts.get(user_id):
        manejar_mensaje_ca(event, logger)
        return

    if thread_ts == personal_dm_ts.get(user_id):
        manejar_mensaje_personal(event, logger)
        return

    if thread_ts != evaluacion_dm_ts.get(user_id):
        # Si hay una evaluación activa en otro hilo, avisar que esta está caducada
        if evaluacion_dm_ts.get(user_id) is not None:
            with lock:
                es_activo = user_id in evaluaciones_dm_activas
            if es_activo:
                slack_app.client.chat_postMessage(
                    channel=channel,
                    thread_ts=thread_ts,
                    text="Evaluación caducada, por favor conteste en la última notificación.",
                )
        return

    with lock:
        es_activo = user_id in evaluaciones_dm_activas
    if not es_activo:
        return

    dm_channel = evaluacion_dm_canal.get(user_id, channel)
    texto = (event.get("text") or "").strip()

    def reply(text):
        slack_app.client.chat_postMessage(channel=dm_channel, thread_ts=thread_ts, text=text)

    if normalizar_nombre(texto) == "sos":
        with lock:
            conversaciones.pop(user_id, None)
        reply("Evaluación cancelada. Si quieres volver a empezar, escribe en este hilo.")
        return

    # Pre-fetch para esperando_persona: llamadas Notion pesadas FUERA del lock.
    # Para el resto de modos el lock se libera rápido (preguntas cacheadas tras primera llamada).
    with lock:
        _modo_peek = (conversaciones.get(user_id) or {}).get("modo", "pre_inicial")
        _campo_peek = (conversaciones.get(user_id) or {}).get("campo_modificando")
        _cargo_ev_peek = (conversaciones.get(user_id) or {}).get("cargo_evaluador")
        _relacion_peek = (conversaciones.get(user_id) or {}).get("relacion_jerarquica", "igual")
        _area_peek = (conversaciones.get(user_id) or {}).get("area", "negocio")

    _empleado_pre = None
    _cargo_pre = None
    _cargo_evaluador_pre = _cargo_ev_peek
    _relacion_pre = _relacion_peek
    _preguntas_pre = {}
    _preguntas_area_pre = []
    _invalido_pre = None

    _necesita_busqueda = (
        (_modo_peek == "esperando_persona" and texto and not _parece_saludo(texto))
        or (_modo_peek == "modificando_respuesta" and _campo_peek == "evaluado" and texto)
    )
    if _necesita_busqueda:
        try:
            _empleado_pre, _cargo_pre = buscar_empleado_y_cargo(texto)
            if _empleado_pre:
                if _area_peek == "middleoffice":
                    _preguntas_area_pre = obtener_preguntas_mo()
                else:
                    if _cargo_ev_peek is None:
                        _cargo_evaluador_pre = obtener_cargo_por_slack_id(user_id)
                    _relacion_pre = comparar_jerarquia(_cargo_evaluador_pre or "", _cargo_pre or "")
                    if _area_peek == "palantir":
                        _preguntas_area_pre = obtener_preguntas_palantir(tipo_relacion(_relacion_pre))
                    else:
                        _preguntas_pre = obtener_preguntas_desde_notion(tipo_relacion(_relacion_pre))
            else:
                _invalido_pre = _mensaje_empleado_no_encontrado(texto)
        except Exception:
            logger.exception("Error en Notion al buscar empleado")
            reply("⚠️ Error temporal consultando datos. Vuelve a intentarlo.")
            return

    # Máquina de estados en un único bloque con lock
    with lock:
        estado = conversaciones.get(user_id)
        if estado is None:
            estado = {
                "modo": "pre_inicial",
                "respuestas": {},
                "proyecto_actual": None,
                "evaluados_en_sesion": set(),
            }
            conversaciones[user_id] = estado

        modo = estado.get("modo")
        accion = None
        pregunta = None

        if modo == "pre_inicial":
            estado["modo"] = "esperando_area"
            accion = "pedir_area"
            pregunta = (
                "¿A qué área perteneces?\n"
                "*1.* Negocio\n"
                "*2.* MiddleOffice\n"
                "*3.* Palantir"
            )

        elif modo == "esperando_area":
            _AREA_MAP = {
                "1": "negocio", "negocio": "negocio",
                "2": "middleoffice", "middleoffice": "middleoffice",
                "middle office": "middleoffice", "middle": "middleoffice", "mo": "middleoffice",
                "3": "palantir", "palantir": "palantir",
            }
            _area_elegida = _AREA_MAP.get(normalizar_nombre(texto))
            if _area_elegida:
                estado["area"] = _area_elegida
                if _area_elegida == "middleoffice":
                    estado["respuestas"]["proyecto"] = ""
                    estado["modo"] = "esperando_persona"
                    accion = "pedir_persona"
                    pregunta = "¿A quién quieres evaluar? Dime el nombre de la persona."
                else:
                    estado["modo"] = "esperando_proyecto"
                    accion = "pedir_proyecto"
                    pregunta = (
                        "¿En qué proyecto estás trabajando ahora? "
                        "Si estás en más de uno, elige solo uno y escribe el nombre del proyecto."
                    )
            else:
                accion = "pedir_area"
                pregunta = (
                    "Por favor, elige tu área:\n"
                    "*1.* Negocio\n"
                    "*2.* MiddleOffice\n"
                    "*3.* Palantir"
                )

        elif modo == "esperando_proyecto":
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
                    accion = "pedir_persona"
                    pregunta = "Sigo aquí. Dime el nombre del miembro del proyecto."
                elif _empleado_pre:
                    proyecto_actual = estado.get("proyecto_actual", "")
                    clave_ev = (normalizar_nombre(proyecto_actual), normalizar_nombre(_empleado_pre))
                    if clave_ev in estado.get("evaluados_en_sesion", set()):
                        accion = "pedir_persona"
                        pregunta = (
                            f"Ya has evaluado a *{_empleado_pre}* en *{proyecto_actual or '?'}* en esta sesión. "
                            "Dime el nombre de otro miembro del proyecto."
                        )
                    else:
                        estado["respuestas"]["evaluado"] = _empleado_pre
                        if _cargo_evaluador_pre and _cargo_evaluador_pre != _cargo_ev_peek:
                            estado["cargo_evaluador"] = _cargo_evaluador_pre
                        estado["relacion_jerarquica"] = _relacion_pre
                        _area_actual = estado.get("area", "negocio")
                        if _area_actual in ("middleoffice", "palantir"):
                            for _k in [k for k in estado["respuestas"] if k not in ("evaluado", "proyecto")]:
                                del estado["respuestas"][_k]
                            estado["preguntas_area"] = _preguntas_area_pre
                            estado["pregunta_actual"] = 0
                            estado["modo"] = "preguntando_area_secuencial"
                            accion = "preguntar"
                            pregunta = (
                                _preguntas_area_pre[0]["texto"]
                                if _preguntas_area_pre
                                else "⚠️ No hay preguntas configuradas en Notion para esta área."
                            )
                        else:
                            estado["modo"] = "esperando_satisfaccion"
                            accion = "pedir_satisfaccion"
                            pregunta = _preguntas_pre.get(
                                "satisfaccion",
                                "¿Cómo de satisfecho estás con esa persona? (responde un número del 1 al 5)",
                            )
                else:
                    accion = "pedir_persona_invalida"
                    pregunta = _invalido_pre
            else:
                accion = "pedir_persona"
                pregunta = "¿Qué miembro del proyecto quieres evaluar?"

        elif modo == "esperando_satisfaccion":
            preguntas = obtener_preguntas_desde_notion(tipo_relacion(estado.get("relacion_jerarquica", "igual")))
            if _es_valor_satisfaccion(texto):
                estado["respuestas"]["satisfaccion"] = texto
                estado["modo"] = "esperando_mejor"
                accion = "pedir_mejor"
                pregunta = preguntas.get("mejor_aspecto", "¿Cuál es el mejor aspecto de esa persona?")
            else:
                accion = "pedir_satisfaccion"
                pregunta = "Responde un número del 1 al 5 para la satisfacción."

        elif modo == "esperando_mejor":
            preguntas = obtener_preguntas_desde_notion(tipo_relacion(estado.get("relacion_jerarquica", "igual")))
            if texto:
                estado["respuestas"]["mejor_aspecto"] = texto
                estado["modo"] = "esperando_peor"
                accion = "pedir_peor"
                pregunta = preguntas.get("peor_aspecto", "¿Cuál es el peor aspecto de esa persona?")
            else:
                accion = "pedir_mejor"
                pregunta = preguntas.get("mejor_aspecto", "¿Cuál es el mejor aspecto de esa persona?")

        elif modo == "esperando_peor":
            if texto:
                estado["respuestas"]["peor_aspecto"] = texto
                estado["modo"] = "confirmacion"
                accion = "mostrar_resumen"
                pregunta = resumen_respuestas(estado["respuestas"])
            else:
                preguntas = obtener_preguntas_desde_notion(tipo_relacion(estado.get("relacion_jerarquica", "igual")))
                accion = "pedir_peor"
                pregunta = preguntas.get("peor_aspecto", "¿Cuál es el peor aspecto de esa persona?")

        elif modo == "preguntando_area_secuencial":
            todas = estado.get("preguntas_area", [])
            idx = estado.get("pregunta_actual", 0)
            if texto and todas and idx < len(todas):
                estado["respuestas"][todas[idx]["clave"]] = texto
                idx += 1
                estado["pregunta_actual"] = idx
            if idx < len(todas):
                accion = "preguntar"
                pregunta = todas[idx]["texto"]
            else:
                estado["modo"] = "confirmacion"
                accion = "mostrar_resumen"
                pregunta = resumen_respuestas(
                    estado["respuestas"],
                    area=estado.get("area", "negocio"),
                    preguntas_area=todas,
                )

        elif modo == "confirmacion":
            if respuesta_es_confirmacion(texto):
                estado["modo"] = "guardar"
                accion = "guardar"
            elif respuesta_es_modificacion(texto):
                _area_conf = estado.get("area", "negocio")
                if _area_conf in ("middleoffice", "palantir"):
                    estado["modo"] = "seleccionando_modificacion_area"
                    accion = "pedir_modificacion"
                    pregunta = _texto_menu_modificacion_area(estado)
                else:
                    estado["modo"] = "seleccionando_modificacion"
                    accion = "pedir_modificacion"
                    pregunta = texto_menu_modificacion()
            elif _es_no(texto):
                estado["modo"] = "terminado"
                accion = "terminar"
            else:
                accion = "mostrar_resumen"
                pregunta = resumen_respuestas(
                    estado["respuestas"],
                    area=estado.get("area", "negocio"),
                    preguntas_area=estado.get("preguntas_area"),
                )

        elif modo == "seleccionando_modificacion":
            campo = clave_modificacion(texto)
            if campo:
                estado["campo_modificando"] = campo
                estado["modo"] = "modificando_respuesta"
                accion = "pedir_valor_modificacion"
                if campo in ("satisfaccion", "mejor_aspecto", "peor_aspecto"):
                    preguntas = obtener_preguntas_desde_notion(tipo_relacion(estado.get("relacion_jerarquica", "igual")))
                    pregunta = preguntas.get(campo) or texto_pregunta_por_clave(campo)
                else:
                    pregunta = texto_pregunta_por_clave(campo)
            else:
                accion = "pedir_modificacion"
                pregunta = texto_menu_modificacion()

        elif modo == "modificando_respuesta":
            campo = estado.get("campo_modificando")
            if campo and texto:
                if campo == "evaluado":
                    if not _empleado_pre:
                        accion = "pedir_valor_modificacion"
                        pregunta = _invalido_pre
                    else:
                        if _cargo_evaluador_pre and _cargo_evaluador_pre != _cargo_ev_peek:
                            estado["cargo_evaluador"] = _cargo_evaluador_pre
                        estado["relacion_jerarquica"] = _relacion_pre
                        estado["respuestas"]["evaluado"] = _empleado_pre
                        estado.pop("campo_modificando", None)
                        estado["modo"] = "confirmacion"
                        accion = "mostrar_resumen"
                        pregunta = resumen_respuestas(
                            estado["respuestas"],
                            area=estado.get("area", "negocio"),
                            preguntas_area=estado.get("preguntas_area"),
                        )
                else:
                    estado["respuestas"][campo] = texto
                    if campo == "proyecto":
                        estado["proyecto_actual"] = texto
                    estado.pop("campo_modificando", None)
                    estado["modo"] = "confirmacion"
                    accion = "mostrar_resumen"
                    pregunta = resumen_respuestas(
                        estado["respuestas"],
                        area=estado.get("area", "negocio"),
                        preguntas_area=estado.get("preguntas_area"),
                    )
            else:
                accion = "pedir_valor_modificacion"
                pregunta = texto_pregunta_por_clave(campo) if campo else texto_menu_modificacion()

        elif modo == "seleccionando_modificacion_area":
            campo = _clave_modificacion_area(texto, estado)
            if campo:
                estado["campo_modificando"] = campo
                if campo == "evaluado":
                    estado["modo"] = "modificando_respuesta"
                    accion = "pedir_valor_modificacion"
                    pregunta = "Indica el nombre de la persona a evaluar."
                elif campo == "proyecto":
                    estado["modo"] = "modificando_respuesta_area"
                    accion = "pedir_valor_modificacion"
                    pregunta = "Escribe el nuevo nombre del proyecto."
                else:
                    todas = estado.get("preguntas_area", [])
                    pregunta = next((q["texto"] for q in todas if q["clave"] == campo), "Escribe la nueva respuesta.")
                    estado["modo"] = "modificando_respuesta_area"
                    accion = "pedir_valor_modificacion"
            else:
                accion = "pedir_modificacion"
                pregunta = _texto_menu_modificacion_area(estado)

        elif modo == "modificando_respuesta_area":
            campo = estado.get("campo_modificando")
            if campo and texto:
                estado["respuestas"][campo] = texto
                if campo == "proyecto":
                    estado["proyecto_actual"] = texto
                estado.pop("campo_modificando", None)
                estado["modo"] = "confirmacion"
                accion = "mostrar_resumen"
                pregunta = resumen_respuestas(
                    estado["respuestas"],
                    area=estado.get("area", "negocio"),
                    preguntas_area=estado.get("preguntas_area"),
                )
            else:
                accion = "pedir_valor_modificacion"
                pregunta = "Escribe la nueva respuesta."

        elif modo == "guardar":
            accion = "guardar"

        elif modo == "preguntar_mas_personas":
            _area_mp = estado.get("area", "negocio")
            if _es_si(texto):
                estado["modo"] = "esperando_persona"
                accion = "pedir_persona_mismo_proyecto"
                if _area_mp == "middleoffice":
                    pregunta = "Perfecto. ¿A quién más quieres evaluar? Dime el nombre."
                else:
                    proyecto = estado.get("proyecto_actual") or ""
                    pregunta = (
                        f"Perfecto. ¿Qué otro miembro del proyecto *{proyecto}* quieres evaluar?"
                        if proyecto
                        else "Perfecto. ¿Qué otro miembro quieres evaluar?"
                    )
            elif _es_no(texto):
                if _area_mp == "middleoffice":
                    estado["modo"] = "terminado"
                    accion = "terminar"
                else:
                    estado["modo"] = "preguntar_mas_proyectos"
                    accion = "pedir_mas_proyectos"
                    pregunta = (
                        "Si hay más proyectos en los que estés trabajando, por favor, dímelo. "
                        "¿Hay más proyectos? (`sí` / `no`)"
                    )
            else:
                accion = "pedir_mas_personas"
                pregunta = "Responde `sí` o `no` para indicar si hay más personas que evaluar."

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

    # Despacho de acciones — fuera del lock
    _ACCIONES_PREGUNTA = {
        "pedir_area", "preguntar",
        "pedir_persona", "pedir_persona_invalida", "pedir_persona_mismo_proyecto",
        "pedir_proyecto", "pedir_satisfaccion", "pedir_mejor", "pedir_peor",
        "pedir_modificacion", "pedir_valor_modificacion", "pedir_mas_personas",
        "pedir_mas_proyectos",
    }
    if accion in _ACCIONES_PREGUNTA:
        reply(pregunta if pregunta else "")
        return
    if accion == "mostrar_resumen":
        reply(pregunta)
        return
    if accion == "guardar":
        nombre = _nombre_real(user_id, logger)
        with lock:
            respuestas_finales = dict(estado.get("respuestas", {}))
            relacion_final = estado.get("relacion_jerarquica", "igual")
            _AREA_DISPLAY = {"negocio": "Negocio", "middleoffice": "MiddleOffice", "palantir": "Palantir"}
            area_final = _AREA_DISPLAY.get(estado.get("area", "negocio"), "Negocio")
        guardado = guardar_en_notion(nombre, respuestas_finales, relacion=relacion_final, area=area_final)
        if guardado:
            with lock:
                clave_guardada = (
                    normalizar_nombre(respuestas_finales.get("proyecto", "")),
                    normalizar_nombre(respuestas_finales.get("evaluado", "")),
                )
                estado.setdefault("evaluados_en_sesion", set()).add(clave_guardada)
                estado["modo"] = "preguntar_mas_personas"
            reply(
                "✅ Evaluación guardada en Notion.\n\n"
                "Si hay más miembros en este proyecto, por favor, dímelo. "
                "¿Hay más miembros para evaluar aquí? (`sí` / `no`)"
            )
            return
        reply("⚠️ No se pudo guardar en Notion. Revisa permisos/logs.")
        return
    if accion == "terminar":
        reply("Perfecto, gracias por tu tiempo. 👋")
        return
    if accion == "ya_terminado":
        reply("Esta evaluación ya ha concluido, por favor salga del hilo. 👋")
        return
    if pregunta:
        reply(pregunta)


_RECORDATORIO_PROYECTO_SEGUNDOS = 7 * 24 * 60 * 60  # 1 semana


def ciclo_recordatorios_proyecto():
    while True:
        time.sleep(30)
        ahora = time.time()
        with lock:
            pendientes = [
                uid for uid in evaluaciones_dm_activas
                if (
                    ahora - max(evaluacion_hora.get(uid, ahora), evaluacion_ultimo_recordatorio.get(uid, 0) or evaluacion_hora.get(uid, ahora)) >= _RECORDATORIO_PROYECTO_SEGUNDOS
                    and conversaciones.get(uid, {}).get("modo") not in ("terminado",)
                )
            ]
        for uid in pendientes:
            try:
                dm_channel = evaluacion_dm_canal.get(uid)
                if not dm_channel:
                    continue
                slack_app.client.chat_postMessage(
                    channel=dm_channel,
                    text="*⏰ Recuerda realizar tu evaluación de proyecto.* Abre el hilo del mensaje de evaluación y responde.",
                )
                with lock:
                    evaluacion_ultimo_recordatorio[uid] = time.time()
            except Exception:
                logging.exception(f"Error enviando recordatorio DM a {uid}")


def start_socket_mode():
    SocketModeHandler(slack_app, config.SLACK_APP_TOKEN).start()
