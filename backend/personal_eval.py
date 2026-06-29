import json
import logging
import threading
import time
from datetime import datetime, timezone

from . import config
from .clients import slack_app
from .notion_service import (
    evaluacion_personal_guardada_desde,
    guardar_evaluacion_personal,
    obtener_ca_de_empleado,
    obtener_config_calendario,
    obtener_criterios_evaluacion,
    obtener_nombre_por_id_usuario,
    obtener_objetivos,
    obtener_slack_id_por_nombre,
    obtener_slack_ids_empleados,
    siguiente_envio_calendario,
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


_BLOQUES_OPORTUNIDAD_SIN_URGENCIA = [
    {
        "type": "section",
        "text": {"type": "mrkdwn", "text": "*Esta es tu oportunidad para:*"},
    },
    {
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": '*1.* Explicar cómo estás ayudando en _"Contribution to the firm"_',
        },
    },
    {
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": "*2.* Cómo te estás acercando a tus objetivos",
        },
        "accessory": {
            "type": "button",
            "text": {"type": "plain_text", "text": "📋 Ver mis objetivos"},
            "action_id": "personal_ver_objetivos",
        },
    },
    {
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": "*3.* Señalar limitaciones o aspectos relevantes respecto al cumplimiento de los criterios de evaluación",
        },
        "accessory": {
            "type": "button",
            "text": {"type": "plain_text", "text": "📊 Ver criterios"},
            "action_id": "personal_ver_criterios",
        },
    },
]

_BLOQUES_OPORTUNIDAD = [
    {
        "type": "section",
        "text": {"type": "mrkdwn", "text": "*Esta es tu oportunidad para:*"},
    },
    {
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": '*1.* Explicar cómo estás ayudando en _"Contribution to the firm"_',
        },
    },
    {
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": "*2.* Cómo te estás acercando a tus objetivos",
        },
        "accessory": {
            "type": "button",
            "text": {"type": "plain_text", "text": "📋 Ver mis objetivos"},
            "action_id": "personal_ver_objetivos",
        },
    },
    {
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": "*3.* Señalar limitaciones o aspectos relevantes respecto al cumplimiento de los criterios de evaluación",
        },
        "accessory": {
            "type": "button",
            "text": {"type": "plain_text", "text": "📊 Ver criterios"},
            "action_id": "personal_ver_criterios",
        },
    },
    {
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": (
                "*4.* Si necesitas ayuda con algún tema o has tenido alguna dificultad que quieras comentar\n"
                "_El botón de urgencia notifica a tu CA por Slack. Si no lo pulsas, el problema no se notifica automáticamente y solo quedará registrado._"
            ),
        },
        "accessory": {
            "type": "button",
            "text": {"type": "plain_text", "text": "🚨 Urgencia"},
            "style": "danger",
            "action_id": "personal_urgencia",
        },
    },
]


def enviar_pregunta_inicial_personal() -> None:
    try:
        if config.APP_MODE != "produccion" and config.SLACK_TEST_USER_ID:
            slack_ids = [config.SLACK_TEST_USER_ID]
        else:
            slack_ids = obtener_slack_ids_empleados()
            if not slack_ids:
                logging.warning("No se encontraron Slack IDs para evaluación personal")
                return

        with _lock:
            personal_dm_activas.clear()

        bloques_principal = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        "📝 *Tienes opción de seguimiento personal pendiente*\n\n"
                        "_Esta evaluación es totalmente privada, solo podrá verla tu CA._\n"
                        "_Si en algún momento quieres cancelar, escribe SOS en el hilo._\n\n"
                        "👉 *Envía cualquier mensaje en el hilo para comenzar la evaluación*"
                    ),
                },
            },
        ]

        for user_id in slack_ids:
            try:
                resp_dm = slack_app.client.conversations_open(users=[user_id])
                dm_channel = resp_dm["channel"]["id"]
                resp = slack_app.client.chat_postMessage(
                    channel=dm_channel,
                    text="📝 Tienes opción de seguimiento personal pendiente",
                    blocks=bloques_principal,
                )
                msg_ts = resp["ts"]
                with _lock:
                    personal_dm_activas.add(user_id)
                    personal_dm_ts[user_id] = msg_ts
                    personal_dm_canal[user_id] = dm_channel
                    personal_hora[user_id] = time.time()
                    conversaciones_personal.pop(user_id, None)
                logging.info("Evaluación personal enviada a %s", user_id)
            except Exception as exc:
                err_str = str(exc)
                if "user_not_found" in err_str or "channel_not_found" in err_str:
                    logging.warning("Slack ID %s no encontrado en el workspace, omitiendo", user_id)
                else:
                    logging.exception("Error enviando evaluación personal a %s", user_id)
    except Exception:
        logging.exception("Error en enviar_pregunta_inicial_personal")


def _enviar_preguntando_otro(channel, thread_ts):
    texto = "✅ Evaluación guardada. ¿Quieres añadir otro comentario?"
    slack_app.client.chat_postMessage(
        channel=channel,
        thread_ts=thread_ts,
        text=texto,
        blocks=[
            {"type": "section", "text": {"type": "mrkdwn", "text": texto}},
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "✅ Sí"},
                        "style": "primary",
                        "action_id": "personal_otro_si",
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "❌ No"},
                        "action_id": "personal_otro_no",
                    },
                ],
            },
        ],
    )


def notificar_urgencia_personal_web(nombre: str, descripcion: str) -> bool:
    """Notifica la urgencia al CA del empleado. Para uso desde la web."""
    return _notificar_urgencia_al_ca(nombre, descripcion, logging.getLogger(__name__))


def _notificar_urgencia_al_ca(nombre, descripcion, logger):
    nombre_ca = obtener_ca_de_empleado(nombre)
    if not nombre_ca:
        logger.warning("No se encontró CA para '%s', no se puede notificar urgencia", nombre)
        return False
    slack_id_ca = obtener_slack_id_por_nombre(nombre_ca)
    if not slack_id_ca:
        logger.warning("No se encontró Slack ID para el CA '%s'", nombre_ca)
        return False
    try:
        resp_dm = slack_app.client.conversations_open(users=[slack_id_ca])
        dm_ca = resp_dm["channel"]["id"]
        slack_app.client.chat_postMessage(
            channel=dm_ca,
            text=(
                f"🚨 *Urgencia de {nombre}*\n\n"
                f"*Descripción:* {descripcion}\n\n"
                "Por favor, contacta con él/ella lo antes posible."
            ),
        )
        logger.info("Urgencia de '%s' notificada al CA '%s'", nombre, nombre_ca)
        return True
    except Exception as e:
        if "user_not_found" in str(e):
            logger.warning(
                "Slack ID '%s' del CA '%s' no encontrado en el workspace. "
                "Comprueba el campo ID_usuario en Notion.",
                slack_id_ca, nombre_ca,
            )
        else:
            logger.exception("Error notificando urgencia al CA '%s'", nombre_ca)
        return False


def _enviar_resumen_urgencia(channel, thread_ts, descripcion):
    texto = f"📋 Tu descripción de urgencia:\n_{descripcion}_\n\n¿La envío a tu CA?"
    slack_app.client.chat_postMessage(
        channel=channel,
        thread_ts=thread_ts,
        text=texto,
        blocks=[
            {"type": "section", "text": {"type": "mrkdwn", "text": texto}},
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "✅ Enviar al CA"},
                        "style": "primary",
                        "action_id": "personal_urgencia_enviar",
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "✏️ Modificar"},
                        "action_id": "personal_urgencia_modificar",
                    },
                ],
            },
        ],
    )


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
        reply("Evaluación *cancelada* voluntariamente. Si quieres volver a empezar, escribe cualquier mensaje en este hilo.")
        return

    texto_norm = normalizar_nombre(texto)

    with _lock:
        estado = conversaciones_personal.get(user_id)
        if estado is None:
            estado = {"modo": "pre_inicial", "respuestas": {}}
            conversaciones_personal[user_id] = estado

        modo = estado.get("modo")
        urgencia_modo = estado.get("urgencia_modo")
        accion = None
        pregunta = None

        if urgencia_modo == "esperando_descripcion":
            if texto:
                estado["urgencia_descripcion"] = texto
                estado["urgencia_modo"] = "confirmacion_urgencia"
                accion = "mostrar_resumen_urgencia"
                pregunta = texto
            else:
                accion = "preguntar"
                pregunta = "🚨 Describe en una frase breve la urgencia:"

        elif urgencia_modo == "confirmacion_urgencia":
            if texto_norm in {"si", "sí", "s", "enviar", "confirmar"}:
                accion = "enviar_urgencia"
            elif texto_norm in {"modificar", "cambiar", "editar"}:
                estado["urgencia_modo"] = "esperando_descripcion"
                estado.pop("urgencia_descripcion", None)
                accion = "preguntar"
                pregunta = "🚨 Describe de nuevo la urgencia:"
            else:
                accion = "mostrar_resumen_urgencia"
                pregunta = estado.get("urgencia_descripcion", "")

        elif modo == "pre_inicial":
            estado["modo"] = "esperando_comentario"
            accion = "mostrar_bloque_inicio"

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
                estado["modo"] = "esperando_comentario"
                estado["respuestas"].pop("comentario", None)
                accion = "preguntar"
                pregunta = "Escribe de nuevo tu comentario:"
            else:
                accion = "mostrar_resumen"
                pregunta = (
                    f"📋 Tu comentario:\n_{estado['respuestas'].get('comentario', '')}_\n\n"
                    "Las únicas opciones son elegir uno de los botones o escribir *SOS* para terminar y perder el contenido de la evaluación."
                )

        elif modo == "guardar":
            accion = "guardar"
            respuestas_snap = dict(estado["respuestas"])

        elif modo == "preguntando_otro":
            if texto_norm in {"si", "sí", "s", "ok", "okay"}:
                estado["respuestas"] = {}
                estado["modo"] = "esperando_comentario"
                accion = "preguntar"
                pregunta = "¿Qué más me quieres contar? Responde con tu comentario."
            elif texto_norm in {"no", "n", "cancelar"}:
                estado["modo"] = "terminado"
                personal_dm_activas.discard(user_id)
                accion = "ya_terminado"
            else:
                accion = "preguntar_otro"

        elif modo == "terminado":
            accion = "ya_terminado"

    if accion == "mostrar_bloque_inicio":
        slack_app.client.chat_postMessage(
            channel=dm_channel,
            thread_ts=thread_ts,
            text="Esta es tu oportunidad para compartir tu progreso",
            blocks=_BLOQUES_OPORTUNIDAD,
        )
        return

    if accion == "mostrar_resumen_urgencia":
        _enviar_resumen_urgencia(dm_channel, thread_ts, pregunta or "")
        return

    if accion == "enviar_urgencia":
        nombre_u = obtener_nombre_por_id_usuario(user_id)
        if not nombre_u:
            try:
                resp_u = slack_app.client.users_info(user=user_id)
                u_d = resp_u.get("user", {})
                p_d = u_d.get("profile", {})
                nombre_u = u_d.get("real_name") or p_d.get("real_name") or p_d.get("display_name") or u_d.get("name") or user_id
            except Exception:
                nombre_u = user_id
        with _lock:
            descripcion = estado.get("urgencia_descripcion", "")
            estado.pop("urgencia_modo", None)
            estado.pop("urgencia_descripcion", None)
            modo_actual = estado.get("modo", "pre_inicial")
        ok = _notificar_urgencia_al_ca(nombre_u, descripcion, logger)
        if ok:
            reply("✅ Tu urgencia ha sido enviada a tu CA.")
        else:
            reply("⚠️ No se pudo notificar a tu CA. Contacta directamente.")
        slack_app.client.chat_postMessage(
            channel=dm_channel,
            thread_ts=thread_ts,
            text="Esta es tu oportunidad para compartir tu progreso",
            blocks=_BLOQUES_OPORTUNIDAD_SIN_URGENCIA,
        )
        return

    if accion == "preguntar":
        reply(pregunta)
        return

    if accion == "mostrar_resumen":
        slack_app.client.chat_postMessage(
            channel=dm_channel,
            thread_ts=thread_ts,
            text=pregunta,
            blocks=[
                {"type": "section", "text": {"type": "mrkdwn", "text": pregunta}},
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "✅ Sí, guardar"},
                            "style": "primary",
                            "action_id": "personal_confirmar",
                        },
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "✏️ Modificar"},
                            "action_id": "personal_modificar",
                        },
                    ],
                },
            ],
        )
        return

    if accion == "preguntar_otro":
        _enviar_preguntando_otro(dm_channel, thread_ts)
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
            _enviar_preguntando_otro(dm_channel, thread_ts)
        else:
            reply("⚠️ No se pudo guardar en Notion. Revisa los permisos o contacta con soporte.")
        return

    if accion == "ya_terminado":
        reply("Evaluación finalizada, por favor salga del hilo. 👋")
        return


@slack_app.action("personal_confirmar")
def _handle_personal_confirmar(ack, body, logger):
    ack()
    try:
        msg = body.get("message", {})
        evento = {
            "user": body["user"]["id"],
            "channel": body["channel"]["id"],
            "thread_ts": msg.get("thread_ts") or msg.get("ts", ""),
            "text": "sí",
        }
        manejar_mensaje_personal(evento, logger)
    except Exception:
        logger.exception("Error procesando confirmación personal interactiva")


@slack_app.action("personal_modificar")
def _handle_personal_modificar(ack, body, logger):
    ack()
    try:
        msg = body.get("message", {})
        evento = {
            "user": body["user"]["id"],
            "channel": body["channel"]["id"],
            "thread_ts": msg.get("thread_ts") or msg.get("ts", ""),
            "text": "modificar",
        }
        manejar_mensaje_personal(evento, logger)
    except Exception:
        logger.exception("Error procesando modificación personal interactiva")


@slack_app.action("personal_otro_si")
def _handle_personal_otro_si(ack, body, logger):
    ack()
    try:
        msg = body.get("message", {})
        evento = {
            "user": body["user"]["id"],
            "channel": body["channel"]["id"],
            "thread_ts": msg.get("thread_ts") or msg.get("ts", ""),
            "text": "sí",
        }
        manejar_mensaje_personal(evento, logger)
    except Exception:
        logger.exception("Error procesando personal_otro_si")


@slack_app.action("personal_otro_no")
def _handle_personal_otro_no(ack, body, logger):
    ack()
    try:
        msg = body.get("message", {})
        evento = {
            "user": body["user"]["id"],
            "channel": body["channel"]["id"],
            "thread_ts": msg.get("thread_ts") or msg.get("ts", ""),
            "text": "no",
        }
        manejar_mensaje_personal(evento, logger)
    except Exception:
        logger.exception("Error procesando personal_otro_no")


@slack_app.action("personal_ver_objetivos")
def _handle_personal_ver_objetivos(ack, body, logger):
    ack()
    try:
        user_id = body["user"]["id"]
        msg = body.get("message", {})
        channel = body["channel"]["id"]
        thread_ts = msg.get("ts", "")

        nombre = obtener_nombre_por_id_usuario(user_id)
        if not nombre:
            try:
                resp = slack_app.client.users_info(user=user_id)
                u = resp.get("user", {})
                p = u.get("profile", {})
                nombre = u.get("real_name") or p.get("real_name") or p.get("display_name") or u.get("name") or user_id
            except Exception:
                nombre = user_id

        objetivos = obtener_objetivos(nombre) if nombre else []
        if objetivos:
            texto_obj = objetivos[0]["objetivos"]
            msg_obj = f"📌 *Tus objetivos actuales:*\n\n{texto_obj}"
        else:
            msg_obj = "📌 No tienes objetivos registrados actualmente."

        slack_app.client.chat_postMessage(
            channel=channel,
            thread_ts=thread_ts,
            text=msg_obj,
        )
    except Exception:
        logger.exception("Error mostrando objetivos en evaluación personal")


@slack_app.action("personal_urgencia")
def _handle_personal_urgencia(ack, body, logger):
    ack()
    try:
        user_id = body["user"]["id"]
        msg = body.get("message", {})
        channel = body["channel"]["id"]
        thread_ts = msg.get("ts", "")
        dm_channel = personal_dm_canal.get(user_id, channel)

        with _lock:
            if user_id not in personal_dm_activas:
                return
            estado = conversaciones_personal.get(user_id)
            if estado is None:
                estado = {"modo": "pre_inicial", "respuestas": {}}
                conversaciones_personal[user_id] = estado
            estado["urgencia_modo"] = "esperando_descripcion"

        slack_app.client.chat_postMessage(
            channel=dm_channel,
            thread_ts=thread_ts,
            text="🚨 Describe en una frase breve la urgencia:",
        )
    except Exception:
        logger.exception("Error procesando urgencia en evaluación personal")


@slack_app.action("personal_urgencia_enviar")
def _handle_personal_urgencia_enviar(ack, body, logger):
    ack()
    try:
        user_id = body["user"]["id"]
        msg = body.get("message", {})
        channel = body["channel"]["id"]
        thread_ts = msg.get("thread_ts") or msg.get("ts", "")
        dm_channel = personal_dm_canal.get(user_id, channel)

        try:
            slack_app.client.chat_update(
                channel=channel,
                ts=msg["ts"],
                blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": "🚨 Urgencia enviada al CA ✅"}}],
                text="🚨 Urgencia enviada al CA ✅",
            )
        except Exception:
            pass

        with _lock:
            es_activo = user_id in personal_dm_activas
            estado = conversaciones_personal.get(user_id)
            if not es_activo or not estado or estado.get("urgencia_modo") != "confirmacion_urgencia":
                return
            descripcion = estado.get("urgencia_descripcion", "")
            estado.pop("urgencia_modo", None)
            estado.pop("urgencia_descripcion", None)
            modo_actual = estado.get("modo", "pre_inicial")

        nombre = obtener_nombre_por_id_usuario(user_id)
        if not nombre:
            try:
                resp = slack_app.client.users_info(user=user_id)
                u = resp.get("user", {})
                p = u.get("profile", {})
                nombre = u.get("real_name") or p.get("real_name") or p.get("display_name") or u.get("name") or user_id
            except Exception:
                nombre = user_id

        ok = _notificar_urgencia_al_ca(nombre, descripcion, logger)
        if ok:
            slack_app.client.chat_postMessage(channel=dm_channel, thread_ts=thread_ts, text="✅ Tu urgencia ha sido enviada a tu CA.")
        else:
            slack_app.client.chat_postMessage(channel=dm_channel, thread_ts=thread_ts, text="⚠️ No se pudo notificar a tu CA. Contacta directamente.")

        slack_app.client.chat_postMessage(
            channel=dm_channel,
            thread_ts=thread_ts,
            text="Esta es tu oportunidad para compartir tu progreso",
            blocks=_BLOQUES_OPORTUNIDAD_SIN_URGENCIA,
        )
    except Exception:
        logger.exception("Error procesando personal_urgencia_enviar")


@slack_app.action("personal_urgencia_modificar")
def _handle_personal_urgencia_modificar(ack, body, logger):
    ack()
    try:
        user_id = body["user"]["id"]
        msg = body.get("message", {})
        channel = body["channel"]["id"]
        thread_ts = msg.get("thread_ts") or msg.get("ts", "")
        dm_channel = personal_dm_canal.get(user_id, channel)

        try:
            slack_app.client.chat_update(
                channel=channel,
                ts=msg["ts"],
                blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": "✏️ Modificando..."}}],
                text="✏️ Modificando...",
            )
        except Exception:
            pass

        with _lock:
            es_activo = user_id in personal_dm_activas
            estado = conversaciones_personal.get(user_id)
            if not es_activo or not estado or estado.get("urgencia_modo") != "confirmacion_urgencia":
                return
            estado["urgencia_modo"] = "esperando_descripcion"
            estado.pop("urgencia_descripcion", None)

        slack_app.client.chat_postMessage(channel=dm_channel, thread_ts=thread_ts, text="🚨 Describe de nuevo la urgencia:")
    except Exception:
        logger.exception("Error procesando personal_urgencia_modificar")


# ---------------------------------------------------------------------------
# Criterios de evaluación — modal interactivo
# ---------------------------------------------------------------------------

_GRUPOS_CRITERIOS = {
    "negocio": "Negocio",
    "palantir": "Palantir",
    "middleoffice": "MiddleOffice",
}

_LIDERAZGO_KEYWORDS = {"liderazgo", "leadership", "leadership & management"}


def _es_subarea_liderazgo(nombre: str) -> bool:
    return any(kw in nombre.lower() for kw in _LIDERAZGO_KEYWORDS)


def _build_grupo_selector_view() -> dict:
    return {
        "type": "modal",
        "callback_id": "criterios_selector",
        "title": {"type": "plain_text", "text": "Criterios de evaluación"},
        "close": {"type": "plain_text", "text": "Cerrar"},
        "blocks": [
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": "¿Para qué área quieres ver los criterios?"},
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "static_select",
                        "action_id": "criterios_elegir_grupo",
                        "placeholder": {"type": "plain_text", "text": "Selecciona un área..."},
                        "options": [
                            {"text": {"type": "plain_text", "text": "Negocio"}, "value": "negocio"},
                            {"text": {"type": "plain_text", "text": "Palantir"}, "value": "palantir"},
                            {"text": {"type": "plain_text", "text": "Middle Office"}, "value": "middleoffice"},
                        ],
                    }
                ],
            },
        ],
    }


def _build_criterios_view(grupo: str, criterios: dict, expanded: set) -> dict:
    display = {"negocio": "Negocio", "palantir": "Palantir", "middleoffice": "Middle Office"}.get(grupo, grupo)
    blocks: list = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"📊 *Criterios de evaluación — {display}*\nPulsa *Ver* en cada subárea para expandirla:",
            },
        },
        {"type": "divider"},
    ]
    for subarea, niveles in criterios.items():
        es_liderazgo = _es_subarea_liderazgo(subarea)
        titulo = f"*{subarea}*" + (" _(solo Asociado Sr y Manager)_" if es_liderazgo else "")
        is_expanded = subarea in expanded
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": titulo},
            "accessory": {
                "type": "button",
                "text": {"type": "plain_text", "text": "▼ Ocultar" if is_expanded else "▶ Ver"},
                "action_id": "criterios_toggle",
                "value": subarea,
            },
        })
        if is_expanded:
            for nivel, textos in niveles.items():
                lineas = "\n".join(f"• {t}" for t in textos)
                blocks.append({
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": f"*{nivel}*\n{lineas}"[:3000]},
                })
            blocks.append({"type": "divider"})
    return {
        "type": "modal",
        "callback_id": "criterios_ver",
        "private_metadata": json.dumps({"grupo": grupo, "expanded": list(expanded)}),
        "title": {"type": "plain_text", "text": "Criterios"},
        "close": {"type": "plain_text", "text": "Cerrar"},
        "blocks": blocks[:100],
    }


@slack_app.action("personal_ver_criterios")
def _handle_personal_ver_criterios(ack, body, logger):
    ack()
    trigger_id = body.get("trigger_id")
    if not trigger_id:
        return
    try:
        slack_app.client.views_open(trigger_id=trigger_id, view=_build_grupo_selector_view())
    except Exception:
        logger.exception("Error abriendo modal de criterios")


@slack_app.action("criterios_elegir_grupo")
def _handle_criterios_elegir_grupo(ack, body, logger):
    ack()
    view = body.get("view", {})
    view_id = view.get("id")
    if not view_id:
        return
    action = (body.get("actions") or [{}])[0]
    selected = action.get("selected_option") or {}
    grupo = selected.get("value", "negocio")
    try:
        notion_grupo = _GRUPOS_CRITERIOS.get(grupo, grupo)
        criterios = obtener_criterios_evaluacion(notion_grupo)
        slack_app.client.views_update(
            view_id=view_id,
            view=_build_criterios_view(grupo, criterios, set()),
        )
    except Exception:
        logger.exception("Error mostrando criterios del grupo '%s'", grupo)


@slack_app.action("criterios_toggle")
def _handle_criterios_toggle(ack, body, logger):
    ack()
    view = body.get("view", {})
    try:
        metadata = json.loads(view.get("private_metadata", "{}"))
    except Exception:
        metadata = {}
    grupo = metadata.get("grupo", "negocio")
    expanded = set(metadata.get("expanded", []))
    action = (body.get("actions") or [{}])[0]
    subarea = action.get("value", "")
    if subarea in expanded:
        expanded.discard(subarea)
    else:
        expanded.add(subarea)
    try:
        notion_grupo = _GRUPOS_CRITERIOS.get(grupo, grupo)
        criterios = obtener_criterios_evaluacion(notion_grupo)
        slack_app.client.views_update(
            view_id=view["id"],
            view=_build_criterios_view(grupo, criterios, expanded),
        )
    except Exception:
        logger.exception("Error actualizando criterios para subárea '%s'", subarea)


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
                if (ahora - max(
                    personal_hora.get(uid, ahora),
                    personal_ultimo_recordatorio.get(uid, 0) or personal_hora.get(uid, ahora),
                )) >= _RECORDATORIO_SEGUNDOS
            ]
        for uid in pendientes:
            try:
                nombre = obtener_nombre_por_id_usuario(uid)
                if not nombre:
                    try:
                        resp = slack_app.client.users_info(user=uid)
                        u = resp.get("user", {})
                        p = u.get("profile", {})
                        nombre = u.get("real_name") or p.get("real_name") or p.get("display_name") or u.get("name") or uid
                    except Exception:
                        nombre = uid
                if evaluacion_personal_guardada_desde(nombre, personal_hora.get(uid, 0)):
                    with _lock:
                        personal_dm_activas.discard(uid)
                    continue
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
