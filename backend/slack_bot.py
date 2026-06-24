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
    obtener_evaluados_middleoffice,
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
                        "📍 *Tienes una evaluación mensual pendiente.*\n\n"
                        "_Esta evaluación es totalmente privada, solo podrá verla el CA de la persona evaluada._\n"
                        "_Si en algún momento quieres cancelar, escribe SOS en el hilo._\n"
                        "*Envía cualquier mensaje en el hilo* para comenzar la evaluación"
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


def enviar_evaluaciones_modo_prueba():
    enviar_una_evaluacion()
    while True:
        time.sleep(config.INTERVALO_PRUEBA_DIAS * 24 * 60 * 60)
        enviar_una_evaluacion()


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
    lineas = ["Resumen de tus respuestas:"]
    lineas.append(f"- Persona evaluada: {respuestas.get('evaluado', '')}")
    if respuestas.get("proyecto"):
        lineas.append(f"- Proyecto: {respuestas.get('proyecto', '')}")
    if respuestas.get("satisfaccion"):
        lineas.append(f"- Satisfacción: {respuestas.get('satisfaccion', '')}")
    if preguntas_area:
        for q in preguntas_area:
            val = respuestas.get(q["clave"], "")
            label = q["texto"].split("\n")[0][:55]
            lineas.append(f"- {label}: {val}")
    return "\n".join(lineas) + _sufijo


def _texto_menu_modificacion_area(estado):
    preguntas_area = estado.get("preguntas_area", [])
    lineas = ["¿Qué respuesta quieres modificar?", "1. Persona evaluada", "2. Proyecto"]
    for i, q in enumerate(preguntas_area, start=3):
        lineas.append(f"{i}. {q['texto'].split(chr(10))[0][:55]}")
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




def texto_pregunta_por_clave(clave, preguntas=None):
    if preguntas and clave == "satisfaccion":
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


_Q5_EJEMPLO = "Indica un ejemplo concreto que justifique tu valoración"

_PALABRAS_NUMERO = {"uno": "1", "dos": "2", "tres": "3", "cuatro": "4", "cinco": "5"}


def _normalizar_valoracion(texto: str) -> str | None:
    """Devuelve '1'-'5' si el texto es un número válido (dígito o palabra), None si no."""
    t = texto.strip().lower()
    if t in {"1", "2", "3", "4", "5"}:
        return t
    return _PALABRAS_NUMERO.get(t)


def _pregunta_contribucion(relacion: str) -> str:
    if relacion == "inferior":
        rol = "Project Leader"
    elif relacion == "superior":
        rol = "miembro del equipo"
    else:
        rol = "tu compañero"
    return (
        f"Este mes, ¿cómo valorarías la contribución del {rol} al buen avance del proyecto? (número del 1 al 5)\n"
        "_Puedes considerar claridad, comunicación, prioridades, riesgos, equipo o cliente, "
        "pero no es necesario cubrir todos los aspectos._"
    )


def _preguntas_negocio(relacion: str, preguntas_notion: dict = None) -> list:
    pn = preguntas_notion or {}
    return [
        {"clave": "q1", "texto": pn.get("q1") or _pregunta_contribucion(relacion)},
        {"clave": "q2", "texto": pn.get("q2") or _Q5_EJEMPLO},
    ]


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
        slack_app.client.chat_postMessage(
            channel=channel,
            text="No debes escribir fuera de un hilo.",
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
    _mo_evaluados = []
    _mo_invalido = False

    _necesita_busqueda = (
        (_modo_peek == "esperando_persona" and texto and not _parece_saludo(texto))
        or (_modo_peek == "modificando_respuesta" and _campo_peek == "evaluado" and texto)
    )
    if _necesita_busqueda:
        try:
            if _area_peek == "middleoffice":
                _nombre_ev = obtener_nombre_por_id_usuario(user_id)
                _mo_evaluados = obtener_evaluados_middleoffice(_nombre_ev or "") if _nombre_ev else []
            _empleado_pre, _cargo_pre = buscar_empleado_y_cargo(texto)
            if _empleado_pre:
                if _area_peek == "middleoffice":
                    _preguntas_area_pre = obtener_preguntas_mo()
                    if _mo_evaluados and not any(
                        normalizar_nombre(_empleado_pre) == normalizar_nombre(e) for e in _mo_evaluados
                    ):
                        _mo_invalido = True
                        _empleado_pre = None
                        _cargo_pre = None
                elif _area_peek == "palantir":
                    if _cargo_ev_peek is None:
                        _cargo_evaluador_pre = obtener_cargo_por_slack_id(user_id)
                    _relacion_pre = comparar_jerarquia(_cargo_evaluador_pre or "", _cargo_pre or "")
                    _preguntas_area_pre = obtener_preguntas_palantir(tipo_relacion(_relacion_pre))
                else:
                    if _cargo_ev_peek is None:
                        _cargo_evaluador_pre = obtener_cargo_por_slack_id(user_id)
                    _relacion_pre = comparar_jerarquia(_cargo_evaluador_pre or "", _cargo_pre or "")
                    _preguntas_pre = obtener_preguntas_desde_notion(tipo_relacion(_relacion_pre))
            else:
                if _area_peek == "middleoffice":
                    _mo_invalido = True
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
                    accion = "pedir_persona_mo"
                else:
                    estado["modo"] = "esperando_proyecto"
                    accion = "pedir_proyecto"
                    pregunta = (
                        "Escribe el nombre de uno de los proyectos en los que estás trabajando. Más adelante podrás evaluar el resto"
                    )
            else:
                accion = "pedir_area"
                pregunta = (
                    "Por favor, escribe el número o el nombre del área al que perteneces 😊"
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
                    "Dime el nombre de uno de los miembros, podrás evaluar al resto después."
                )
            else:
                accion = "pedir_proyecto"
                pregunta = (
                    "¿En qué proyecto estás trabajando ahora? "
                    "Si estás en más de uno, elige solo uno y escribe el nombre, después podrás evaluar otros proyectos."
                )

        elif modo == "esperando_persona":
            if texto:
                if _parece_saludo(texto):
                    if estado.get("area") == "middleoffice":
                        accion = "pedir_persona_mo"
                    else:
                        accion = "pedir_persona"
                        pregunta = "Sigo aquí. Dime el nombre de uno de los miembros, podrás evaluar al resto después."
                elif _mo_invalido:
                    accion = "pedir_persona_mo"
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
                            preguntas = _preguntas_negocio(estado.get("relacion_jerarquica", "igual"), _preguntas_pre)
                            for _k in [k for k in estado["respuestas"] if k not in ("evaluado", "proyecto")]:
                                del estado["respuestas"][_k]
                            estado["preguntas_area"] = preguntas
                            estado["pregunta_actual"] = 0
                            estado["modo"] = "preguntando_area_secuencial"
                            accion = "preguntar"
                            pregunta = preguntas[0]["texto"]
                else:
                    accion = "pedir_persona_invalida"
                    pregunta = _invalido_pre
            else:
                if estado.get("area") == "middleoffice":
                    accion = "pedir_persona_mo"
                else:
                    accion = "pedir_persona"
                    pregunta = "¿Qué miembro del proyecto quieres evaluar?"

        elif modo == "preguntando_area_secuencial":
            todas = estado.get("preguntas_area", [])
            idx = estado.get("pregunta_actual", 0)
            if texto and todas and idx < len(todas):
                clave_actual = todas[idx]["clave"]
                valor_normalizado = _normalizar_valoracion(texto) if clave_actual in {"q1", "mo_contribucion"} else None
                if clave_actual in {"q1", "mo_contribucion"} and valor_normalizado is None:
                    accion = "preguntar"
                    pregunta = "Por favor, responde solo con un número del 1 al 5.\n" + todas[idx]["texto"]
                else:
                    estado["respuestas"][clave_actual] = valor_normalizado if valor_normalizado is not None else texto
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
            elif idx < len(todas):
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
                estado["modo"] = "seleccionando_modificacion_area"
                accion = "pedir_modificacion"
                pregunta = _texto_menu_modificacion_area(estado)
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
                pregunta = texto_pregunta_por_clave(campo) if campo else "Escribe la nueva respuesta."

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
                if campo in {"q1", "mo_contribucion"}:
                    valor_norm = _normalizar_valoracion(texto)
                    if valor_norm is None:
                        accion = "pedir_valor_modificacion"
                        todas = estado.get("preguntas_area", [])
                        pregunta_base = next((q["texto"] for q in todas if q["clave"] == campo), "")
                        pregunta = "Por favor, responde solo con un número del 1 al 5.\n" + pregunta_base
                    else:
                        estado["respuestas"][campo] = valor_norm
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
                pregunta = "Escribe la nueva respuesta."

        elif modo == "guardar":
            accion = "guardar"

        elif modo == "preguntar_mas_personas":
            _area_mp = estado.get("area", "negocio")
            if _es_si(texto):
                estado["modo"] = "esperando_persona"
                if _area_mp == "middleoffice":
                    accion = "pedir_persona_mo"
                else:
                    accion = "pedir_persona_mismo_proyecto"
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
                    "Perfecto. Escribe el nombre de uno de los proyectos en los que estás trabajando. Más adelante podrás evaluar el resto"
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
        "pedir_proyecto",
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
    if accion == "pedir_persona_mo":
        nombre_ev = obtener_nombre_por_id_usuario(user_id)
        mo_ev = obtener_evaluados_middleoffice(nombre_ev or user_id, [user_id])
        if mo_ev:
            lista = "\n".join(f"- {e}" for e in mo_ev)
            reply(f"¿A quién quieres evaluar?\n{lista}")
        else:
            reply("¿A quién quieres evaluar? Dime el nombre de la persona.")
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
                    text="*⏰ Recuerda realizar tu evaluación mensual.* Abre el hilo del mensaje de evaluación y responde.",
                )
                with lock:
                    evaluacion_ultimo_recordatorio[uid] = time.time()
            except Exception:
                logging.exception(f"Error enviando recordatorio DM a {uid}")


def start_socket_mode():
    SocketModeHandler(slack_app, config.SLACK_APP_TOKEN).start()
