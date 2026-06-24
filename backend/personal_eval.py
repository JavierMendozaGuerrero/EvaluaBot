import logging
import threading
import time
from datetime import datetime, timezone

from . import config
from .clients import slack_app
from .notion_service import (
    guardar_evaluacion_personal,
    obtener_config_calendario,
    obtener_nombre_por_id_usuario,
    obtener_objetivos,
    obtener_preguntas_personales,
    obtener_slack_ids_empleados,
    siguiente_envio_calendario,
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

    texto_norm = normalizar_nombre(texto)

    with _lock:
        estado = conversaciones_personal.get(user_id)
        if estado is None:
            estado = {"modo": "pre_inicial", "respuestas": {}}
            conversaciones_personal[user_id] = estado

        modo = estado.get("modo")
        accion = None
        pregunta = None

        if modo == "pre_inicial":
            estado["modo"] = "esperando_comentario"
            accion = "preguntar"
            pregunta = "Ya puedes responder."

        elif modo == "esperando_comentario":
            if texto:
                estado["respuestas"]["comentario"] = texto
                estado["modo"] = "confirmacion"
                accion = "mostrar_resumen"
                pregunta = (
                    f"📋 Tu comentario:\n_{texto}_\n\n"
                    "¿Lo guardo? Responde *sí* para guardar o *modificar* para cambiar."
                )
            else:
                accion = "preguntar"
                pregunta = "Ya puedes responder."

        elif modo == "confirmacion":
            if texto_norm in {"si", "sí", "s", "ok", "okay", "confirmar", "guardar", "correcto"}:
                estado["modo"] = "guardar"
                accion = "guardar"
                respuestas_snap = dict(estado["respuestas"])
            elif texto_norm in {"modificar", "cambiar", "editar"}:
                estado["modo"] = "pre_inicial"
                estado["respuestas"].pop("comentario", None)
                accion = "preguntar"
                pregunta = "Escribe de nuevo tu comentario:"
            else:
                accion = "mostrar_resumen"
                pregunta = (
                    f"📋 Tu comentario:\n_{estado['respuestas'].get('comentario', '')}_\n\n"
                    "¿Lo guardo? Responde *sí* para guardar o *modificar* para cambiar."
                )

        elif modo == "guardar":
            accion = "guardar"
            respuestas_snap = dict(estado["respuestas"])

        elif modo == "preguntando_otro":
            if texto_norm in {"si", "sí", "s", "ok", "okay"}:
                estado["respuestas"] = {}
                estado["modo"] = "pre_inicial"
                accion = "preguntar"
                pregunta = "¿Qué más me quieres contar? Responde con tu comentario."
            elif texto_norm in {"no", "n", "cancelar"}:
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

    if accion == "ya_terminado":
        reply("Evaluación finalizada, por favor salga del hilo. 👋")
        return


def ciclo_envio_personal() -> None:
    """Envía evaluaciones personales: en prueba una vez al mes, en producción cada 2 semanas desde la fecha de Notion."""
    if config.APP_MODE != "produccion":
        try:
            enviar_pregunta_inicial_personal()
        except Exception:
            logging.exception("Error en ciclo personal prueba")
        while True:
            time.sleep(config.INTERVALO_PRUEBA_DIAS * 24 * 60 * 60)
            try:
                enviar_pregunta_inicial_personal()
            except Exception:
                logging.exception("Error en ciclo personal prueba")
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
