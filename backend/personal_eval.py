import json
import logging
import re
import threading
import time
from datetime import datetime, timezone

from . import config
from .clients import slack_app
from .conversation_back import boton_atras, fila_atras, limpiar_historial, pop_historial, push_historial, tiene_historial
from .slack_lists import añadir_pendiente, enlace_lista_pendientes, quitar_pendiente
from .eval_tracking import registrar_envio_por_slack_id, marcar_completada_por_slack_id
from .i18n import t, botones_idioma_slack, traducir_dimension
from .notion_service import (
    evaluacion_personal_guardada_desde,
    guardar_evaluacion_personal,
    idioma_por_slack_id,
    guardar_idioma_por_slack_id,
    invalidar_cache_empleados,
    esperar_hasta_proximo_envio,
    obtener_ca_de_empleado,
    obtener_criterios_evaluacion,
    obtener_ejemplos_guia,
    obtener_nombre_por_id_usuario,
    obtener_objetivos_persona,
    obtener_preguntas_personales,
    PREGUNTAS_PERSONALES_DEFAULT,
    obtener_slack_id_por_nombre,
    obtener_slack_ids_empleados,
)
from .slack_carga import AnimacionCargando
from .utils import normalizar_nombre

_lock = threading.Lock()

personal_dm_activas: set = set()
personal_dm_ts: dict = {}
personal_dm_ts_anterior: dict = {}  # user_id -> ts de la personal anterior (caducada)
personal_dm_canal: dict = {}
personal_hora: dict = {}
personal_ultimo_recordatorio: dict = {}
conversaciones_personal: dict = {}
# user_id -> {"channel", "ts", "expanded": set(), "idioma"}: mensaje de ejemplos
# desplegables publicado en el hilo tras pulsar "Sí" en el DM inicial.
_personal_ejemplos_hilo: dict = {}

_RECORDATORIO_SEGUNDOS = 7 * 24 * 60 * 60  # 1 semana


def _editar_dm_inicial_personal(user_id, idioma=None):
    """Sustituye el mensaje inicial (raíz del hilo) del seguimiento personal por el
    resumen de 'completado'. Se llama al marcar la evaluación como completada."""
    ts = personal_dm_ts.get(user_id)
    canal = personal_dm_canal.get(user_id)
    if not ts or not canal:
        return
    idioma = idioma or idioma_por_slack_id(user_id)
    texto = t("bp.dm_completada", idioma)
    try:
        slack_app.client.chat_update(
            channel=canal, ts=ts, text=texto,
            blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": texto}}],
        )
    except Exception:
        logging.exception("No se pudo editar el DM inicial personal de %s", user_id)


def _editar_dm_inicial_personal_caducada(user_id, idioma=None):
    """Marca como caducado el DM inicial del seguimiento personal anterior de user_id,
    que quedó sin responder al llegar uno nuevo. No se toca si ya fue completado
    (en ese caso ya lo sustituyó _editar_dm_inicial_personal)."""
    ts = personal_dm_ts.get(user_id)
    canal = personal_dm_canal.get(user_id)
    if not ts or not canal:
        return
    idioma = idioma or idioma_por_slack_id(user_id)
    texto = t("bp.dm_expirada", idioma)
    try:
        slack_app.client.chat_update(
            channel=canal, ts=ts, text=texto,
            blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": texto}}],
        )
    except Exception:
        logging.exception("No se pudo marcar como caducado el DM inicial personal de %s", user_id)


# Temas del selector "¿Sobre qué vas a querer hablar hoy?".
# (clave del action_id, clave i18n de la etiqueta del botón, valor canónico guardado en Notion → columna "Tipo")
_TOPICOS_PERSONAL = [
    ("cttf",         "bp.topic_cttf",         "CTTF"),
    ("objetivos",    "bp.topic_objetivos",    "Objetivos"),
    ("dificultades", "bp.topic_dificultades", "Dificultades"),
    ("trayectoria",  "bp.topic_trayectoria",  "Trayectoria"),
    ("otro",         "bp.topic_otro",         "Otro"),
]
_TOPICO_LABEL = {clave: label for clave, _i18n, label in _TOPICOS_PERSONAL}

# Alias tópico → nombre del apartado en la BD "Ejemplos de Guía para bot", cuando no
# coinciden literalmente. CTTF = "Contribution To The Firm", así que el ejemplo de guía
# de ese área está guardado como "Personal-Contribution to the firm" en Notion.
# Dificultades y Trayectoria no tienen ejemplo propio, así que reutilizan los de
# Apoyo y Criterios respectivamente.
_ALIAS_APARTADO_EJEMPLO = {
    "cttf": "Contribution to the firm",
    "dificultades": "Apoyo",
    "trayectoria": "Criterios",
}


def _obtener_bloques_oportunidad(idioma: str = "es") -> list:
    preguntas = obtener_preguntas_personales(idioma)
    items = [
        ("item_1", None),
        ("item_2", {"type": "button", "text": {"type": "plain_text", "text": t("bp.btn_view_goals", idioma), "emoji": True}, "action_id": "personal_ver_objetivos"}),
        ("item_4", None),
        ("item_3", {"type": "button", "text": {"type": "plain_text", "text": t("bp.btn_view_criteria", idioma), "emoji": True}, "action_id": "personal_ver_criterios"}),
    ]
    bloques = [{"type": "section", "text": {"type": "mrkdwn", "text": t("bp.opp_header", idioma)}}]
    for clave, accessory in items:
        texto = preguntas.get(clave, clave)
        bloque: dict = {"type": "section", "text": {"type": "mrkdwn", "text": f"➜ {texto}"}}
        if accessory:
            bloque["accessory"] = accessory
        bloques.append(bloque)

    bloques.append({"type": "divider"})
    bloques.extend(_bloques_selector_topico(idioma, preguntas))
    return bloques


def _bloques_selector_topico(idioma: str = "es", preguntas: dict | None = None) -> list:
    """Bloques del selector '¿Sobre qué vas a querer hablar hoy?' (pregunta + 4 botones).

    Tanto el texto de la pregunta ('pregunta_tipo') como las etiquetas de los botones
    ('topic_*') son editables en Notion (BD 'Preguntas'); si faltan, se usa el texto i18n."""
    if preguntas is None:
        preguntas = obtener_preguntas_personales(idioma)
    # ES: valor de Notion (con fallback). Otros idiomas: valor de Notion SOLO si hay
    # una fila en ese idioma; si no, la traducción i18n.
    preguntas_disp = preguntas if idioma == "es" else obtener_preguntas_personales(idioma, con_fallback_es=False)
    pregunta_tipo = preguntas_disp.get("pregunta_tipo") or t("bp.q_topic", idioma)
    return [
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*{pregunta_tipo}*"}},
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": preguntas_disp.get(f"topic_{clave}") or t(clave_i18n, idioma), "emoji": True},
                    "action_id": f"personal_tipo_{clave}",
                }
                for clave, clave_i18n, _label in _TOPICOS_PERSONAL
            ],
        },
    ]


def _enviar_selector_topico(dm_channel, thread_ts, idioma="es", prefijo="") -> None:
    """Envía un mensaje con el selector de tema (para cuando pide 'otro comentario')."""
    preguntas = obtener_preguntas_personales(idioma)
    preguntas_disp = preguntas if idioma == "es" else obtener_preguntas_personales(idioma, con_fallback_es=False)
    pregunta_tipo = preguntas_disp.get("pregunta_tipo") or t("bp.q_topic", idioma)
    bloques = _bloques_selector_topico(idioma, preguntas)
    if prefijo:
        bloques = [{"type": "section", "text": {"type": "mrkdwn", "text": prefijo}}] + bloques
    slack_app.client.chat_postMessage(
        channel=dm_channel, thread_ts=thread_ts, text=pregunta_tipo, blocks=bloques,
    )


def _bloques_dm_personal(idioma, enlace_pendientes=None):
    """Bloques del DM inicial de la evaluación personal, con botón de cambio de idioma en la cabecera."""
    bloques = [
        botones_idioma_slack("lang_set_personal"),
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": t("bp.pending_header", idioma)},
        },
        {"type": "section", "text": {"type": "mrkdwn", "text": t("bp.pending_body", idioma)}},
        {"type": "context", "elements": [{"type": "mrkdwn", "text": t("bot.no_inteligente", idioma)}]},
        {"type": "section", "text": {"type": "mrkdwn", "text": t("bot.example_q", idioma)}},
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": t("bm.yes_btn", idioma), "emoji": True},
                    "style": "primary",
                    "action_id": "personal_ejemplo_si",
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": t("bm.no_btn", idioma), "emoji": True},
                    "action_id": "personal_ejemplo_no",
                },
            ],
        },
    ]
    if enlace_pendientes:
        bloques.append({"type": "section", "text": {"type": "mrkdwn", "text": t("bp.pendientes_link", idioma, url=enlace_pendientes)}})
    bloques.append({"type": "divider"})
    return bloques


def enviar_pregunta_inicial_personal() -> None:
    try:
        invalidar_cache_empleados()  # leer el idioma actual de Notion, no una copia cacheada
        if config.APP_MODE != "produccion" and config.SLACK_TEST_USER_IDS:
            slack_ids = config.SLACK_TEST_USER_IDS
        else:
            slack_ids = obtener_slack_ids_empleados()
            if not slack_ids:
                logging.warning("No se encontraron Slack IDs para evaluación personal")
                return

        with _lock:
            activas_previas = set(personal_dm_activas)
            personal_dm_activas.clear()

        enlace_pendientes = enlace_lista_pendientes()
        for user_id in slack_ids:
            try:
                idioma = idioma_por_slack_id(user_id)
                if user_id in activas_previas:
                    _editar_dm_inicial_personal_caducada(user_id, idioma)
                resp_dm = slack_app.client.conversations_open(users=[user_id])
                dm_channel = resp_dm["channel"]["id"]
                resp = slack_app.client.chat_postMessage(
                    channel=dm_channel,
                    text=t("bp.pending_fallback", idioma),
                    blocks=_bloques_dm_personal(idioma, enlace_pendientes),
                )
                msg_ts = resp["ts"]
                with _lock:
                    personal_dm_activas.add(user_id)
                    if personal_dm_ts.get(user_id):
                        personal_dm_ts_anterior[user_id] = personal_dm_ts[user_id]
                    personal_dm_ts[user_id] = msg_ts
                    personal_dm_canal[user_id] = dm_channel
                    personal_hora[user_id] = time.time()
                    conversaciones_personal.pop(user_id, None)
                añadir_pendiente("personal", user_id, t("bp.pendientes_titulo", idioma))
                registrar_envio_por_slack_id(user_id, "personal")
                logging.info("Evaluación personal enviada a %s", user_id)
            except Exception as exc:
                err_str = str(exc)
                if "user_not_found" in err_str or "channel_not_found" in err_str:
                    logging.warning("Slack ID %s no encontrado en el workspace, omitiendo", user_id)
                else:
                    logging.exception("Error enviando evaluación personal a %s", user_id)
    except Exception:
        logging.exception("Error en enviar_pregunta_inicial_personal")


def _enviar_preguntando_otro(channel, thread_ts, idioma="es"):
    texto = t("bp.saved_more_q", idioma)
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
                        "text": {"type": "plain_text", "text": t("bm.yes_btn", idioma), "emoji": True},
                        "style": "primary",
                        "action_id": "personal_otro_si",
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": t("bm.no_btn", idioma), "emoji": True},
                        "action_id": "personal_otro_no",
                    },
                ],
            },
        ],
    )


def _enviar_pregunta_personal(dm_channel, thread_ts, texto, estado, idioma="es"):
    bloques = [{"type": "section", "text": {"type": "mrkdwn", "text": texto}}]
    bloques += fila_atras("atras_personal", "bp.back_btn", estado, idioma)
    slack_app.client.chat_postMessage(channel=dm_channel, thread_ts=thread_ts, text=texto, blocks=bloques)


def _enviar_resumen_personal(dm_channel, thread_ts, texto, estado, idioma="es"):
    elementos = [
        {
            "type": "button",
            "text": {"type": "plain_text", "text": t("bp.btn_save_yes", idioma), "emoji": True},
            "style": "primary",
            "action_id": "personal_confirmar",
        },
        {
            "type": "button",
            "text": {"type": "plain_text", "text": t("bm.edit_btn", idioma), "emoji": True},
            "action_id": "personal_modificar",
        },
    ]
    if tiene_historial(estado):
        elementos.append(boton_atras("atras_personal", "bp.back_btn", idioma))
    slack_app.client.chat_postMessage(
        channel=dm_channel,
        thread_ts=thread_ts,
        text=texto,
        blocks=[
            {"type": "section", "text": {"type": "mrkdwn", "text": texto}},
            {"type": "actions", "elements": elementos},
        ],
    )


def _reenviar_pregunta_actual_personal(estado, dm_channel, thread_ts):
    idi = estado.get("idioma", "es")
    modo = estado.get("modo")
    if modo == "esperando_comentario":
        _enviar_pregunta_personal(dm_channel, thread_ts, t("bp.rewrite_comment", idi), estado, idi)
    elif modo == "confirmacion":
        texto = t("bp.comment_summary_opts", idi, texto=estado["respuestas"].get("comentario", ""))
        _enviar_resumen_personal(dm_channel, thread_ts, texto, estado, idi)


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
            text=t("bp.urgency_to_ca", idioma_por_slack_id(slack_id_ca), nombre=nombre, desc=descripcion),
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
        reply(t("bp.eval_finished", idioma_por_slack_id(user_id)))
        return

    if not es_activo:
        return

    if normalizar_nombre(texto) == "sos":
        with _lock:
            conversaciones_personal.pop(user_id, None)
        reply(t("bm.eval_cancelled", idioma_por_slack_id(user_id)))
        return

    texto_norm = normalizar_nombre(texto)

    with _lock:
        estado = conversaciones_personal.get(user_id)
        if estado is None:
            estado = {"modo": "pre_inicial", "respuestas": {}, "idioma": idioma_por_slack_id(user_id)}
            conversaciones_personal[user_id] = estado
        _idi = estado.get("idioma", "es")

        modo = estado.get("modo")
        accion = None
        pregunta = None

        if modo == "pre_inicial":
            estado["modo"] = "esperando_comentario"
            accion = "mostrar_bloque_inicio"

        elif modo == "esperando_comentario":
            if texto:
                push_historial(estado)
                estado["respuestas"]["comentario"] = texto
                estado["modo"] = "confirmacion"
                accion = "mostrar_resumen"
                pregunta = t("bp.comment_summary", _idi, texto=texto)
            else:
                accion = "preguntar"
                pregunta = t("bp.can_reply", _idi)

        elif modo == "confirmacion":
            if texto_norm in {"si", "sí", "s", "ok", "okay", "confirmar", "guardar", "correcto", "yes", "y", "save", "confirm", "correct", "sim", "gravar", "correto"}:
                estado["modo"] = "guardar"
                accion = "guardar"
                respuestas_snap = dict(estado["respuestas"])
            elif texto_norm in {"modificar", "cambiar", "editar", "modify", "change", "edit", "alterar", "mudar"}:
                push_historial(estado)
                estado["modo"] = "esperando_comentario"
                estado["respuestas"].pop("comentario", None)
                accion = "preguntar"
                pregunta = t("bp.rewrite_comment", _idi)
            else:
                accion = "mostrar_resumen"
                pregunta = t("bp.comment_summary_opts", _idi, texto=estado['respuestas'].get('comentario', ''))

        elif modo == "guardar":
            accion = "guardar"
            respuestas_snap = dict(estado["respuestas"])

        elif modo == "preguntando_otro":
            if texto_norm in {"si", "sí", "s", "ok", "okay", "yes", "y", "sim"}:
                estado["respuestas"] = {}
                estado["modo"] = "esperando_comentario"
                accion = "mostrar_topicos"
            elif texto_norm in {"no", "n", "cancelar", "cancel", "nao", "não", "cancelar"}:
                estado["modo"] = "terminado"
                personal_dm_activas.discard(user_id)
                accion = "ya_terminado"
            else:
                accion = "preguntar_otro"

        elif modo == "terminado":
            accion = "ya_terminado"

    if accion == "mostrar_bloque_inicio":
        # Primer mensaje del hilo: barra de carga mientras leemos las preguntas de Notion.
        with AnimacionCargando(dm_channel, thread_ts, _idi):
            bloques = _obtener_bloques_oportunidad(_idi)
        slack_app.client.chat_postMessage(
            channel=dm_channel,
            thread_ts=thread_ts,
            text=t("bp.opportunity_share", _idi),
            blocks=bloques,
        )
        return

    if accion == "preguntar":
        _enviar_pregunta_personal(dm_channel, thread_ts, pregunta, estado, _idi)
        return

    if accion == "mostrar_resumen":
        _enviar_resumen_personal(dm_channel, thread_ts, pregunta, estado, _idi)
        return

    if accion == "preguntar_otro":
        _enviar_preguntando_otro(dm_channel, thread_ts, _idi)
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

        with AnimacionCargando(dm_channel, thread_ts, _idi):
            guardado = guardar_evaluacion_personal(nombre, respuestas_snap)
        if guardado:
            with _lock:
                if conversaciones_personal.get(user_id, {}).get("modo") == "guardar":
                    conversaciones_personal[user_id]["modo"] = "preguntando_otro"
                    limpiar_historial(conversaciones_personal[user_id])
            quitar_pendiente("personal", user_id)
            marcar_completada_por_slack_id(user_id, "personal")
            _enviar_preguntando_otro(dm_channel, thread_ts, _idi)
        else:
            reply(t("bp.err_save", _idi))
        return

    if accion == "mostrar_topicos":
        _enviar_selector_topico(dm_channel, thread_ts, _idi)
        return

    if accion == "ya_terminado":
        # El mensaje inicial solo se marca como "completado" cuando el usuario termina del todo.
        _editar_dm_inicial_personal(user_id, _idi)
        reply(t("bp.eval_finished", _idi))
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


@slack_app.action(re.compile(r"^personal_tipo_(cttf|objetivos|dificultades|trayectoria|otro)$"))
def _handle_personal_tipo(ack, body, logger):
    ack()
    try:
        user_id = body["user"]["id"]
        channel = body["channel"]["id"]
        msg = body.get("message", {})
        thread_ts = msg.get("thread_ts") or msg.get("ts", "")
        dm_channel = personal_dm_canal.get(user_id, channel)
        clave = body["actions"][0]["action_id"].replace("personal_tipo_", "")
        # 'Tipo' guardado = etiqueta canónica en ES (editable en Notion), consistente entre idiomas.
        tipo = obtener_preguntas_personales("es").get(f"topic_{clave}") or _TOPICO_LABEL.get(clave, "")
        with _lock:
            estado = conversaciones_personal.get(user_id)
            if estado is None:
                estado = {"modo": "esperando_comentario", "respuestas": {}, "idioma": idioma_por_slack_id(user_id)}
                conversaciones_personal[user_id] = estado
            estado.setdefault("respuestas", {})["tipo"] = tipo
            if estado.get("modo") != "terminado":
                estado["modo"] = "esperando_comentario"
            _idi = estado.get("idioma", "es")
        tipo_display = tipo if _idi == "es" else (obtener_preguntas_personales(_idi, con_fallback_es=False).get(f"topic_{clave}") or t(f"bp.topic_{clave}", _idi))
        texto_msg = f"*{tipo_display}*\n{t('bp.write_comment', _idi)}"
        seccion: dict = {"type": "section", "text": {"type": "mrkdwn", "text": texto_msg}}
        # Botón "Ver ejemplo" a la derecha, solo si hay un ejemplo de guía para ese área.
        if _ejemplo_personal_para_clave(clave, obtener_ejemplos_guia(_idi)):
            seccion["accessory"] = {
                "type": "button",
                "text": {"type": "plain_text", "text": t("bp.see_example", _idi), "emoji": True},
                "action_id": "personal_ver_ejemplo_area",
                "value": clave,
            }
        slack_app.client.chat_postMessage(
            channel=dm_channel,
            thread_ts=thread_ts,
            text=texto_msg,
            blocks=[seccion],
        )
        # Desactiva el selector del mensaje original: sustituye los botones de tema por
        # una nota del tema elegido, para que no se pueda volver a pulsar y duplicar el
        # "Escribe tu comentario".
        try:
            bloques_orig = msg.get("blocks", [])

            def _es_selector_topico(b):
                return b.get("type") == "actions" and any(
                    el.get("action_id", "").startswith("personal_tipo_") for el in b.get("elements", [])
                )

            nuevos = [
                {"type": "context", "elements": [{"type": "mrkdwn", "text": f"✅ *{tipo_display}*"}]}
                if _es_selector_topico(b)
                else b
                for b in bloques_orig
            ]
            if nuevos != bloques_orig:
                slack_app.client.chat_update(
                    channel=channel,
                    ts=msg["ts"],
                    blocks=nuevos,
                    text=msg.get("text", tipo_display),
                )
        except Exception:
            logger.exception("No se pudo desactivar el selector de tipo personal")
    except Exception:
        logger.exception("Error procesando selección de tipo personal")


@slack_app.action("atras_personal")
def _handle_personal_atras(ack, body, client, logger):
    ack()
    try:
        user_id = body["user"]["id"]
        channel = body["channel"]["id"]
        msg = body.get("message", {})
        thread_ts = msg.get("thread_ts") or msg.get("ts", "")
        dm_channel = personal_dm_canal.get(user_id, channel)
        idi = idioma_por_slack_id(user_id)
        try:
            client.chat_update(
                channel=channel,
                ts=msg["ts"],
                blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": t("bp.back_done", idi)}}],
                text=t("bp.back_done", idi),
            )
        except Exception:
            logger.warning("No se pudo actualizar el mensaje al volver atrás (personal)")

        with _lock:
            estado = conversaciones_personal.get(user_id)
            if not estado or not pop_historial(estado):
                return
        _reenviar_pregunta_actual_personal(estado, dm_channel, thread_ts)
    except Exception:
        logger.exception("Error procesando atrás en evaluación personal")


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


def _resolver_nombre_slack(user_id: str) -> str:
    """Resuelve el nombre real del usuario (Notion → perfil de Slack → user_id)."""
    nombre = obtener_nombre_por_id_usuario(user_id)
    if not nombre:
        try:
            r = slack_app.client.users_info(user=user_id)
            u = r.get("user", {})
            p = u.get("profile", {})
            nombre = u.get("real_name") or p.get("real_name") or p.get("display_name") or u.get("name") or user_id
        except Exception:
            nombre = user_id
    return nombre


def _build_objetivos_view(objetivos: list, expanded: set | None = None, idioma: str = "es") -> dict:
    """Modal con los objetivos actuales (título + KPIs). La descripción va en un desplegable."""
    expanded = expanded or set()
    if objetivos:
        blocks: list = [
            {"type": "section", "text": {"type": "mrkdwn", "text": t("bp.current_goals_header", idioma)}},
            {"type": "divider"},
        ]
        for idx, obj in enumerate(objetivos):
            clave = str(idx)
            is_exp = clave in expanded
            linea = f"• *{obj['titulo']}*"
            if obj.get("kpis"):
                linea += f"\n  _KPIs:_ {obj['kpis']}"
            descripcion = (obj.get("descripcion") or "").strip()
            seccion: dict = {"type": "section", "text": {"type": "mrkdwn", "text": linea[:3000]}}
            if descripcion:
                seccion["accessory"] = {
                    "type": "button",
                    "text": {"type": "plain_text", "text": t("bm.btn_hide_item", idioma) if is_exp else t("bm.btn_show_item", idioma)},
                    "action_id": "objetivos_personal_toggle",
                    "value": clave,
                }
            blocks.append(seccion)
            if descripcion and is_exp:
                blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": descripcion[:3000]}})
                blocks.append({"type": "divider"})
    else:
        blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": t("bp.no_current_goals", idioma)}}]
    return {
        "type": "modal",
        "callback_id": "objetivos_personal_ver",
        "private_metadata": json.dumps({"expanded": list(expanded)}),
        "title": {"type": "plain_text", "text": t("bp.btn_view_goals", idioma)[:24]},
        "close": {"type": "plain_text", "text": t("bm.close", idioma)},
        "blocks": blocks[:100],
    }


@slack_app.action("personal_ver_objetivos")
def _handle_personal_ver_objetivos(ack, body, logger):
    ack()
    trigger_id = body.get("trigger_id")
    if not trigger_id:
        return
    user_id = body.get("user", {}).get("id", "")
    _idi = idioma_por_slack_id(user_id)
    # Abrir un modal de carga YA (sin lecturas de Notion) para no agotar el trigger_id (~3s),
    # y luego rellenarlo con views_update una vez leídos los objetivos.
    try:
        resp = slack_app.client.views_open(
            trigger_id=trigger_id, view=_vista_modal_cargando(t("bp.btn_view_goals", _idi)),
        )
    except Exception:
        logger.exception("Error abriendo modal de objetivos personal")
        return
    try:
        nombre = _resolver_nombre_slack(user_id)
        objetivos = obtener_objetivos_persona(nombre) if nombre else []
        slack_app.client.views_update(
            view_id=resp["view"]["id"], view=_build_objetivos_view(objetivos, set(), _idi),
        )
    except Exception:
        logger.exception("Error mostrando objetivos en modal personal")


@slack_app.action("objetivos_personal_toggle")
def _handle_objetivos_personal_toggle(ack, body, logger):
    ack()
    view = body.get("view", {})
    try:
        metadata = json.loads(view.get("private_metadata", "{}"))
    except Exception:
        metadata = {}
    expanded = set(metadata.get("expanded", []))
    clave = (body.get("actions") or [{}])[0].get("value", "")
    if clave in expanded:
        expanded.discard(clave)
    else:
        expanded.add(clave)
    try:
        user_id = body.get("user", {}).get("id", "")
        _idi = idioma_por_slack_id(user_id)
        objetivos = obtener_objetivos_persona(_resolver_nombre_slack(user_id))
        slack_app.client.views_update(
            view_id=view["id"], view=_build_objetivos_view(objetivos, expanded, _idi),
        )
    except Exception:
        logger.exception("Error actualizando desplegable de objetivos personal")


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


def _build_grupo_selector_view(idioma: str = "es") -> dict:
    return {
        "type": "modal",
        "callback_id": "criterios_selector",
        "title": {"type": "plain_text", "text": t("bp.criteria_title", idioma)},
        "close": {"type": "plain_text", "text": t("bm.close", idioma)},
        "blocks": [
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": t("bp.criteria_which_area", idioma)},
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "static_select",
                        "action_id": "criterios_elegir_grupo",
                        "placeholder": {"type": "plain_text", "text": t("bp.criteria_select_area", idioma)},
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


def _build_criterios_view(grupo: str, criterios: dict, expanded: set, idioma: str = "es") -> dict:
    display = {"negocio": "Negocio", "palantir": "Palantir", "middleoffice": "Middle Office"}.get(grupo, grupo)
    blocks: list = [
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": t("bp.criteria_intro", idioma, display=display)},
        },
        {"type": "divider"},
    ]
    for subarea, niveles in criterios.items():
        es_liderazgo = _es_subarea_liderazgo(subarea)
        titulo = f"*{traducir_dimension(subarea, idioma)}*" + (t("bp.criteria_leadership_note", idioma) if es_liderazgo else "")
        is_expanded = subarea in expanded
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": titulo},
            "accessory": {
                "type": "button",
                "text": {"type": "plain_text", "text": t("bm.btn_hide_item", idioma) if is_expanded else t("bm.btn_show_item", idioma)},
                "action_id": "criterios_toggle",
                "value": subarea,
            },
        })
        if is_expanded:
            for nivel, textos in niveles.items():
                lineas = "\n".join(f"• {texto}" for texto in textos)
                blocks.append({
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": f"*{nivel}*\n{lineas}"[:3000]},
                })
                blocks.append({"type": "divider"})
    return {
        "type": "modal",
        "callback_id": "criterios_ver",
        "private_metadata": json.dumps({"grupo": grupo, "expanded": list(expanded)}),
        "title": {"type": "plain_text", "text": t("bp.criteria_title_short", idioma)},
        "close": {"type": "plain_text", "text": t("bm.close", idioma)},
        "blocks": blocks[:100],
    }


@slack_app.action("personal_ver_criterios")
def _handle_personal_ver_criterios(ack, body, logger):
    ack()
    trigger_id = body.get("trigger_id")
    if not trigger_id:
        return
    try:
        _idi = idioma_por_slack_id(body.get("user", {}).get("id", ""))
        slack_app.client.views_open(trigger_id=trigger_id, view=_build_grupo_selector_view(_idi))
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
        _idi = idioma_por_slack_id(body.get("user", {}).get("id", ""))
        criterios = obtener_criterios_evaluacion(notion_grupo, _idi)
        slack_app.client.views_update(
            view_id=view_id,
            view=_build_criterios_view(grupo, criterios, set(), _idi),
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
        _idi = idioma_por_slack_id(body.get("user", {}).get("id", ""))
        criterios = obtener_criterios_evaluacion(notion_grupo, _idi)
        slack_app.client.views_update(
            view_id=view["id"],
            view=_build_criterios_view(grupo, criterios, expanded, _idi),
        )
    except Exception:
        logger.exception("Error actualizando criterios para subárea '%s'", subarea)


# ---------------------------------------------------------------------------
# Ejemplos de guía — modal interactivo (Personal)
# ---------------------------------------------------------------------------

# Traducción de los nombres de apartado de los ejemplos de guía (la clave en Notion
# es estable/en español; aquí la traducimos al mostrarla). Si no está en el mapa,
# se muestra tal cual.
_TRAD_APARTADO = {
    "objetivos": {"en": "Goals", "pt": "Objetivos"},
    "apoyo": {"en": "Support", "pt": "Apoio"},
    "criterios": {"en": "Criteria", "pt": "Critérios"},
    "contribution to the firm": {"en": "Contribution to the firm", "pt": "Contribuição para a empresa"},
}


def _traducir_apartado(nombre: str, idioma: str) -> str:
    if idioma == "es":
        return nombre
    return _TRAD_APARTADO.get(nombre.strip().lower(), {}).get(idioma, nombre)


def _bloques_items_ejemplos_personal(personales: dict, expanded: set, idioma: str, action_id: str) -> list:
    """Bloques desplegables (uno por apartado), con botón Ver/Ocultar. Compartido
    entre el modal de ejemplos y el mensaje de ejemplos en el hilo; `action_id`
    distingue quién debe manejar el toggle en cada contexto."""
    blocks: list = []
    for tipo, ejemplo in personales.items():
        is_expanded = tipo in expanded
        nombre = _traducir_apartado(_nombre_apartado_ejemplo(tipo), idioma)
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*{nombre}*"},
            "accessory": {
                "type": "button",
                "text": {"type": "plain_text", "text": t("bm.btn_hide_item", idioma) if is_expanded else t("bm.btn_show_item", idioma)},
                "action_id": action_id,
                "value": tipo,  # clave exacta de Notion para el toggle
            },
        })
        if is_expanded:
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": ejemplo[:3000] if ejemplo else t("bm.no_example", idioma)},
            })
            blocks.append({"type": "divider"})
    return blocks


def _build_ejemplos_personal_view(ejemplos: dict, expanded: set, idioma: str = "es") -> dict:
    # Filtrar entradas de Notion cuyo tipo contenga "personal" (case-insensitive)
    personales = {k: v for k, v in ejemplos.items() if "personal" in k.lower()}

    blocks: list = [
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": t("bp.examples_intro", idioma)},
        },
        {"type": "divider"},
    ]
    blocks.extend(_bloques_items_ejemplos_personal(personales, expanded, idioma, "ejemplo_personal_toggle"))

    if not personales:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": t("bp.no_personal_examples", idioma)},
        })

    return {
        "type": "modal",
        "callback_id": "ejemplo_personal_ver",
        "private_metadata": json.dumps({"expanded": list(expanded)}),
        "title": {"type": "plain_text", "text": t("bp.examples_title", idioma)},
        "close": {"type": "plain_text", "text": t("bm.close", idioma)},
        "blocks": blocks[:100],
    }


def _bloques_ejemplos_personal_hilo(ejemplos: dict, expanded: set, idioma: str = "es") -> list:
    """Bloques del mensaje de ejemplos publicado en el hilo (apartados desplegables,
    igual que el modal antiguo, pero en el propio hilo)."""
    personales = {k: v for k, v in ejemplos.items() if "personal" in k.lower()}
    blocks: list = [
        {"type": "section", "text": {"type": "mrkdwn", "text": t("bp.examples_header", idioma)}},
        {"type": "divider"},
    ]
    blocks.extend(_bloques_items_ejemplos_personal(personales, expanded, idioma, "personal_ejemplo_toggle_hilo"))
    if not personales:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": t("bp.no_personal_examples", idioma)},
        })
    return blocks[:50]


def _nombre_apartado_ejemplo(tipo: str) -> str:
    """Quita el prefijo 'Personal - ' de la clave de Notion y devuelve el nombre del apartado."""
    nombre = tipo
    for prefijo in ("Personal - ", "Personal-", "personal - ", "personal-"):
        if nombre.startswith(prefijo):
            return nombre[len(prefijo):].strip()
    return nombre


def _ejemplo_personal_para_clave(clave: str, ejemplos: dict) -> tuple[str, str] | None:
    """Devuelve (tipo_key, texto) del ejemplo personal que corresponde al tópico `clave`
    (cttf/objetivos/dificultades/trayectoria), o None si no hay ejemplo para ese área."""
    candidatos = {
        _TOPICO_LABEL.get(clave, ""),
        obtener_preguntas_personales("es").get(f"topic_{clave}") or "",
        clave,
        # Alias: el nombre del apartado en la BD de ejemplos no siempre coincide con la
        # etiqueta del botón (p.ej. el botón dice "CTTF" pero el ejemplo se llama
        # "Contribution to the firm", que es lo que significa el acrónimo).
        _ALIAS_APARTADO_EJEMPLO.get(clave, ""),
    }
    candidatos_norm = {normalizar_nombre(c) for c in candidatos if c}
    for tipo, texto in ejemplos.items():
        if "personal" not in tipo.lower():
            continue
        if normalizar_nombre(_nombre_apartado_ejemplo(tipo)) in candidatos_norm:
            return tipo, texto
    return None


def _build_ejemplo_area_view(clave: str, ejemplos: dict, idioma: str = "es") -> dict:
    """Modal con el ejemplo de guía de un único área (el tópico que el usuario acaba de pulsar)."""
    match = _ejemplo_personal_para_clave(clave, ejemplos)
    if match:
        tipo, texto = match
        nombre = _traducir_apartado(_nombre_apartado_ejemplo(tipo), idioma)
        blocks = [
            {"type": "section", "text": {"type": "mrkdwn", "text": f"*{nombre}*"}},
            {"type": "divider"},
            {"type": "section", "text": {"type": "mrkdwn", "text": texto[:3000] if texto else t("bm.no_example", idioma)}},
        ]
    else:
        blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": t("bp.no_personal_examples", idioma)}}]
    return {
        "type": "modal",
        "callback_id": "ejemplo_personal_area_ver",
        "title": {"type": "plain_text", "text": t("bp.examples_title", idioma)},
        "close": {"type": "plain_text", "text": t("bm.close", idioma)},
        "blocks": blocks[:100],
    }


@slack_app.action(re.compile(r"^lang_set_personal_(es|en|pt)$"))
def _handle_lang_set_personal(ack, body, logger):
    ack()
    try:
        user_id = body.get("user", {}).get("id", "")
        idioma_elegido = body["actions"][0]["value"]
        nuevo = guardar_idioma_por_slack_id(user_id, idioma_elegido)
        channel = (body.get("channel") or {}).get("id") or (body.get("container") or {}).get("channel_id")
        ts = (body.get("message") or {}).get("ts") or (body.get("container") or {}).get("message_ts")
        if channel and ts:
            slack_app.client.chat_update(
                channel=channel,
                ts=ts,
                text=t("bp.pending_fallback", nuevo),
                blocks=_bloques_dm_personal(nuevo),
            )
    except Exception:
        logger.exception("Error cambiando idioma (personal)")


def _vista_modal_cargando(titulo: str = "Ejemplo") -> dict:
    """Modal ligero de carga: se abre al instante para no agotar el trigger_id de Slack."""
    return {
        "type": "modal",
        "title": {"type": "plain_text", "text": (titulo or "…")[:24]},
        "close": {"type": "plain_text", "text": "Cerrar"},
        "blocks": [{"type": "section", "text": {"type": "mrkdwn", "text": "⏳ Cargando… / Loading… / A carregar…"}}],
    }


@slack_app.action("personal_ver_ejemplo")
def _handle_personal_ver_ejemplo(ack, body, logger):
    ack()
    trigger_id = body.get("trigger_id")
    if not trigger_id:
        return
    # Abrir un modal de carga YA (sin lecturas de Notion) para no agotar el trigger_id (~3s),
    # y luego rellenarlo con views_update una vez leídos idioma + ejemplos.
    try:
        resp = slack_app.client.views_open(trigger_id=trigger_id, view=_vista_modal_cargando())
    except Exception:
        logger.exception("Error abriendo modal de ejemplos personal")
        return
    try:
        _idi = idioma_por_slack_id(body.get("user", {}).get("id", ""))
        ejemplos = obtener_ejemplos_guia(_idi)
        slack_app.client.views_update(
            view_id=resp["view"]["id"],
            view=_build_ejemplos_personal_view(ejemplos, set(), _idi),
        )
    except Exception:
        logger.exception("Error actualizando modal de ejemplos personal")


@slack_app.action("personal_ver_ejemplo_area")
def _handle_personal_ver_ejemplo_area(ack, body, logger):
    ack()
    trigger_id = body.get("trigger_id")
    if not trigger_id:
        return
    clave = (body.get("actions") or [{}])[0].get("value", "")
    # Modal de carga inmediato (sin lecturas de Notion) para no agotar el trigger_id.
    try:
        resp = slack_app.client.views_open(trigger_id=trigger_id, view=_vista_modal_cargando())
    except Exception:
        logger.exception("Error abriendo modal de ejemplo de área personal")
        return
    try:
        _idi = idioma_por_slack_id(body.get("user", {}).get("id", ""))
        ejemplos = obtener_ejemplos_guia(_idi)
        slack_app.client.views_update(
            view_id=resp["view"]["id"],
            view=_build_ejemplo_area_view(clave, ejemplos, _idi),
        )
    except Exception:
        logger.exception("Error actualizando modal de ejemplo de área personal")


@slack_app.action("ejemplo_personal_toggle")
def _handle_ejemplo_personal_toggle(ack, body, logger):
    ack()
    view = body.get("view", {})
    try:
        metadata = json.loads(view.get("private_metadata", "{}"))
    except Exception:
        metadata = {}
    expanded = set(metadata.get("expanded", []))
    action = (body.get("actions") or [{}])[0]
    tipo = action.get("value", "")
    if tipo in expanded:
        expanded.discard(tipo)
    else:
        expanded.add(tipo)
    try:
        _idi = idioma_por_slack_id(body.get("user", {}).get("id", ""))
        ejemplos = obtener_ejemplos_guia(_idi)
        slack_app.client.views_update(
            view_id=view["id"],
            view=_build_ejemplos_personal_view(ejemplos, expanded, _idi),
        )
    except Exception:
        logger.exception("Error actualizando ejemplos personal para tipo '%s'", tipo)


def _arrancar_personal_desde_boton(body, logger, con_ejemplo):
    """Botones Sí/No del DM inicial personal. 'Sí' publica los ejemplos de guía en
    el hilo; ambos arrancan la evaluación inyectando el evento que antes generaba
    el primer mensaje del usuario. Si la conversación ya está en marcha, 'Sí' solo
    muestra los ejemplos y 'No' no hace nada."""
    user_id = body.get("user", {}).get("id", "")
    channel = (body.get("channel") or {}).get("id") or (body.get("container") or {}).get("channel_id")
    msg = body.get("message") or {}
    thread_ts = msg.get("thread_ts") or msg.get("ts")
    if not (user_id and channel and thread_ts):
        return
    with _lock:
        es_activo = user_id in personal_dm_activas and thread_ts == personal_dm_ts.get(user_id)
        estado = conversaciones_personal.get(user_id)
        ya_empezada = estado is not None and estado.get("modo", "pre_inicial") != "pre_inicial"
    if not es_activo:
        return
    idioma = idioma_por_slack_id(user_id)
    if con_ejemplo:
        with AnimacionCargando(channel, thread_ts, idioma):
            ejemplos = obtener_ejemplos_guia(idioma)
        expanded: set = set()
        resp = slack_app.client.chat_postMessage(
            channel=channel,
            thread_ts=thread_ts,
            text=t("bp.examples_header", idioma),
            blocks=_bloques_ejemplos_personal_hilo(ejemplos, expanded, idioma),
        )
        with _lock:
            _personal_ejemplos_hilo[user_id] = {
                "channel": channel, "ts": resp["ts"], "expanded": expanded, "idioma": idioma,
            }
    if ya_empezada:
        return
    manejar_mensaje_personal({"user": user_id, "channel": channel, "thread_ts": thread_ts, "text": ""}, logger)


@slack_app.action("personal_ejemplo_toggle_hilo")
def _handle_personal_ejemplo_toggle_hilo(ack, body, logger):
    ack()
    user_id = body.get("user", {}).get("id", "")
    tipo = (body.get("actions") or [{}])[0].get("value", "")
    try:
        with _lock:
            estado = _personal_ejemplos_hilo.get(user_id)
            if estado is None:
                return
            expanded = estado["expanded"]
            if tipo in expanded:
                expanded.discard(tipo)
            else:
                expanded.add(tipo)
            channel, ts, idioma = estado["channel"], estado["ts"], estado["idioma"]
        ejemplos = obtener_ejemplos_guia(idioma)
        slack_app.client.chat_update(
            channel=channel,
            ts=ts,
            text=t("bp.examples_header", idioma),
            blocks=_bloques_ejemplos_personal_hilo(ejemplos, expanded, idioma),
        )
    except Exception:
        logger.exception("Error actualizando ejemplos personal en el hilo para tipo '%s'", tipo)


@slack_app.action("personal_ejemplo_si")
def _handle_personal_ejemplo_si(ack, body, logger):
    ack()
    try:
        _arrancar_personal_desde_boton(body, logger, con_ejemplo=True)
    except Exception:
        logger.exception("Error arrancando evaluación personal desde el botón Sí")


@slack_app.action("personal_ejemplo_no")
def _handle_personal_ejemplo_no(ack, body, logger):
    ack()
    try:
        _arrancar_personal_desde_boton(body, logger, con_ejemplo=False)
    except Exception:
        logger.exception("Error arrancando evaluación personal desde el botón No")


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
    # Cada 2 semanas, desfasado unas horas de proyecto para que, cuando coincidan en el
    # mismo día (cada 4 semanas), no lleguen a la misma hora. esperar_hasta_proximo_envio
    # relee el calendario mientras espera, así un cambio de fecha en caliente se aplica
    # sin reiniciar.
    while True:
        esperar_hasta_proximo_envio("personal", 2, offset_horas=config.PERSONAL_OFFSET_HORAS, etiqueta="[Personal]")
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
                    text=t("bp.reminder", idioma_por_slack_id(uid)),
                )
                with _lock:
                    personal_ultimo_recordatorio[uid] = ahora
            except Exception:
                logging.exception("Error enviando recordatorio personal a %s", uid)
