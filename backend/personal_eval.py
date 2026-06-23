import logging
import threading
import time
from datetime import datetime, timezone

from . import config
from .clients import slack_app
from .notion_service import (
    buscar_empleado_en_lista,
    guardar_evaluacion_personal,
    obtener_config_calendario,
    obtener_nombre_por_id_usuario,
    obtener_objetivos,
    obtener_preguntas_personales,
    obtener_slack_ids_empleados,
    siguiente_envio_calendario,
    sugerir_empleados_parecidos,
    PREGUNTAS_PERSONALES_DEFAULT,
)
from .utils import normalizar_nombre

_lock = threading.Lock()

personal_dm_activas: set = set()
personal_dm_ts: dict = {}
personal_dm_canal: dict = {}
personal_hora: dict = {}
personal_ultimo_recordatorio: dict = {}
conversaciones_personal: dict = {}

_RECORDATORIO_SEGUNDOS = 7 * 24 * 60 * 60  # 1 semana

_OPCIONES_MOD_PERSONAL = {
    "1": "proyecto", "proyecto": "proyecto",
    "2": "personas", "personas": "personas", "personas implicadas": "personas",
    "3": "comentario", "comentario": "comentario",
}


def _clave_mod_personal(texto):
    return _OPCIONES_MOD_PERSONAL.get(normalizar_nombre(texto))


def _texto_menu_mod_personal():
    return (
        "¿Qué quieres modificar?\n"
        "1. Proyecto\n"
        "2. Personas implicadas\n"
        "3. Comentario\n\n"
        "Responde con el número o el nombre del campo."
    )


def _texto_resumen(r):
    personas = r.get("personas") or "Ninguna"
    return (
        "📋 *Resumen de tu evaluación personal:*\n\n"
        f"• *Proyecto:* {r.get('proyecto', '')}\n"
        f"• *Personas implicadas:* {personas}\n"
        f"• *Comentario:* {r.get('comentario', '')}\n\n"
        "¿Es correcto? Responde *sí* para guardar o *modificar* para cambiar algún campo."
    )


def enviar_pregunta_inicial_personal() -> None:
    try:
        if config.APP_MODE != "produccion" and config.SLACK_TEST_USER_ID:
            slack_ids = [config.SLACK_TEST_USER_ID]
        else:
            slack_ids = obtener_slack_ids_empleados()
            if not slack_ids:
                logging.warning("No se encontraron Slack IDs para evaluación personal")
                return

        try:
            pq = obtener_preguntas_personales()
            mensaje_inicial = pq.get("mensaje_inicial", PREGUNTAS_PERSONALES_DEFAULT["mensaje_inicial"])
        except Exception:
            mensaje_inicial = PREGUNTAS_PERSONALES_DEFAULT["mensaje_inicial"]

        with _lock:
            personal_dm_activas.clear()

        for user_id in slack_ids:
            try:
                resp_dm = slack_app.client.conversations_open(users=[user_id])
                dm_channel = resp_dm["channel"]["id"]
                resp = slack_app.client.chat_postMessage(
                    channel=dm_channel,
                    text=mensaje_inicial,
                )
                msg_ts = resp["ts"]
                with _lock:
                    personal_dm_activas.add(user_id)
                    personal_dm_ts[user_id] = msg_ts
                    personal_dm_canal[user_id] = dm_channel
                    personal_hora[user_id] = time.time()
                    conversaciones_personal.pop(user_id, None)
                logging.info("Evaluación personal enviada a %s", user_id)

                try:
                    nombre = obtener_nombre_por_id_usuario(user_id)
                    if not nombre:
                        resp_u = slack_app.client.users_info(user=user_id)
                        u = resp_u.get("user", {})
                        p = u.get("profile", {})
                        nombre = u.get("real_name") or p.get("real_name") or p.get("display_name") or u.get("name") or ""
                    objetivos = obtener_objetivos(nombre) if nombre else []
                    if objetivos:
                        texto_obj = objetivos[0]["objetivos"]
                        msg_obj = f"📌 Como recordatorio, tus objetivos son:\n\n{texto_obj}"
                    else:
                        msg_obj = "📌 Como recordatorio: no tienes objetivos registrados."
                    slack_app.client.chat_postMessage(
                        channel=dm_channel,
                        thread_ts=msg_ts,
                        text=msg_obj,
                    )
                except Exception:
                    logging.exception("Error enviando objetivos en hilo personal a %s", user_id)
            except Exception as exc:
                err_str = str(exc)
                if "user_not_found" in err_str or "channel_not_found" in err_str:
                    logging.warning("Slack ID %s no encontrado en el workspace, omitiendo", user_id)
                else:
                    logging.exception("Error enviando evaluación personal a %s", user_id)
    except Exception:
        logging.exception("Error en enviar_pregunta_inicial_personal")


def manejar_mensaje_personal(event, logger) -> None:
    user_id = event.get("user")
    channel = event.get("channel", "")
    thread_ts = event.get("thread_ts")
    texto = (event.get("text") or "").strip()

    with _lock:
        es_activo = user_id in personal_dm_activas
        modo_terminado = conversaciones_personal.get(user_id, {}).get("modo") == "terminado"

    dm_channel = personal_dm_canal.get(user_id, channel)

    def reply(text):
        slack_app.client.chat_postMessage(channel=dm_channel, thread_ts=thread_ts, text=text)

    if modo_terminado:
        reply("Evaluación finalizada, por favor salga del hilo. 👋")
        return

    if not es_activo:
        return

    if normalizar_nombre(texto) == "sos":
        with _lock:
            conversaciones_personal.pop(user_id, None)
            personal_dm_activas.discard(user_id)
        reply("Evaluación cancelada. Si quieres volver a empezar, escribe en este hilo.")
        return

    # Fetch preguntas de Notion (cacheadas 5 min) y búsqueda de empleado — ambas FUERA del lock
    try:
        _pq = obtener_preguntas_personales()
    except Exception:
        _pq = {}
    _Q_PROYECTO = _pq.get("proyecto", PREGUNTAS_PERSONALES_DEFAULT["proyecto"])
    _Q_PERSONAS = _pq.get("personas", PREGUNTAS_PERSONALES_DEFAULT["personas"])
    _Q_COMENTARIO = _pq.get("comentario", PREGUNTAS_PERSONALES_DEFAULT["comentario"])

    with _lock:
        modo_peek = (conversaciones_personal.get(user_id) or {}).get("modo", "pre_inicial")

    _empleado_pre = None
    _invalido_pre = None

    _buscar_persona = modo_peek in ("esperando_personas_inicial", "esperando_mas_personas")
    _texto_norm = normalizar_nombre(texto)
    _es_negacion = _texto_norm in {"ninguna", "no", "n", "nadie"}

    if _buscar_persona and texto and not _es_negacion:
        _empleado_pre = buscar_empleado_en_lista(texto)
        if not _empleado_pre:
            sugerencias = sugerir_empleados_parecidos(texto)
            if sugerencias:
                opciones = "\n".join(f"• {n}" for n in sugerencias)
                _invalido_pre = (
                    f"*{texto}* no aparece tal cual en la lista. ¿Querías decir alguno de estos?\n"
                    f"{opciones}\n\nCopia el nombre exacto o escribe *no* para continuar sin añadir más personas."
                )
            else:
                _invalido_pre = (
                    f"*{texto}* no aparece en la lista de empleados. "
                    "Escribe el nombre exacto o *no* para continuar."
                )

    with _lock:
        estado = conversaciones_personal.get(user_id)
        if estado is None:
            estado = {"modo": "pre_inicial", "respuestas": {}, "personas_lista": []}
            conversaciones_personal[user_id] = estado

        modo = estado.get("modo")
        accion = None
        pregunta = None

        if modo == "pre_inicial":
            estado["modo"] = "esperando_proyecto"
            accion = "preguntar"
            pregunta = _Q_PROYECTO

        elif modo == "esperando_proyecto":
            if texto:
                _sin_proyecto = _texto_norm in {"ninguno", "ninguna", "no", "n", "sin proyecto", "no hay proyecto", "ningún proyecto"}
                estado["respuestas"]["proyecto"] = "Sin proyecto" if _sin_proyecto else texto
                estado["modo"] = "esperando_personas_inicial"
                accion = "preguntar"
                pregunta = _Q_PERSONAS
            else:
                accion = "preguntar"
                pregunta = _Q_PROYECTO

        elif modo == "esperando_personas_inicial":
            if _es_negacion:
                estado["respuestas"]["personas"] = ""
                if estado.pop("_retornar_a_confirmacion", False):
                    estado["modo"] = "confirmacion"
                    accion = "mostrar_resumen"
                    pregunta = _texto_resumen(estado["respuestas"])
                else:
                    estado["modo"] = "esperando_comentario"
                    accion = "preguntar"
                    pregunta = _Q_COMENTARIO
            elif _empleado_pre:
                estado["personas_lista"].append(_empleado_pre)
                estado["modo"] = "esperando_mas_personas"
                accion = "preguntar"
                pregunta = f"✓ *{_empleado_pre}* añadido/a. ¿Hay alguien más? Escribe otro nombre o *no* para continuar."
            elif _invalido_pre:
                accion = "preguntar"
                pregunta = _invalido_pre
            else:
                accion = "preguntar"
                pregunta = _Q_PERSONAS

        elif modo == "esperando_mas_personas":
            if _es_negacion:
                estado["respuestas"]["personas"] = ", ".join(estado.get("personas_lista", []))
                if estado.pop("_retornar_a_confirmacion", False):
                    estado["modo"] = "confirmacion"
                    accion = "mostrar_resumen"
                    pregunta = _texto_resumen(estado["respuestas"])
                else:
                    estado["modo"] = "esperando_comentario"
                    accion = "preguntar"
                    pregunta = _Q_COMENTARIO
            elif _empleado_pre:
                estado["personas_lista"].append(_empleado_pre)
                accion = "preguntar"
                pregunta = f"✓ *{_empleado_pre}* añadido/a. ¿Hay alguien más? Escribe otro nombre o *no* para continuar."
            elif _invalido_pre:
                accion = "preguntar"
                pregunta = _invalido_pre
            else:
                accion = "preguntar"
                pregunta = "¿Hay alguien más? Escribe otro nombre o *no* para continuar."

        elif modo == "esperando_comentario":
            if texto:
                estado["respuestas"]["comentario"] = texto
                estado["modo"] = "confirmacion"
                accion = "mostrar_resumen"
                pregunta = _texto_resumen(estado["respuestas"])
            else:
                accion = "preguntar"
                pregunta = _Q_COMENTARIO

        elif modo == "confirmacion":
            if _texto_norm in {"si", "sí", "s", "ok", "okay", "confirmar", "guardar", "correcto"}:
                estado["modo"] = "guardar"
                accion = "guardar"
                respuestas_snap = dict(estado["respuestas"])
            elif _texto_norm in {"modificar", "cambiar", "editar"}:
                estado["modo"] = "seleccionando_modificacion_personal"
                accion = "preguntar"
                pregunta = _texto_menu_mod_personal()
            else:
                accion = "mostrar_resumen"
                pregunta = _texto_resumen(estado["respuestas"])

        elif modo == "seleccionando_modificacion_personal":
            campo = _clave_mod_personal(texto)
            if campo == "proyecto":
                estado["campo_modificando"] = "proyecto"
                estado["modo"] = "modificando_personal"
                accion = "preguntar"
                pregunta = _Q_PROYECTO
            elif campo == "personas":
                estado["personas_lista"] = []
                estado["respuestas"].pop("personas", None)
                estado["_retornar_a_confirmacion"] = True
                estado["modo"] = "esperando_personas_inicial"
                accion = "preguntar"
                pregunta = _Q_PERSONAS
            elif campo == "comentario":
                estado["campo_modificando"] = "comentario"
                estado["modo"] = "modificando_personal"
                accion = "preguntar"
                pregunta = _Q_COMENTARIO
            else:
                accion = "preguntar"
                pregunta = _texto_menu_mod_personal()

        elif modo == "modificando_personal":
            campo = estado.get("campo_modificando")
            if campo and texto:
                if campo == "proyecto":
                    _sin = _texto_norm in {"ninguno", "ninguna", "no", "n", "sin proyecto", "no hay proyecto", "ningún proyecto"}
                    estado["respuestas"]["proyecto"] = "Sin proyecto" if _sin else texto
                else:
                    estado["respuestas"][campo] = texto
                estado.pop("campo_modificando", None)
                estado["modo"] = "confirmacion"
                accion = "mostrar_resumen"
                pregunta = _texto_resumen(estado["respuestas"])
            else:
                accion = "preguntar"
                pregunta = _Q_PROYECTO if campo == "proyecto" else _Q_COMENTARIO

        elif modo == "guardar":
            accion = "guardar"
            respuestas_snap = dict(estado["respuestas"])

        elif modo == "preguntando_otro":
            if _texto_norm in {"si", "sí", "s", "ok", "okay"}:
                estado["respuestas"] = {}
                estado["personas_lista"] = []
                estado["modo"] = "esperando_proyecto"
                accion = "preguntar"
                pregunta = _Q_PROYECTO
            elif _texto_norm in {"no", "n", "cancelar"}:
                estado["modo"] = "terminado"
                personal_dm_activas.discard(user_id)
                accion = "ya_terminado"
            else:
                accion = "preguntar"
                pregunta = "¿Quieres añadir otro comentario? Responde *sí* para continuar o *no* para finalizar."

        elif modo == "terminado":
            accion = "ya_terminado"

    if accion in ("preguntar", "mostrar_resumen"):
        reply(pregunta)
        return

    if accion == "guardar":
        nombre = obtener_nombre_por_id_usuario(user_id)
        if not nombre:
            try:
                resp = slack_app.client.users_info(user=user_id)
                u = resp.get("user", {})
                p = u.get("profile", {})
                nombre = (
                    u.get("real_name")
                    or p.get("real_name")
                    or p.get("display_name")
                    or u.get("name")
                    or user_id
                )
            except Exception:
                nombre = user_id

        guardado = guardar_evaluacion_personal(nombre, respuestas_snap)
        if guardado:
            with _lock:
                if conversaciones_personal.get(user_id, {}).get("modo") == "guardar":
                    conversaciones_personal[user_id]["modo"] = "preguntando_otro"
            reply("✅ Evaluación guardada. ¿Quieres añadir otro comentario? Responde *sí* para continuar o *no* para finalizar.")
        else:
            reply("⚠️ No se pudo guardar en Notion. Revisa los permisos o contacta con soporte.")
        return

    if accion == "terminar":
        reply("Evaluación cancelada. ¡Hasta la próxima! 👋")
        return

    if accion == "ya_terminado":
        reply("Evaluación finalizada, por favor salga del hilo. 👋")
        return


def ciclo_envio_personal() -> None:
    """Solo activo en producción: envía evaluaciones personales cada 2 semanas desde la fecha de Notion."""
    if config.APP_MODE != "produccion":
        return
    while True:
        cal = obtener_config_calendario()
        fecha = cal.get("personal")
        if not fecha:
            logging.info("[Personal] Sin 'Personal' en Calendario evaluaciones de Notion. Reintentando en 1h.")
            time.sleep(3600)
            continue
        siguiente = siguiente_envio_calendario(fecha, 2)
        espera = max(60, (siguiente - datetime.now(timezone.utc)).total_seconds())
        logging.info(f"[Personal] Próximo envío: {siguiente.isoformat()} (en {espera/3600:.1f}h)")
        time.sleep(espera)
        try:
            enviar_pregunta_inicial_personal()
        except Exception:
            logging.exception("Error en ciclo personal producción")


def ciclo_recordatorios_personal() -> None:
    while True:
        time.sleep(30)
        ahora = time.time()
        with _lock:
            pendientes = [
                uid for uid in list(personal_dm_activas)
                if (
                    conversaciones_personal.get(uid, {}).get("modo") not in ("terminado", "preguntando_otro")
                    and (ahora - max(
                        personal_hora.get(uid, ahora),
                        personal_ultimo_recordatorio.get(uid, 0) or personal_hora.get(uid, ahora),
                    )) >= _RECORDATORIO_SEGUNDOS
                )
            ]
        for uid in pendientes:
            try:
                dm_channel = personal_dm_canal.get(uid)
                ts = personal_dm_ts.get(uid)
                if not dm_channel or not ts:
                    continue
                slack_app.client.chat_postMessage(
                    channel=dm_channel,
                    thread_ts=ts,
                    text="⏰ Recuerda que tienes una evaluación personal pendiente. Responde en este hilo cuando puedas.",
                )
                with _lock:
                    personal_ultimo_recordatorio[uid] = ahora
            except Exception:
                logging.exception("Error enviando recordatorio personal a %s", uid)
