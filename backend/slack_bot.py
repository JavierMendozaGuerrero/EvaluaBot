import logging
import re
import threading
import time
from datetime import datetime, timedelta, timezone

from slack_bolt.adapter.socket_mode import SocketModeHandler

from . import config
from .i18n import t, botones_idioma_slack
from .conversation_back import boton_atras, fila_atras, limpiar_historial, pop_historial, push_historial, tiene_historial
from .slack_lists import añadir_pendiente, enlace_lista_pendientes, quitar_pendiente
from .eval_tracking import registrar_envio_por_slack_id, marcar_completada_por_slack_id
from .ca_reviews import ca_dm_activas, ca_dm_ts, ca_dm_ts_anterior, manejar_mensaje_ca
from .personal_eval import (
    enviar_pregunta_inicial_personal,
    manejar_mensaje_personal,
    personal_dm_activas,
    personal_dm_ts,
    personal_dm_ts_anterior,
)
from .clients import slack_app
from .slack_carga import AnimacionCargando
from .hierarchy import comparar_jerarquia, tipo_relacion
from .notion_service import (
    buscar_empleado_y_cargo,
    evaluacion_proyecto_guardada_desde,
    guardar_barbecho_en_notion,
    actualizar_en_notion,
    guardar_en_notion,
    obtener_area_por_slack_id,
    obtener_cargo_por_slack_id,
    idioma_por_slack_id,
    guardar_idioma_por_slack_id,
    invalidar_cache_empleados,
    obtener_config_calendario,
    obtener_ejemplos_guia,
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
    evaluacion_dm_ts_anterior,
    evaluacion_hora,
    evaluacion_ultimo_recordatorio,
    evaluaciones_dm_activas,
    evaluaciones_dm_expiradas,
    lock,
)
from .utils import normalizar_nombre


def _editar_dm_inicial_mensual(user_id, idioma=None):
    """Sustituye el mensaje inicial (raíz del hilo) de la evaluación mensual por el
    resumen de 'completada'. Se llama al marcar la evaluación como completada."""
    ts = evaluacion_dm_ts.get(user_id)
    canal = evaluacion_dm_canal.get(user_id)
    if not ts or not canal:
        return
    idioma = idioma or idioma_por_slack_id(user_id)
    texto = t("bm.dm_completada", idioma)
    try:
        slack_app.client.chat_update(
            channel=canal, ts=ts, text=texto,
            blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": texto}}],
        )
    except Exception:
        logging.exception("No se pudo editar el DM inicial mensual de %s", user_id)


def _editar_dm_inicial_mensual_caducada(user_id, idioma=None):
    """Marca como caducado el DM inicial de la evaluación mensual anterior de user_id,
    que quedó sin responder al llegar una nueva. No se toca si ya fue completada
    (en ese caso ya la sustituyó _editar_dm_inicial_mensual)."""
    ts = evaluacion_dm_ts.get(user_id)
    canal = evaluacion_dm_canal.get(user_id)
    if not ts or not canal:
        return
    idioma = idioma or idioma_por_slack_id(user_id)
    texto = t("bm.dm_expirada", idioma)
    try:
        slack_app.client.chat_update(
            channel=canal, ts=ts, text=texto,
            blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": texto}}],
        )
    except Exception:
        logging.exception("No se pudo marcar como caducado el DM inicial mensual de %s", user_id)


def _bloques_dm_mensual(idioma, enlace_pendientes=None):
    """Bloques del DM inicial de la evaluación mensual, con botón de cambio de idioma en la cabecera."""
    bloques = [
        botones_idioma_slack("lang_set_mensual"),
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": t("bm.pending_header", idioma)},
        },
        {"type": "section", "text": {"type": "mrkdwn", "text": t("bm.pending_body", idioma)}},
        {"type": "context", "elements": [{"type": "mrkdwn", "text": t("bot.no_inteligente", idioma)}]},
        {"type": "section", "text": {"type": "mrkdwn", "text": t("bot.example_q", idioma)}},
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": t("bm.yes_btn", idioma), "emoji": True},
                    "style": "primary",
                    "action_id": "mensual_ejemplo_si",
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": t("bm.no_btn", idioma), "emoji": True},
                    "action_id": "mensual_ejemplo_no",
                },
            ],
        },
    ]
    if enlace_pendientes:
        bloques.append({"type": "section", "text": {"type": "mrkdwn", "text": t("bm.pendientes_link", idioma, url=enlace_pendientes)}})
    bloques.append({"type": "divider"})
    return bloques


def enviar_una_evaluacion():
    try:
        invalidar_cache_empleados()  # leer el idioma actual de Notion, no una copia cacheada
        if config.APP_MODE != "produccion" and config.SLACK_TEST_USER_ID:
            slack_ids = [config.SLACK_TEST_USER_ID]
            logging.info(f"Modo prueba: enviando solo a {config.SLACK_TEST_USER_ID}")
        else:
            slack_ids = obtener_slack_ids_empleados()
            if not slack_ids:
                logging.warning("No se encontraron Slack IDs en la lista de empleados de Notion")
                return
        with lock:
            activas_previas = set(evaluaciones_dm_activas)
            evaluaciones_dm_expiradas.update(evaluaciones_dm_activas)
            evaluaciones_dm_activas.clear()
        enlace_pendientes = enlace_lista_pendientes()
        for user_id in slack_ids:
            try:
                idioma = idioma_por_slack_id(user_id)
                if user_id in activas_previas:
                    _editar_dm_inicial_mensual_caducada(user_id, idioma)
                resp_dm = slack_app.client.conversations_open(users=[user_id])
                dm_channel = resp_dm["channel"]["id"]
                resp = slack_app.client.chat_postMessage(
                    channel=dm_channel,
                    text=t("bm.pending_fallback", idioma),
                    blocks=_bloques_dm_mensual(idioma, enlace_pendientes),
                )
                with lock:
                    evaluaciones_dm_activas.add(user_id)
                    evaluacion_dm_canal[user_id] = dm_channel
                    if evaluacion_dm_ts.get(user_id):
                        evaluacion_dm_ts_anterior[user_id] = evaluacion_dm_ts[user_id]
                    evaluacion_dm_ts[user_id] = resp["ts"]
                    evaluacion_hora[user_id] = time.time()
                    conversaciones.pop(user_id, None)
                añadir_pendiente("mensual", user_id, t("bm.pendientes_titulo", idioma))
                registrar_envio_por_slack_id(user_id, "mensual")
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


def resumen_respuestas(respuestas, area="negocio", preguntas_area=None, tras_modificacion=False, idioma="es"):
    _sufijo = t("bm.updated_suffix", idioma) if tras_modificacion else t("bm.satisfied_suffix", idioma)
    lineas = [t("bm.summary_head", idioma)]
    lineas.append(t("bm.summary_evaluado", idioma, v=respuestas.get('evaluado', '')))
    if respuestas.get("proyecto"):
        lineas.append(t("bm.summary_proyecto", idioma, v=respuestas.get('proyecto', '')))
    if respuestas.get("satisfaccion"):
        lineas.append(t("bm.summary_satisfaccion", idioma, v=respuestas.get('satisfaccion', '')))
    if preguntas_area:
        for q in preguntas_area:
            val = respuestas.get(q["clave"], "")
            label = q["texto"].split("\n")[0][:55].strip()
            lineas.append(f"- *{label}*: {val}")
    return "\n".join(lineas) + _sufijo


def _texto_menu_modificacion_area(estado):
    idioma = estado.get("idioma", "es")
    preguntas_area = estado.get("preguntas_area", [])
    lineas = [t("bm.mod_which", idioma), f"1. {t('bm.mod_persona', idioma)}", f"2. {t('bm.mod_proyecto', idioma)}"]
    for i, q in enumerate(preguntas_area, start=3):
        lineas.append(f"{i}. {q['texto'].split(chr(10))[0][:55]}")
    lineas.append(t("bm.mod_reply_number", idioma))
    return "\n".join(lineas)


def _bloques_menu_modificacion_area(estado):
    """Menú '¿Qué respuesta quieres modificar?' como botones (value = número de opción)."""
    idioma = estado.get("idioma", "es")
    preguntas_area = estado.get("preguntas_area", [])
    opciones = [("1", t("bm.mod_persona", idioma)), ("2", t("bm.mod_proyecto", idioma))]
    for i, q in enumerate(preguntas_area, start=3):
        opciones.append((str(i), q["texto"].split(chr(10))[0][:70]))
    bloques = [{"type": "section", "text": {"type": "mrkdwn", "text": t("bm.mod_which_bold", idioma)}}]
    fila = []
    for val, label in opciones:
        fila.append({"type": "button", "text": {"type": "plain_text", "text": label[:74]},
                     "value": val, "action_id": f"mod_area_{val}"})
        if len(fila) == 5:  # máximo 5 botones por bloque de acciones en Slack
            bloques.append({"type": "actions", "elements": fila}); fila = []
    if fila:
        bloques.append({"type": "actions", "elements": fila})
    return bloques


def _enviar_menu_modificacion_area(dm_channel, thread_ts, estado):
    slack_app.client.chat_postMessage(
        channel=dm_channel, thread_ts=thread_ts,
        text=t("bm.mod_which", estado.get("idioma", "es")),
        blocks=_bloques_menu_modificacion_area(estado),
    )


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
    return normalizar_nombre(texto) in {"si", "sí", "s", "ok", "okay", "confirmar", "guardar", "correcto",
                                        "yes", "y", "save", "confirm", "correct",
                                        "sim", "gravar", "correto"}


def respuesta_es_modificacion(texto):
    return normalizar_nombre(texto) in {"modificar", "cambiar", "editar", "repetir",
                                        "modify", "change", "edit", "repeat",
                                        "alterar", "mudar"}


def _es_si(texto):
    return normalizar_nombre(texto) in {"si", "sí", "s", "yes", "y", "ok", "okay", "claro", "vale", "sim"}


def _es_no(texto):
    return normalizar_nombre(texto) in {"no", "n", "nope", "nel", "nao", "não"}


_Q5_EJEMPLO = "Indica un ejemplo concreto que justifique tu valoración"

_PALABRAS_NUMERO = {"uno": "1", "dos": "2", "tres": "3", "cuatro": "4",
                    "one": "1", "two": "2", "three": "3", "four": "4"}

_sugerencias_por_usuario: dict = {}  # user_id -> [nombre, ...]

_VALORACION_CLAVES = {"q1", "mo_contribucion"}


def _bloques_valoracion(texto_pregunta: str, user_id: str, estado: dict = None) -> list:
    idioma = (estado or {}).get("idioma", "es")
    return [
        {"type": "section", "text": {"type": "mrkdwn", "text": texto_pregunta}},
        {
            "type": "actions",
            "block_id": f"blq_val_{user_id}",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": str(i)},
                    "value": str(i),
                    "action_id": f"valoracion_{i}",
                }
                for i in range(1, 5)
            ],
        },
    ] + (fila_atras("atras_negocio", "bm.back_btn", estado, idioma) if estado is not None else [])


def _bloques_area(texto: str, user_id: str = "", estado: dict = None) -> list:
    idioma = (estado or {}).get("idioma") or idioma_por_slack_id(user_id)
    return [
        {"type": "section", "text": {"type": "mrkdwn", "text": texto}},
        {
            "type": "actions",
            "block_id": f"blq_area_{user_id}" if user_id else "blq_area",
            "elements": [
                {"type": "button", "text": {"type": "plain_text", "text": t("bm.area_negocio", idioma)}, "value": "negocio", "action_id": "area_negocio"},
                {"type": "button", "text": {"type": "plain_text", "text": "MiddleOffice"}, "value": "middleoffice", "action_id": "area_middleoffice"},
                {"type": "button", "text": {"type": "plain_text", "text": "Palantir"}, "value": "palantir", "action_id": "area_palantir"},
            ],
        },
    ] + (fila_atras("atras_negocio", "bm.back_btn", estado, idioma) if estado is not None else [])


def _enviar_pregunta_texto(channel: str, thread_ts: str, texto: str, estado: dict, idioma: str = "es") -> None:
    """Envía una pregunta de texto libre, con botón '⬅️ Atrás' si hay historial al que volver."""
    if estado is not None and tiene_historial(estado):
        bloques = [{"type": "section", "text": {"type": "mrkdwn", "text": texto}}] + fila_atras("atras_negocio", "bm.back_btn", estado, idioma)
        slack_app.client.chat_postMessage(channel=channel, thread_ts=thread_ts, text=texto, blocks=bloques)
    else:
        slack_app.client.chat_postMessage(channel=channel, thread_ts=thread_ts, text=texto)


def _enviar_pedir_primer_miembro(channel: str, thread_ts: str, texto: str, estado: dict, idioma: str = "es") -> None:
    """Primer '¿a quién evalúas?' tras elegir proyecto. Incluye el botón de autoevaluación
    para quien está solo en el proyecto (única vía habilitada para autoevaluarse)."""
    elementos = [
        {
            "type": "button",
            "text": {"type": "plain_text", "text": t("bm.btn_alone_project", idioma), "emoji": True},
            "value": "solo",
            "action_id": "autoeval_solo",
        },
    ]
    if estado is not None and tiene_historial(estado):
        elementos.append(boton_atras("atras_negocio", "bm.back_btn", idioma))
    slack_app.client.chat_postMessage(
        channel=channel,
        thread_ts=thread_ts,
        text=texto,
        blocks=[
            {"type": "section", "text": {"type": "mrkdwn", "text": texto}},
            {"type": "actions", "elements": elementos},
        ],
    )


def _enviar_pregunta_situacion(channel: str, thread_ts: str, idioma: str, estado: dict) -> None:
    slack_app.client.chat_postMessage(
        channel=channel,
        thread_ts=thread_ts,
        text=t("bm.situation_q", idioma),
        blocks=[
            {"type": "section", "text": {"type": "mrkdwn", "text": t("bm.situation_q", idioma)}},
            {
                "type": "actions",
                "elements": [
                    {"type": "button", "text": {"type": "plain_text", "text": t("bm.btn_in_project", idioma), "emoji": True}, "value": "proyecto", "action_id": "situacion_proyecto"},
                    {"type": "button", "text": {"type": "plain_text", "text": t("bm.btn_in_bench", idioma), "emoji": True}, "value": "barbecho", "action_id": "situacion_barbecho"},
                ],
            },
        ] + fila_atras("atras_negocio", "bm.back_btn", estado, idioma),
    )


def _bloques_sugerencias(texto_intro: str, sugerencias: list, user_id: str) -> list:
    bloques = [{"type": "section", "text": {"type": "mrkdwn", "text": texto_intro}}]
    if sugerencias:
        bloques.append({
            "type": "actions",
            "block_id": f"blq_sug_{user_id}",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": nombre},
                    "value": nombre,
                    "action_id": f"sugerencia_{i}",
                }
                for i, nombre in enumerate(sugerencias)
            ],
        })
    return bloques


def _aplicar_respuesta_valoracion(user_id: str, valor: str):
    """Aplica la valoración al estado y devuelve (accion, texto_siguiente)."""
    with lock:
        estado = conversaciones.get(user_id)
        if not estado:
            return None, None
        modo = estado.get("modo")
        todas = estado.get("preguntas_area", [])

        # Modificación puntual de una valoración desde el menú de "modificar"
        if modo == "modificando_respuesta_area":
            campo = estado.get("campo_modificando")
            if campo not in _VALORACION_CLAVES:
                return None, None
            estado["respuestas"][campo] = valor
            estado.pop("campo_modificando", None)
            estado["modo"] = "confirmacion"
            return "mostrar_resumen", resumen_respuestas(
                estado["respuestas"],
                area=estado.get("area", "negocio"), idioma=estado["idioma"],
                preguntas_area=todas,
                tras_modificacion=True,
            )

        if modo != "preguntando_area_secuencial":
            return None, None
        idx = estado.get("pregunta_actual", 0)
        if idx >= len(todas) or todas[idx]["clave"] not in _VALORACION_CLAVES:
            return None, None
        push_historial(estado)
        estado["respuestas"][todas[idx]["clave"]] = valor
        idx += 1
        estado["pregunta_actual"] = idx
        if idx < len(todas):
            return "preguntar", todas[idx]["texto"]
        estado["modo"] = "confirmacion"
        return "mostrar_resumen", resumen_respuestas(
            estado["respuestas"],
            area=estado.get("area", "negocio"), idioma=estado["idioma"],
            preguntas_area=todas,
        )


@slack_app.action(re.compile(r"^valoracion_[1-4]$"))
def _handle_valoracion_interactiva(ack, body, client, logger):
    ack()
    try:
        user_id = body["user"]["id"]
        valor = body["actions"][0]["value"]
        _idi = idioma_por_slack_id(user_id)
        channel = body["channel"]["id"]
        msg = body.get("message", {})
        thread_ts = msg.get("thread_ts") or msg.get("ts", "")
        try:
            client.chat_update(
                channel=channel,
                ts=msg["ts"],
                blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": t("bm.rating_updated", _idi, v=valor)}}],
                text=t("bm.rating_fallback", _idi, v=valor),
            )
        except Exception:
            logger.warning("No se pudo actualizar el mensaje de valoración interactiva")
        accion, texto = _aplicar_respuesta_valoracion(user_id, valor)
        with lock:
            estado = conversaciones.get(user_id)
        if accion == "mostrar_resumen" and texto:
            _enviar_resumen_con_botones(channel, thread_ts, texto, _idi, estado=estado)
        elif accion and texto:
            _enviar_pregunta_texto(channel, thread_ts, texto, estado, _idi)
    except Exception:
        logger.exception("Error procesando valoración interactiva")


@slack_app.action(re.compile(r"^area_(negocio|middleoffice|palantir)$"))
def _handle_area_interactiva(ack, body, client, logger):
    ack()
    try:
        user_id = body["user"]["id"]
        area_elegida = body["actions"][0]["value"]
        channel = body["channel"]["id"]
        msg = body.get("message", {})
        thread_ts = msg.get("thread_ts") or msg.get("ts", "")
        dm_channel = evaluacion_dm_canal.get(user_id, channel)

        _idi = idioma_por_slack_id(user_id)
        _AREA_DISPLAY = {"negocio": t("bm.area_negocio", _idi), "middleoffice": "MiddleOffice", "palantir": "Palantir"}
        try:
            client.chat_update(
                channel=channel,
                ts=msg["ts"],
                blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": t("bm.area_updated", _idi, v=_AREA_DISPLAY[area_elegida])}}],
                text=t("bm.area_fallback", _idi, v=_AREA_DISPLAY[area_elegida]),
            )
        except Exception:
            logger.warning("No se pudo actualizar el mensaje de área")

        accion = None
        with lock:
            es_activo = user_id in evaluaciones_dm_activas
            estado = conversaciones.get(user_id)
            if not es_activo or not estado or estado.get("modo") != "esperando_area":
                return
            push_historial(estado)
            estado["area"] = area_elegida
            if area_elegida == "middleoffice":
                estado["respuestas"]["proyecto"] = ""
                estado["modo"] = "esperando_persona"
                accion = "pedir_persona_mo"
            else:
                estado["modo"] = "esperando_situacion"
                accion = "pedir_situacion"

        if accion == "pedir_persona_mo":
            nombre_ev = obtener_nombre_por_id_usuario(user_id)
            mo_ev = obtener_evaluados_middleoffice(nombre_ev or user_id, [user_id])
            if mo_ev:
                lista = "\n".join(f"- {e}" for e in mo_ev)
                _enviar_pregunta_texto(dm_channel, thread_ts, t("bm.ask_who_list", _idi, lista=lista), estado, _idi)
            else:
                _enviar_pregunta_texto(dm_channel, thread_ts, t("bm.ask_who", _idi), estado, _idi)
        elif accion == "pedir_situacion":
            _enviar_pregunta_situacion(dm_channel, thread_ts, _idi, estado)
    except Exception:
        logger.exception("Error procesando selección de área")


@slack_app.action(re.compile(r"^situacion_(proyecto|barbecho)$"))
def _handle_situacion_interactiva(ack, body, client, logger):
    ack()
    try:
        user_id = body["user"]["id"]
        situacion = body["actions"][0]["value"]
        channel = body["channel"]["id"]
        msg = body.get("message", {})
        thread_ts = msg.get("thread_ts") or msg.get("ts", "")
        dm_channel = evaluacion_dm_canal.get(user_id, channel)

        _idi = idioma_por_slack_id(user_id)
        _SITUACION_DISPLAY = {"proyecto": t("bm.situ_proyecto", _idi), "barbecho": t("bm.situ_barbecho", _idi)}
        try:
            client.chat_update(
                channel=channel,
                ts=msg["ts"],
                blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": t("bm.situation_updated", _idi, v=_SITUACION_DISPLAY[situacion])}}],
                text=t("bm.situation_fallback", _idi, v=_SITUACION_DISPLAY[situacion]),
            )
        except Exception:
            logger.warning("No se pudo actualizar el mensaje de situación")

        with lock:
            es_activo = user_id in evaluaciones_dm_activas
            estado = conversaciones.get(user_id)
            if not es_activo or not estado or estado.get("modo") != "esperando_situacion":
                return
            push_historial(estado)
            if situacion == "proyecto":
                estado["modo"] = "esperando_proyecto"
                _enviar_pregunta_texto(dm_channel, thread_ts, t("bm.ask_project", _idi), estado, _idi)
            else:
                estado["modo"] = "esperando_labores_barbecho"
                _enviar_pregunta_texto(dm_channel, thread_ts, t("bm.ask_barbecho", _idi), estado, _idi)
    except Exception:
        logger.exception("Error procesando selección de situación")


@slack_app.action("barbecho_entregar")
def _handle_barbecho_entregar(ack, body, logger):
    ack()
    try:
        user_id = body["user"]["id"]
        msg = body.get("message", {})
        channel = body["channel"]["id"]
        thread_ts = msg.get("thread_ts") or msg.get("ts", "")
        dm_channel = evaluacion_dm_canal.get(user_id, channel)

        _idi = idioma_por_slack_id(user_id)
        try:
            slack_app.client.chat_update(
                channel=channel,
                ts=msg["ts"],
                blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": t("bm.submitted", _idi)}}],
                text=t("bm.submitted", _idi),
            )
        except Exception:
            pass

        with lock:
            es_activo = user_id in evaluaciones_dm_activas
            estado = conversaciones.get(user_id)
            if not es_activo or not estado or estado.get("modo") != "confirmacion_barbecho":
                return
            _AREA_DISPLAY = {"negocio": "Negocio", "middleoffice": "MiddleOffice", "palantir": "Palantir"}
            area_final = _AREA_DISPLAY.get(estado.get("area", "negocio"), "Negocio")
            labores = estado.get("labores_barbecho", "")
            estado["modo"] = "terminado"
            evaluaciones_dm_activas.discard(user_id)

        nombre = _nombre_real(user_id, logger)
        guardado = guardar_barbecho_en_notion(nombre, area_final, labores)
        if guardado:
            with lock:
                limpiar_historial(estado)
            quitar_pendiente("mensual", user_id)
            marcar_completada_por_slack_id(user_id, "mensual")
            _editar_dm_inicial_mensual(user_id, _idi)
            slack_app.client.chat_postMessage(channel=dm_channel, thread_ts=thread_ts, text=t("bm.barbecho_saved", _idi))
        else:
            slack_app.client.chat_postMessage(channel=dm_channel, thread_ts=thread_ts, text=t("bm.err_save_notion", _idi))
    except Exception:
        logger.exception("Error procesando barbecho_entregar")


@slack_app.action("barbecho_modificar")
def _handle_barbecho_modificar(ack, body, logger):
    ack()
    try:
        user_id = body["user"]["id"]
        msg = body.get("message", {})
        channel = body["channel"]["id"]
        thread_ts = msg.get("thread_ts") or msg.get("ts", "")
        dm_channel = evaluacion_dm_canal.get(user_id, channel)

        _idi = idioma_por_slack_id(user_id)
        try:
            slack_app.client.chat_update(
                channel=channel,
                ts=msg["ts"],
                blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": t("bm.editing", _idi)}}],
                text=t("bm.editing", _idi),
            )
        except Exception:
            pass

        with lock:
            es_activo = user_id in evaluaciones_dm_activas
            estado = conversaciones.get(user_id)
            if not es_activo or not estado or estado.get("modo") != "confirmacion_barbecho":
                return
            push_historial(estado)
            estado["modo"] = "esperando_labores_barbecho"
            estado.pop("labores_barbecho", None)

        _enviar_pregunta_texto(dm_channel, thread_ts, t("bm.rewrite_tasks", estado.get("idioma", "es")), estado, estado.get("idioma", "es"))
    except Exception:
        logger.exception("Error procesando barbecho_modificar")


@slack_app.action(re.compile(r"^sugerencia_\d+$"))
def _handle_sugerencia_interactiva(ack, body, client, logger):
    ack()
    try:
        user_id = body["user"]["id"]
        nombre_elegido = body["actions"][0]["value"]
        channel = body["channel"]["id"]
        msg = body.get("message", {})
        thread_ts = msg.get("thread_ts") or msg.get("ts", "")
        dm_channel = evaluacion_dm_canal.get(user_id, channel)

        def reply(text):
            client.chat_postMessage(channel=dm_channel, thread_ts=thread_ts, text=text)

        _idi = idioma_por_slack_id(user_id)
        try:
            client.chat_update(
                channel=channel,
                ts=msg["ts"],
                blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": t("bm.employee_selected", _idi, nombre=nombre_elegido)}}],
                text=t("bm.employee_selected_plain", _idi, nombre=nombre_elegido),
            )
        except Exception:
            logger.warning("No se pudo actualizar el mensaje de sugerencias")

        _sugerencias_por_usuario.pop(user_id, None)

        with lock:
            es_activo = user_id in evaluaciones_dm_activas
            estado = conversaciones.get(user_id)
            if not es_activo or not estado:
                return
            modo = estado.get("modo")
            if modo not in ("esperando_persona", "modificando_respuesta"):
                return
            _cargo_ev_peek = estado.get("cargo_evaluador")
            _area_peek = estado.get("area", "negocio")

        # Notion lookups fuera del lock
        _idi = idioma_por_slack_id(user_id)
        _empleado, _cargo = buscar_empleado_y_cargo(nombre_elegido)
        if not _empleado:
            reply(t("bm.not_found_full", _idi, nombre=nombre_elegido))
            return

        _cargo_evaluador = _cargo_ev_peek
        _relacion = "igual"
        _preguntas_pre = {}
        _preguntas_area_pre = []
        _mo_invalido = False

        if _area_peek == "middleoffice":
            _nombre_ev = obtener_nombre_por_id_usuario(user_id)
            _mo_evaluados = obtener_evaluados_middleoffice(_nombre_ev or "") if _nombre_ev else []
            _preguntas_area_pre = obtener_preguntas_mo(_idi)
            if _mo_evaluados and not any(normalizar_nombre(_empleado) == normalizar_nombre(e) for e in _mo_evaluados):
                _mo_invalido = True
        elif _area_peek == "palantir":
            if _cargo_ev_peek is None:
                _cargo_evaluador = obtener_cargo_por_slack_id(user_id)
            _relacion = comparar_jerarquia(_cargo_evaluador or "", _cargo or "")
            _preguntas_area_pre = obtener_preguntas_palantir(tipo_relacion(_relacion), _idi)
        else:
            if _cargo_ev_peek is None:
                _cargo_evaluador = obtener_cargo_por_slack_id(user_id)
            _relacion = comparar_jerarquia(_cargo_evaluador or "", _cargo or "")
            _preguntas_pre = obtener_preguntas_desde_notion(tipo_relacion(_relacion), _idi)

        if _mo_invalido:
            nombre_ev = obtener_nombre_por_id_usuario(user_id)
            mo_ev = obtener_evaluados_middleoffice(nombre_ev or user_id, [user_id])
            if mo_ev:
                lista = "\n".join(f"- {e}" for e in mo_ev)
                reply(t("bm.ask_who_list", _idi, lista=lista))
            else:
                reply(t("bm.ask_who", _idi))
            return

        accion = None
        pregunta = None
        with lock:
            estado = conversaciones.get(user_id)
            if not estado:
                return
            modo = estado.get("modo")

            if modo == "esperando_persona":
                proyecto_actual = estado.get("proyecto_actual", "")
                clave_ev = (normalizar_nombre(proyecto_actual), normalizar_nombre(_empleado))
                if clave_ev in estado.get("evaluados_en_sesion", set()):
                    accion = "pedir_persona"
                    pregunta = t("bm.already_evaluated", _idi, emp=_empleado, proy=proyecto_actual or '?')
                else:
                    push_historial(estado)
                    estado["respuestas"]["evaluado"] = _empleado
                    if _cargo_evaluador and _cargo_evaluador != _cargo_ev_peek:
                        estado["cargo_evaluador"] = _cargo_evaluador
                    estado["relacion_jerarquica"] = _relacion
                    _area_actual = estado.get("area", "negocio")
                    if _area_actual in ("middleoffice", "palantir"):
                        for _k in [k for k in estado["respuestas"] if k not in ("evaluado", "proyecto")]:
                            del estado["respuestas"][_k]
                        _preguntas_inyectadas = [
                            {**q, "texto": _resolver_texto_q1(q["texto"], _relacion, _empleado)}
                            if q["clave"] == "q1"
                            else q
                            for q in _preguntas_area_pre
                        ]
                        estado["preguntas_area"] = _preguntas_inyectadas
                        estado["pregunta_actual"] = 0
                        estado["modo"] = "preguntando_area_secuencial"
                        _primera = _preguntas_inyectadas[0] if _preguntas_inyectadas else None
                        if _primera and _primera["clave"] in _VALORACION_CLAVES:
                            accion = "preguntar_valoracion"
                            pregunta = _primera["texto"]
                        else:
                            accion = "preguntar"
                            pregunta = _primera["texto"] if _primera else t("bm.no_questions_area", _idi)
                    else:
                        preguntas = _preguntas_negocio(_relacion, _preguntas_pre, nombre_evaluado=_empleado)
                        for _k in [k for k in estado["respuestas"] if k not in ("evaluado", "proyecto")]:
                            del estado["respuestas"][_k]
                        estado["preguntas_area"] = preguntas
                        estado["pregunta_actual"] = 0
                        estado["modo"] = "preguntando_area_secuencial"
                        accion = "preguntar_valoracion"
                        pregunta = preguntas[0]["texto"]

            elif modo == "modificando_respuesta":
                if _cargo_evaluador and _cargo_evaluador != _cargo_ev_peek:
                    estado["cargo_evaluador"] = _cargo_evaluador
                estado["relacion_jerarquica"] = _relacion
                estado["respuestas"]["evaluado"] = _empleado
                estado.pop("campo_modificando", None)
                estado["modo"] = "confirmacion"
                accion = "mostrar_resumen"
                pregunta = resumen_respuestas(
                    estado["respuestas"],
                    area=estado.get("area", "negocio"), idioma=estado["idioma"],
                    preguntas_area=estado.get("preguntas_area"),
                )

        if accion == "preguntar_valoracion":
            client.chat_postMessage(
                channel=dm_channel,
                thread_ts=thread_ts,
                blocks=_bloques_valoracion(pregunta, user_id, estado=estado),
                text=pregunta,
            )
        elif accion == "mostrar_resumen":
            _enviar_resumen_con_botones(dm_channel, thread_ts, pregunta, estado.get("idioma", "es"), estado=estado)
        elif pregunta:
            _enviar_pregunta_texto(dm_channel, thread_ts, pregunta, estado, estado.get("idioma", "es"))
    except Exception:
        logger.exception("Error procesando sugerencia de empleado")


@slack_app.action("autoeval_solo")
def _handle_autoeval_solo(ack, body, client, logger):
    """El evaluador declara estar solo en el proyecto y se autoevalúa (autor == evaluado).
    Es la única vía habilitada para autoevaluarse; usa las mismas preguntas que evaluar a otro."""
    ack()
    try:
        user_id = body["user"]["id"]
        channel = body["channel"]["id"]
        msg = body.get("message", {})
        thread_ts = msg.get("thread_ts") or msg.get("ts", "")
        dm_channel = evaluacion_dm_canal.get(user_id, channel)

        def reply(text):
            client.chat_postMessage(channel=dm_channel, thread_ts=thread_ts, text=text)

        _idi = idioma_por_slack_id(user_id)

        with lock:
            es_activo = user_id in evaluaciones_dm_activas
            estado = conversaciones.get(user_id)
            if not es_activo or not estado or estado.get("modo") != "esperando_persona":
                return
            _cargo_ev_peek = estado.get("cargo_evaluador")
            _area_peek = estado.get("area", "negocio")
            _proyecto_peek = estado.get("proyecto_actual", "")

        _nombre_propio = obtener_nombre_por_id_usuario(user_id) or _nombre_real(user_id, logger)
        if not _nombre_propio:
            reply(t("bm.err_temp_data", _idi))
            return

        # Ya te autoevaluaste en esta sesión para este proyecto
        clave_ev = (normalizar_nombre(_proyecto_peek), normalizar_nombre(_nombre_propio))
        with lock:
            estado = conversaciones.get(user_id)
            if estado and clave_ev in estado.get("evaluados_en_sesion", set()):
                reply(t("bm.already_evaluated", _idi, emp=_nombre_propio, proy=_proyecto_peek or '?'))
                return

        # Refleja la elección en el mensaje del botón
        try:
            client.chat_update(
                channel=channel,
                ts=msg["ts"],
                blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": t("bm.self_eval_selected", _idi)}}],
                text=t("bm.self_eval_selected", _idi),
            )
        except Exception:
            logger.warning("No se pudo actualizar el mensaje de autoevaluación")

        # Carga de preguntas por área (las mismas que evaluar a un compañero);
        # la relación con uno mismo es "igual".
        _cargo_evaluador = _cargo_ev_peek
        _relacion = "igual"
        _preguntas_pre = {}
        _preguntas_area_pre = []

        if _area_peek == "palantir":
            if _cargo_ev_peek is None:
                _cargo_evaluador = obtener_cargo_por_slack_id(user_id)
            _preguntas_area_pre = obtener_preguntas_palantir(tipo_relacion(_relacion), _idi)
        elif _area_peek == "middleoffice":
            _preguntas_area_pre = obtener_preguntas_mo(_idi)
        else:
            if _cargo_ev_peek is None:
                _cargo_evaluador = obtener_cargo_por_slack_id(user_id)
            _preguntas_pre = obtener_preguntas_desde_notion(tipo_relacion(_relacion), _idi)

        accion = None
        pregunta = None
        with lock:
            estado = conversaciones.get(user_id)
            if not estado or estado.get("modo") != "esperando_persona":
                return
            push_historial(estado)
            estado["respuestas"]["evaluado"] = _nombre_propio
            estado["es_autoevaluacion"] = True
            if _cargo_evaluador and _cargo_evaluador != _cargo_ev_peek:
                estado["cargo_evaluador"] = _cargo_evaluador
            estado["relacion_jerarquica"] = _relacion
            _area_actual = estado.get("area", "negocio")
            if _area_actual in ("middleoffice", "palantir"):
                for _k in [k for k in estado["respuestas"] if k not in ("evaluado", "proyecto")]:
                    del estado["respuestas"][_k]
                _preguntas_inyectadas = [
                    {**q, "texto": _resolver_texto_q1(q["texto"], _relacion, _nombre_propio)}
                    if q["clave"] == "q1"
                    else q
                    for q in _preguntas_area_pre
                ]
                estado["preguntas_area"] = _preguntas_inyectadas
                estado["pregunta_actual"] = 0
                estado["modo"] = "preguntando_area_secuencial"
                _primera = _preguntas_inyectadas[0] if _preguntas_inyectadas else None
                if _primera and _primera["clave"] in _VALORACION_CLAVES:
                    accion = "preguntar_valoracion"
                    pregunta = _primera["texto"]
                else:
                    accion = "preguntar"
                    pregunta = _primera["texto"] if _primera else t("bm.no_questions_area", _idi)
            else:
                preguntas = _preguntas_negocio(_relacion, _preguntas_pre, nombre_evaluado=_nombre_propio)
                for _k in [k for k in estado["respuestas"] if k not in ("evaluado", "proyecto")]:
                    del estado["respuestas"][_k]
                estado["preguntas_area"] = preguntas
                estado["pregunta_actual"] = 0
                estado["modo"] = "preguntando_area_secuencial"
                accion = "preguntar_valoracion"
                pregunta = preguntas[0]["texto"]

        if accion == "preguntar_valoracion":
            client.chat_postMessage(
                channel=dm_channel,
                thread_ts=thread_ts,
                blocks=_bloques_valoracion(pregunta, user_id, estado=estado),
                text=pregunta,
            )
        elif pregunta:
            _enviar_pregunta_texto(dm_channel, thread_ts, pregunta, estado, estado.get("idioma", "es"))
    except Exception:
        logger.exception("Error procesando autoevaluación en solitario")


def _normalizar_valoracion(texto: str) -> str | None:
    """Devuelve '1'-'4' si el texto es un número válido (dígito o palabra), None si no."""
    t = texto.strip().lower()
    if t in {"1", "2", "3", "4"}:
        return t
    return _PALABRAS_NUMERO.get(t)


def _pregunta_contribucion(relacion: str, nombre_evaluado: str = "") -> str:
    if relacion == "inferior":
        sujeto = "del Project Leader"
    else:
        sujeto = f"de {nombre_evaluado}" if nombre_evaluado else "de tu compañero"
    return f"¿Cómo valorarías del 1 al 4 la contribución {sujeto} al buen avance del proyecto?"


def _es_q1_texto_default(texto: str) -> bool:
    return not texto or texto.startswith("Este mes") or "Puedes considerar claridad" in texto


def _resolver_texto_q1(texto: str, relacion: str, nombre: str) -> str:
    if _es_q1_texto_default(texto):
        return _pregunta_contribucion(relacion, nombre)
    if "{nombre}" in texto:
        nombre_resuelto = nombre if relacion != "inferior" else "el Project Leader"
        return texto.replace("{nombre}", nombre_resuelto)
    return texto


def _preguntas_negocio(relacion: str, preguntas_notion: dict = None, nombre_evaluado: str = "") -> list:
    pn = preguntas_notion or {}
    nocion_q1 = pn.get("q1", "")
    texto_q1 = _resolver_texto_q1(nocion_q1, relacion, nombre_evaluado)
    return [
        {"clave": "q1", "texto": texto_q1},
        {"clave": "q2", "texto": pn.get("q2") or _Q5_EJEMPLO},
    ]


def _es_valor_satisfaccion(texto):
    try:
        return int(texto) in {1, 2, 3, 4}
    except Exception:
        return False


def _parece_saludo(texto):
    return normalizar_nombre(texto).strip(" ?!¡¿.") in {"hola", "buenas", "hey", "ei"}


def _mensaje_empleado_no_encontrado(texto, idioma="es", excluir=None):
    sugerencias = sugerir_empleados_parecidos(texto, excluir=excluir)
    if sugerencias:
        return t("bm.not_found_suggest", idioma, nombre=texto), sugerencias
    return t("bm.not_found", idioma, nombre=texto), []


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


def _enviar_mas_proyectos(channel, thread_ts, idioma="es", estado: dict = None):
    texto = t("bm.more_projects_send", idioma)
    elementos = [
        {
            "type": "button",
            "text": {"type": "plain_text", "text": t("bm.yes_btn", idioma), "emoji": True},
            "style": "primary",
            "action_id": "proyecto_proyectos_si",
        },
        {
            "type": "button",
            "text": {"type": "plain_text", "text": t("bm.no_btn", idioma), "emoji": True},
            "action_id": "proyecto_proyectos_no",
        },
    ]
    if estado is not None and tiene_historial(estado):
        elementos.append(boton_atras("atras_negocio", "bm.back_btn", idioma))
    slack_app.client.chat_postMessage(
        channel=channel,
        thread_ts=thread_ts,
        text=texto,
        blocks=[
            {"type": "section", "text": {"type": "mrkdwn", "text": texto}},
            {"type": "actions", "elements": elementos},
        ],
    )


def _enviar_mas_miembros(channel, thread_ts, idioma="es", estado: dict = None):
    texto = t("bm.saved_more_members", idioma)
    elementos = [
        {
            "type": "button",
            "text": {"type": "plain_text", "text": t("bm.yes_btn", idioma), "emoji": True},
            "style": "primary",
            "action_id": "proyecto_mas_si",
        },
        {
            "type": "button",
            "text": {"type": "plain_text", "text": t("bm.no_btn", idioma), "emoji": True},
            "action_id": "proyecto_mas_no",
        },
    ]
    if estado is not None and tiene_historial(estado):
        elementos.append(boton_atras("atras_negocio", "bm.back_btn", idioma))
    slack_app.client.chat_postMessage(
        channel=channel,
        thread_ts=thread_ts,
        text=texto,
        blocks=[
            {"type": "section", "text": {"type": "mrkdwn", "text": texto}},
            {"type": "actions", "elements": elementos},
        ],
    )


def _enviar_boton_modificar(channel: str, thread_ts: str, idioma="es") -> None:
    texto = t("bm.edit_window_notice", idioma)
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
                        "text": {"type": "plain_text", "text": t("bm.edit_answers_btn", idioma), "emoji": True},
                        "action_id": "proyecto_modificar_eval",
                    }
                ],
            },
        ],
    )


def _enviar_lista_modificar(channel: str, thread_ts: str, evaluaciones: list, idioma="es") -> None:
    texto = t("bm.whose_to_edit", idioma)
    botones = [
        {
            "type": "button",
            "text": {"type": "plain_text", "text": f"{ev['evaluado']} — {ev['proyecto']}"},
            "action_id": f"proyecto_sel_mod_{i}",
            "value": ev["page_id"],
        }
        for i, ev in enumerate(evaluaciones)
    ]
    slack_app.client.chat_postMessage(
        channel=channel,
        thread_ts=thread_ts,
        text=texto,
        blocks=[
            {"type": "section", "text": {"type": "mrkdwn", "text": texto}},
            {"type": "actions", "elements": botones},
        ],
    )


def _enviar_pregunta_mas_modificaciones(channel: str, thread_ts: str, idioma="es") -> None:
    texto = t("bm.answers_updated_more", idioma)
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
                        "action_id": "proyecto_modif_mas_si",
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": t("bm.no_btn", idioma), "emoji": True},
                        "action_id": "proyecto_modif_mas_no",
                    },
                ],
            },
        ],
    )


def _enviar_resumen_con_botones(channel, thread_ts, text, idioma="es", estado: dict = None):
    elementos = [
        {
            "type": "button",
            "text": {"type": "plain_text", "text": t("bm.save_yes_btn", idioma), "emoji": True},
            "style": "primary",
            "action_id": "proyecto_confirmar",
        },
        {
            "type": "button",
            "text": {"type": "plain_text", "text": t("bm.edit_btn", idioma), "emoji": True},
            "action_id": "proyecto_modificar",
        },
    ]
    if estado is not None and tiene_historial(estado):
        elementos.append(boton_atras("atras_negocio", "bm.back_btn", idioma))
    slack_app.client.chat_postMessage(
        channel=channel,
        thread_ts=thread_ts,
        text=text,
        blocks=[
            {"type": "section", "text": {"type": "mrkdwn", "text": text}},
            {"type": "actions", "elements": elementos},
        ],
    )


def _reenviar_pregunta_actual_negocio(estado: dict, dm_channel: str, thread_ts: str, user_id: str) -> None:
    """Reenvía la pregunta correspondiente al modo ya restaurado tras pulsar 'Atrás'."""
    idi = estado.get("idioma", "es")
    modo = estado.get("modo")
    if modo == "esperando_area":
        slack_app.client.chat_postMessage(
            channel=dm_channel, thread_ts=thread_ts,
            blocks=_bloques_area(t("bm.ask_area_q", idi), user_id, estado=estado),
            text=t("bm.ask_area_q", idi),
        )
    elif modo == "esperando_situacion":
        _enviar_pregunta_situacion(dm_channel, thread_ts, idi, estado)
    elif modo == "esperando_proyecto":
        _enviar_pregunta_texto(dm_channel, thread_ts, t("bm.ask_project_long", idi), estado, idi)
    elif modo == "esperando_labores_barbecho":
        _enviar_pregunta_texto(dm_channel, thread_ts, t("bm.ask_barbecho", idi), estado, idi)
    elif modo == "esperando_persona":
        if estado.get("area") == "middleoffice":
            nombre_ev = obtener_nombre_por_id_usuario(user_id)
            mo_ev = obtener_evaluados_middleoffice(nombre_ev or user_id, [user_id])
            texto = t("bm.ask_who_list", idi, lista="\n".join(f"- {e}" for e in mo_ev)) if mo_ev else t("bm.ask_who", idi)
            _enviar_pregunta_texto(dm_channel, thread_ts, texto, estado, idi)
        else:
            _enviar_pregunta_texto(dm_channel, thread_ts, t("bm.which_member", idi), estado, idi)
    elif modo == "preguntando_area_secuencial":
        todas = estado.get("preguntas_area", [])
        idx = estado.get("pregunta_actual", 0)
        if idx < len(todas):
            pregunta_actual = todas[idx]
            if pregunta_actual["clave"] in _VALORACION_CLAVES:
                slack_app.client.chat_postMessage(
                    channel=dm_channel, thread_ts=thread_ts,
                    blocks=_bloques_valoracion(pregunta_actual["texto"], user_id, estado=estado),
                    text=pregunta_actual["texto"],
                )
            else:
                _enviar_pregunta_texto(dm_channel, thread_ts, pregunta_actual["texto"], estado, idi)
    elif modo == "confirmacion":
        texto = resumen_respuestas(
            estado.get("respuestas", {}),
            area=estado.get("area", "negocio"), idioma=idi,
            preguntas_area=estado.get("preguntas_area"),
        )
        _enviar_resumen_con_botones(dm_channel, thread_ts, texto, idi, estado=estado)
    elif modo == "preguntar_mas_personas":
        _enviar_mas_miembros(dm_channel, thread_ts, idi, estado=estado)
    elif modo == "preguntar_mas_proyectos":
        _enviar_mas_proyectos(dm_channel, thread_ts, idi, estado=estado)
    elif modo == "confirmacion_barbecho":
        labores = estado.get("labores_barbecho", "")
        texto_resumen = t("bm.barbecho_summary", idi, labores=labores)
        elementos = [
            {"type": "button", "text": {"type": "plain_text", "text": t("bm.btn_submit", idi), "emoji": True}, "style": "primary", "action_id": "barbecho_entregar"},
            {"type": "button", "text": {"type": "plain_text", "text": t("bm.btn_edit", idi), "emoji": True}, "action_id": "barbecho_modificar"},
        ]
        if tiene_historial(estado):
            elementos.append(boton_atras("atras_negocio", "bm.back_btn", idi))
        slack_app.client.chat_postMessage(
            channel=dm_channel, thread_ts=thread_ts, text=texto_resumen,
            blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": texto_resumen}}, {"type": "actions", "elements": elementos}],
        )


@slack_app.action("atras_negocio")
def _handle_negocio_atras(ack, body, client, logger):
    ack()
    try:
        user_id = body["user"]["id"]
        channel = body["channel"]["id"]
        msg = body.get("message", {})
        thread_ts = msg.get("thread_ts") or msg.get("ts", "")
        dm_channel = evaluacion_dm_canal.get(user_id, channel)
        idi = idioma_por_slack_id(user_id)
        try:
            client.chat_update(
                channel=channel,
                ts=msg["ts"],
                blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": t("bm.back_done", idi)}}],
                text=t("bm.back_done", idi),
            )
        except Exception:
            logger.warning("No se pudo actualizar el mensaje al volver atrás (evaluación mensual)")

        with lock:
            estado = conversaciones.get(user_id)
            if not estado or not pop_historial(estado):
                return
        _reenviar_pregunta_actual_negocio(estado, dm_channel, thread_ts, user_id)
    except Exception:
        logger.exception("Error procesando atrás en evaluación mensual")


# (channel, ts) -> original_event: mensajes de audio esperando transcripción
_audio_pendiente: dict = {}


def _despachar_con_texto(original_event, texto, logger):
    """Enruta un evento con el texto ya resuelto (usado tras transcripción de audio)."""
    nuevo_evento = dict(original_event, text=texto)
    user_id = nuevo_evento.get("user")
    thread_ts = nuevo_evento.get("thread_ts")
    channel = nuevo_evento.get("channel", "")

    slack_app.client.chat_postMessage(
        channel=channel,
        thread_ts=thread_ts,
        text=f"📝 _{texto}_",
    )

    if thread_ts == ca_dm_ts.get(user_id):
        manejar_mensaje_ca(nuevo_evento, logger)
    elif thread_ts == personal_dm_ts.get(user_id):
        manejar_mensaje_personal(nuevo_evento, logger)
    elif thread_ts == evaluacion_dm_ts.get(user_id):
        handle_message_events(nuevo_evento, logger)


def _timeout_transcripcion(channel, thread_ts, msg_ts, logger):
    """Hilo de fondo: si tras 3 min no llega message_changed, avisa al usuario."""
    time.sleep(180)
    if _audio_pendiente.pop((channel, msg_ts), None) is not None:
        slack_app.client.chat_postMessage(
            channel=channel,
            thread_ts=thread_ts,
            text="No he podido transcribir el audio. Por favor escribe tu respuesta en texto.",
        )


def _gestionar_audio(event, channel, thread_ts, logger):
    """Detecta si el mensaje tiene audio y lo gestiona.
    Devuelve el evento con texto inyectado (si ya estaba listo),
    None si registramos espera vía message_changed,
    o el evento original si no hay audio.
    """
    files = event.get("files") or []
    if not files or (event.get("text") or "").strip():
        return event

    audio_file = next(
        (f for f in files if (f.get("mimetype") or "").startswith("audio/")),
        None,
    )
    if audio_file is None:
        return event

    t = audio_file.get("transcription") or {}
    if t.get("status") == "complete":
        texto = ((t.get("preview") or {}).get("content") or "").strip()
        if texto:
            slack_app.client.chat_postMessage(
                channel=channel,
                thread_ts=thread_ts,
                text=f"📝 _{texto}_",
            )
            return dict(event, text=texto)

    # Transcripción no disponible aún: pedimos al usuario que la active en Slack
    msg_ts = event.get("ts")
    _audio_pendiente[(channel, msg_ts)] = event
    estado = (audio_file.get("transcription") or {}).get("status", "none")
    if estado == "none":
        texto_aviso = (
            "He recibido tu audio. Para que pueda leerlo, pulsa *\"Ver transcripción\"* "
            "justo a la derecha del audio y mándame el mensaje."
        )
    else:
        texto_aviso = "⏳ Transcribiendo tu audio..."
    slack_app.client.chat_postMessage(
        channel=channel,
        thread_ts=thread_ts,
        text=texto_aviso,
    )
    threading.Thread(
        target=_timeout_transcripcion,
        args=(channel, thread_ts, msg_ts, logger),
        daemon=True,
    ).start()
    return None


def _procesar_message_changed(event, logger):
    """Gestiona message_changed: resuelve transcripciones de audio pendientes."""
    msg = event.get("message") or {}
    channel = event.get("channel", "")
    ts = msg.get("ts")

    original_event = _audio_pendiente.get((channel, ts))
    if not original_event:
        return

    for f in (msg.get("files") or []):
        if (f.get("mimetype") or "").startswith("audio/"):
            t = f.get("transcription") or {}
            if t.get("status") == "complete":
                texto = ((t.get("preview") or {}).get("content") or "").strip()
                if texto:
                    _audio_pendiente.pop((channel, ts), None)
                    _despachar_con_texto(original_event, texto, logger)
            return


@slack_app.event({"type": "message", "subtype": "message_changed"})
def handle_message_changed(event, logger):
    _procesar_message_changed(event, logger)


@slack_app.event("message")
def handle_message_events(event, logger):
    if event.get("subtype") in ("message_deleted", "bot_message"):
        return

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
            text=t("bm.no_reply_outside", idioma_por_slack_id(user_id)),
        )
        return

    event = _gestionar_audio(event, channel, thread_ts, logger)
    if event is None:
        return  # audio pendiente, hilo de fondo espera la transcripción

    if thread_ts == ca_dm_ts.get(user_id):
        manejar_mensaje_ca(event, logger)
        return

    if thread_ts == personal_dm_ts.get(user_id):
        manejar_mensaje_personal(event, logger)
        return

    if thread_ts != evaluacion_dm_ts.get(user_id):
        # El usuario ha escrito en un hilo que NO es su evaluación mensual activa.
        # Los hilos anteriores de cualquiera de las 3 evaluaciones (mensual, personal
        # o CA) caen aquí. Si tiene CUALQUIER evaluación activa, este hilo es una
        # evaluación antigua → avisamos de que ha caducado.
        with lock:
            # Solo caduca si el hilo es la evaluación ANTERIOR de un tipo del que ya
            # tienes una nueva ACTIVA (mismo tipo). No cruza tipos.
            caducada = (
                (thread_ts == evaluacion_dm_ts_anterior.get(user_id) and user_id in evaluaciones_dm_activas)
                or (thread_ts == personal_dm_ts_anterior.get(user_id) and user_id in personal_dm_activas)
                or (thread_ts == ca_dm_ts_anterior.get(user_id) and user_id in ca_dm_activas)
            )
        if caducada:
            slack_app.client.chat_postMessage(
                channel=channel,
                thread_ts=thread_ts,
                text=t("bm.thread_not_eval", idioma_por_slack_id(user_id)),
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
            _modo_sos = (conversaciones.get(user_id) or {}).get("modo")
        if _modo_sos == "terminado":
            reply(t("bm.already_concluded", idioma_por_slack_id(user_id)))
            return
        with lock:
            conversaciones.pop(user_id, None)
        reply(t("bm.eval_cancelled", idioma_por_slack_id(user_id)))
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
    _autoevaluacion_pre = False

    _necesita_busqueda = (
        (_modo_peek == "esperando_persona" and texto and not _parece_saludo(texto))
        or (_modo_peek == "modificando_respuesta" and _campo_peek == "evaluado" and texto)
    )
    if _necesita_busqueda:
        # Buscar el empleado y sus preguntas en Notion puede tardar: barra de carga animada.
        with AnimacionCargando(dm_channel, thread_ts, idioma_por_slack_id(user_id)):
            try:
                # Resolver selección numérica de sugerencias previas
                _sugerencias_actuales = _sugerencias_por_usuario.get(user_id, [])
                texto_busqueda = texto
                if texto.strip().isdigit() and _sugerencias_actuales:
                    idx = int(texto.strip()) - 1
                    if 0 <= idx < len(_sugerencias_actuales):
                        texto_busqueda = _sugerencias_actuales[idx]

                _nombre_evaluador = obtener_nombre_por_id_usuario(user_id)
                if _area_peek == "middleoffice":
                    _mo_evaluados = obtener_evaluados_middleoffice(_nombre_evaluador or "") if _nombre_evaluador else []
                _empleado_pre, _cargo_pre = buscar_empleado_y_cargo(texto_busqueda)
                if _empleado_pre and _nombre_evaluador and normalizar_nombre(_empleado_pre) == normalizar_nombre(_nombre_evaluador):
                    # No permitir que el evaluador se evalúe a sí mismo.
                    _autoevaluacion_pre = True
                    _empleado_pre = None
                    _cargo_pre = None
                    _sugerencias_por_usuario.pop(user_id, None)
                elif _empleado_pre:
                    _sugerencias_por_usuario.pop(user_id, None)
                    if _area_peek == "middleoffice":
                        _preguntas_area_pre = obtener_preguntas_mo(idioma_por_slack_id(user_id))
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
                        _preguntas_area_pre = obtener_preguntas_palantir(tipo_relacion(_relacion_pre), idioma_por_slack_id(user_id))
                    else:
                        if _cargo_ev_peek is None:
                            _cargo_evaluador_pre = obtener_cargo_por_slack_id(user_id)
                        _relacion_pre = comparar_jerarquia(_cargo_evaluador_pre or "", _cargo_pre or "")
                        _preguntas_pre = obtener_preguntas_desde_notion(tipo_relacion(_relacion_pre), idioma_por_slack_id(user_id))
                else:
                    if _area_peek == "middleoffice":
                        _mo_invalido = True
                    else:
                        _invalido_pre, _nuevas_sugerencias = _mensaje_empleado_no_encontrado(texto_busqueda, idioma_por_slack_id(user_id), excluir=_nombre_evaluador)
                        _sugerencias_por_usuario[user_id] = _nuevas_sugerencias
            except Exception:
                logger.exception("Error en Notion al buscar empleado")
                reply(t("bm.err_temp_data", idioma_por_slack_id(user_id)))
                return

    # Comprobar si ya completó la evaluación en este ciclo (solo para conversaciones nuevas)
    _ya_respondio = False
    _area_notion_pre = None
    if _modo_peek == "pre_inicial":
        # Primer mensaje del hilo: barra de carga mientras preparamos la respuesta.
        with AnimacionCargando(dm_channel, thread_ts, idioma_por_slack_id(user_id)):
            try:
                _nombre_ya = _nombre_real(user_id, logger)
                _hora_env = evaluacion_hora.get(user_id, 0)
                if _hora_env:
                    _ya_respondio = evaluacion_proyecto_guardada_desde(_nombre_ya, _hora_env)
            except Exception:
                logger.exception("Error comprobando si ya respondió en este ciclo")
            try:
                # El área (Negocio/Palantir/MiddleOffice) ya está en la Lista de Empleados
                # de Notion, así que no hace falta preguntarla si Notion la tiene.
                _area_notion_pre = obtener_area_por_slack_id(user_id)
            except Exception:
                logger.exception("Error consultando el área del empleado en Notion")

    # Máquina de estados en un único bloque con lock
    with lock:
        estado = conversaciones.get(user_id)
        if estado is None:
            estado = {
                "modo": "pre_inicial",
                "respuestas": {},
                "proyecto_actual": None,
                "evaluados_en_sesion": set(),
                "idioma": idioma_por_slack_id(user_id),
            }
            conversaciones[user_id] = estado

        modo = estado.get("modo")
        accion = None
        pregunta = None

        if modo == "pre_inicial":
            if _area_notion_pre:
                # Área ya conocida por Notion (Lista de Empleados): nos saltamos la pregunta.
                estado["area"] = _area_notion_pre
                if _area_notion_pre == "middleoffice":
                    estado["respuestas"]["proyecto"] = ""
                    estado["modo"] = "esperando_persona"
                    accion = "pedir_persona_mo"
                else:
                    estado["modo"] = "esperando_situacion"
                    accion = "pedir_situacion"
            else:
                estado["modo"] = "esperando_area"
                accion = "pedir_area"
                pregunta = t("bm.ask_area_q", estado["idioma"])

        elif modo == "esperando_area":
            _AREA_MAP = {
                "1": "negocio", "uno": "negocio", "negocio": "negocio",
                "2": "middleoffice", "dos": "middleoffice", "middleoffice": "middleoffice",
                "middle office": "middleoffice", "middle": "middleoffice", "mo": "middleoffice",
                "3": "palantir", "tres": "palantir", "palantir": "palantir",
            }
            _area_elegida = _AREA_MAP.get(normalizar_nombre(texto))
            if _area_elegida:
                push_historial(estado)
                estado["area"] = _area_elegida
                if _area_elegida == "middleoffice":
                    estado["respuestas"]["proyecto"] = ""
                    estado["modo"] = "esperando_persona"
                    accion = "pedir_persona_mo"
                else:
                    estado["modo"] = "esperando_situacion"
                    accion = "pedir_situacion"
            else:
                accion = "pedir_area"
                pregunta = t("bm.tap_area_button", estado["idioma"])

        elif modo == "esperando_situacion":
            _SITUACION_MAP = {
                "proyecto": "proyecto", "en proyecto": "proyecto",
                "barbecho": "barbecho", "en barbecho": "barbecho",
            }
            _situacion = _SITUACION_MAP.get(normalizar_nombre(texto))
            if _situacion == "proyecto":
                push_historial(estado)
                estado["modo"] = "esperando_proyecto"
                accion = "pedir_proyecto"
                pregunta = t("bm.ask_project", estado["idioma"])
            elif _situacion == "barbecho":
                push_historial(estado)
                estado["modo"] = "esperando_labores_barbecho"
                accion = "preguntar"
                pregunta = t("bm.ask_barbecho", estado["idioma"])
            else:
                accion = "pedir_situacion"

        elif modo == "esperando_labores_barbecho":
            if texto:
                push_historial(estado)
                estado["labores_barbecho"] = texto
                estado["modo"] = "confirmacion_barbecho"
                accion = "mostrar_resumen_barbecho"
                pregunta = texto
            else:
                accion = "preguntar"
                pregunta = t("bm.ask_barbecho", estado["idioma"])

        elif modo == "confirmacion_barbecho":
            if _es_si(texto) or normalizar_nombre(texto) in {"entregar", "guardar", "confirmar", "gravar", "entregar"}:
                accion = "guardar_barbecho"
            elif normalizar_nombre(texto) in {"modificar", "cambiar", "editar", "alterar", "mudar"}:
                push_historial(estado)
                estado["modo"] = "esperando_labores_barbecho"
                estado.pop("labores_barbecho", None)
                accion = "preguntar"
                pregunta = t("bm.rewrite_tasks", estado["idioma"])
            else:
                accion = "mostrar_resumen_barbecho"
                pregunta = estado.get("labores_barbecho", "")

        elif modo == "esperando_proyecto":
            if texto:
                push_historial(estado)
                estado["respuestas"]["proyecto"] = texto
                estado["proyecto_actual"] = texto
                estado["modo"] = "esperando_persona"
                accion = "pedir_primer_miembro"
                pregunta = t("bm.project_ok", estado["idioma"], proy=texto)
            else:
                accion = "pedir_proyecto"
                pregunta = t("bm.ask_project_long", estado["idioma"])

        elif modo == "esperando_persona":
            if texto:
                if _parece_saludo(texto):
                    if estado.get("area") == "middleoffice":
                        accion = "pedir_persona_mo"
                    else:
                        accion = "pedir_persona"
                        pregunta = t("bm.still_here", estado["idioma"])
                elif _autoevaluacion_pre:
                    accion = "pedir_persona"
                    pregunta = t("bm.self_eval", estado["idioma"])
                elif _mo_invalido:
                    accion = "pedir_persona_mo"
                elif _empleado_pre:
                    proyecto_actual = estado.get("proyecto_actual", "")
                    clave_ev = (normalizar_nombre(proyecto_actual), normalizar_nombre(_empleado_pre))
                    if clave_ev in estado.get("evaluados_en_sesion", set()):
                        accion = "pedir_persona"
                        pregunta = t("bm.already_evaluated", estado["idioma"], emp=_empleado_pre, proy=proyecto_actual or '?')
                    else:
                        push_historial(estado)
                        estado["respuestas"]["evaluado"] = _empleado_pre
                        if _cargo_evaluador_pre and _cargo_evaluador_pre != _cargo_ev_peek:
                            estado["cargo_evaluador"] = _cargo_evaluador_pre
                        estado["relacion_jerarquica"] = _relacion_pre
                        _area_actual = estado.get("area", "negocio")
                        if _area_actual in ("middleoffice", "palantir"):
                            for _k in [k for k in estado["respuestas"] if k not in ("evaluado", "proyecto")]:
                                del estado["respuestas"][_k]
                            _preguntas_inyectadas = [
                                {**q, "texto": _resolver_texto_q1(q["texto"], _relacion_pre, _empleado_pre)}
                                if q["clave"] == "q1"
                                else q
                                for q in _preguntas_area_pre
                            ]
                            estado["preguntas_area"] = _preguntas_inyectadas
                            estado["pregunta_actual"] = 0
                            estado["modo"] = "preguntando_area_secuencial"
                            _primera = _preguntas_inyectadas[0] if _preguntas_inyectadas else None
                            if _primera and _primera["clave"] in _VALORACION_CLAVES:
                                accion = "preguntar_valoracion"
                                pregunta = _primera["texto"]
                            else:
                                accion = "preguntar"
                                pregunta = _primera["texto"] if _primera else t("bm.no_questions_area", estado["idioma"])
                        else:
                            preguntas = _preguntas_negocio(estado.get("relacion_jerarquica", "igual"), _preguntas_pre, nombre_evaluado=_empleado_pre)
                            for _k in [k for k in estado["respuestas"] if k not in ("evaluado", "proyecto")]:
                                del estado["respuestas"][_k]
                            estado["preguntas_area"] = preguntas
                            estado["pregunta_actual"] = 0
                            estado["modo"] = "preguntando_area_secuencial"
                            accion = "preguntar_valoracion"
                            pregunta = preguntas[0]["texto"]
                else:
                    accion = "pedir_persona_invalida"
                    pregunta = _invalido_pre
            else:
                if estado.get("area") == "middleoffice":
                    accion = "pedir_persona_mo"
                else:
                    accion = "pedir_persona"
                    pregunta = t("bm.which_member", estado["idioma"])

        elif modo == "preguntando_area_secuencial":
            todas = estado.get("preguntas_area", [])
            idx = estado.get("pregunta_actual", 0)
            if texto and todas and idx < len(todas):
                clave_actual = todas[idx]["clave"]
                valor_normalizado = _normalizar_valoracion(texto) if clave_actual in {"q1", "mo_contribucion"} else None
                if clave_actual in {"q1", "mo_contribucion"} and valor_normalizado is None:
                    accion = "preguntar"
                    pregunta = t("bm.reply_1_4", estado["idioma"])
                else:
                    push_historial(estado)
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
                            area=estado.get("area", "negocio"), idioma=estado["idioma"],
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
                    area=estado.get("area", "negocio"), idioma=estado["idioma"],
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
                limpiar_historial(estado)
                estado["modo"] = "terminado"
                accion = "terminar"
            else:
                accion = "mostrar_resumen"
                pregunta = resumen_respuestas(
                    estado["respuestas"],
                    area=estado.get("area", "negocio"), idioma=estado["idioma"],
                    preguntas_area=estado.get("preguntas_area"),
                )

        elif modo == "modificando_respuesta":
            campo = estado.get("campo_modificando")
            if campo and texto:
                if campo == "evaluado":
                    if not _empleado_pre:
                        accion = "pedir_valor_modificacion"
                        pregunta = t("bm.self_eval", estado["idioma"]) if _autoevaluacion_pre else _invalido_pre
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
                            area=estado.get("area", "negocio"), idioma=estado["idioma"],
                            preguntas_area=estado.get("preguntas_area"),
                            tras_modificacion=True,
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
                        area=estado.get("area", "negocio"), idioma=estado["idioma"],
                        preguntas_area=estado.get("preguntas_area"),
                        tras_modificacion=True,
                    )
            else:
                accion = "pedir_valor_modificacion"
                pregunta = texto_pregunta_por_clave(campo) if campo else t("bm.enter_new_answer", estado["idioma"])

        elif modo == "seleccionando_modificacion_area":
            campo = _clave_modificacion_area(texto, estado)
            if campo:
                estado["campo_modificando"] = campo
                if campo == "evaluado":
                    estado["modo"] = "modificando_respuesta"
                    accion = "pedir_valor_modificacion"
                    pregunta = t("bm.enter_person", estado["idioma"])
                elif campo == "proyecto":
                    estado["modo"] = "modificando_respuesta_area"
                    accion = "pedir_valor_modificacion"
                    pregunta = t("bm.enter_new_project", estado["idioma"])
                else:
                    todas = estado.get("preguntas_area", [])
                    pregunta = next((q["texto"] for q in todas if q["clave"] == campo), t("bm.enter_new_answer", estado["idioma"]))
                    estado["modo"] = "modificando_respuesta_area"
                    accion = "preguntar_valoracion" if campo in _VALORACION_CLAVES else "pedir_valor_modificacion"
            else:
                _max_opcion = 2 + len(estado.get("preguntas_area", []))
                accion = "pedir_modificacion"
                pregunta = t("bm.reply_1_n", estado["idioma"], max=_max_opcion)

        elif modo == "modificando_respuesta_area":
            campo = estado.get("campo_modificando")
            if campo and texto:
                if campo in {"q1", "mo_contribucion"}:
                    valor_norm = _normalizar_valoracion(texto)
                    if valor_norm is None:
                        accion = "pedir_valor_modificacion"
                        todas = estado.get("preguntas_area", [])
                        pregunta_base = next((q["texto"] for q in todas if q["clave"] == campo), "")
                        pregunta = t("bm.reply_1_4", estado["idioma"])
                    else:
                        estado["respuestas"][campo] = valor_norm
                        estado.pop("campo_modificando", None)
                        estado["modo"] = "confirmacion"
                        accion = "mostrar_resumen"
                        pregunta = resumen_respuestas(
                            estado["respuestas"],
                            area=estado.get("area", "negocio"), idioma=estado["idioma"],
                            preguntas_area=estado.get("preguntas_area"),
                            tras_modificacion=True,
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
                        area=estado.get("area", "negocio"), idioma=estado["idioma"],
                        preguntas_area=estado.get("preguntas_area"),
                        tras_modificacion=True,
                    )
            else:
                accion = "pedir_valor_modificacion"
                pregunta = t("bm.enter_new_answer", estado["idioma"])

        elif modo == "guardar":
            accion = "guardar"

        elif modo == "preguntar_mas_personas":
            _area_mp = estado.get("area", "negocio")
            if _es_si(texto):
                push_historial(estado)
                estado["modo"] = "esperando_persona"
                if _area_mp == "middleoffice":
                    accion = "pedir_persona_mo"
                else:
                    accion = "pedir_persona_mismo_proyecto"
                    proyecto = estado.get("proyecto_actual") or ""
                    pregunta = (
                        t("bm.ask_other_member_proj", estado["idioma"], proy=proyecto)
                        if proyecto
                        else t("bm.ask_other_member", estado["idioma"])
                    )
            elif _es_no(texto):
                if _area_mp == "middleoffice":
                    estado["modo"] = "terminado"
                    accion = "terminar"
                else:
                    push_historial(estado)
                    estado["modo"] = "preguntar_mas_proyectos"
                    accion = "pedir_mas_proyectos"
                    pregunta = t("bm.more_projects_q", estado["idioma"])
            else:
                accion = "pedir_mas_personas"
                pregunta = t("bm.reply_yes_no_persons", estado["idioma"])

        elif modo == "preguntar_mas_proyectos":
            if _es_si(texto):
                push_historial(estado)
                estado["modo"] = "esperando_proyecto"
                estado["proyecto_actual"] = None
                accion = "pedir_proyecto"
                pregunta = t("bm.ask_project_more", estado["idioma"])
            elif _es_no(texto):
                estado["modo"] = "terminado"
                accion = "terminar"
            else:
                accion = "pedir_mas_proyectos"
                pregunta = t("bm.reply_yes_no_projects", estado["idioma"])

        elif modo == "terminado":
            _ahora_fin = time.time()
            _evs_fin = [e for e in (estado.get("evaluaciones_guardadas") or []) if _ahora_fin - e["ts"] <= 2 * 24 * 3600]
            if normalizar_nombre(texto) in {"modificar", "modificar respuestas", "editar", "alterar", "mudar"} and _evs_fin:
                accion = "mostrar_seleccion_modificar"
            else:
                accion = "ya_terminado"

        elif modo == "preguntar_mas_modificaciones":
            _ahora_mm = time.time()
            _evs_mm = [e for e in (estado.get("evaluaciones_guardadas") or []) if _ahora_mm - e["ts"] <= 2 * 24 * 3600]
            if _es_si(texto) and _evs_mm:
                accion = "mostrar_seleccion_modificar"
            elif _es_no(texto):
                estado["modo"] = "terminado"
                accion = "terminar_modificacion"
            else:
                accion = "preguntar"
                pregunta = t("bm.reply_yes_no", estado["idioma"])

    # Despacho de acciones — fuera del lock
    # Estas acciones pertenecen al flujo secuencial principal, por lo que sí ofrecen
    # el botón "Atrás" cuando hay historial.
    _ACCIONES_PREGUNTA = {
        "preguntar",
        "pedir_persona", "pedir_persona_mismo_proyecto",
        "pedir_proyecto", "pedir_mas_personas",
    }
    # Esta pertenece al submenú de "Modificar" tras confirmar: no ofrece "Atrás"
    # (no hay reenvío implementado para ella).
    _ACCIONES_PREGUNTA_SIN_ATRAS = {
        "pedir_valor_modificacion",
    }
    if accion == "pedir_situacion":
        _enviar_pregunta_situacion(dm_channel, thread_ts, estado["idioma"], estado)
        return
    if accion == "mostrar_resumen_barbecho":
        _idi = estado["idioma"]
        labores = pregunta or estado.get("labores_barbecho", "")
        texto_resumen = t("bm.barbecho_summary", _idi, labores=labores)
        elementos = [
            {"type": "button", "text": {"type": "plain_text", "text": t("bm.btn_submit", _idi), "emoji": True}, "style": "primary", "action_id": "barbecho_entregar"},
            {"type": "button", "text": {"type": "plain_text", "text": t("bm.btn_edit", _idi), "emoji": True}, "action_id": "barbecho_modificar"},
        ]
        if tiene_historial(estado):
            elementos.append(boton_atras("atras_negocio", "bm.back_btn", _idi))
        slack_app.client.chat_postMessage(
            channel=dm_channel,
            thread_ts=thread_ts,
            text=texto_resumen,
            blocks=[
                {"type": "section", "text": {"type": "mrkdwn", "text": texto_resumen}},
                {"type": "actions", "elements": elementos},
            ],
        )
        return
    if accion == "guardar_barbecho":
        nombre = _nombre_real(user_id, logger)
        with lock:
            _AREA_DISPLAY = {"negocio": "Negocio", "middleoffice": "MiddleOffice", "palantir": "Palantir"}
            area_final = _AREA_DISPLAY.get(estado.get("area", "negocio"), "Negocio")
            labores = estado.get("labores_barbecho", "")
            estado["modo"] = "terminado"
            evaluaciones_dm_activas.discard(user_id)
        guardado = guardar_barbecho_en_notion(nombre, area_final, labores)
        if guardado:
            with lock:
                limpiar_historial(estado)
            quitar_pendiente("mensual", user_id)
            marcar_completada_por_slack_id(user_id, "mensual")
            _editar_dm_inicial_mensual(user_id, estado["idioma"])
            reply(t("bm.barbecho_saved", estado["idioma"]))
        else:
            reply(t("bm.err_save_notion", estado["idioma"]))
        return
    if accion == "pedir_area":
        _texto_area = pregunta or t("bm.ask_area_q", estado["idioma"])
        slack_app.client.chat_postMessage(
            channel=dm_channel,
            thread_ts=thread_ts,
            blocks=_bloques_area(_texto_area, user_id, estado=estado),
            text=_texto_area,
        )
        return
    if accion == "pedir_persona_invalida":
        _sug = _sugerencias_por_usuario.get(user_id, [])
        if _sug:
            slack_app.client.chat_postMessage(
                channel=dm_channel,
                thread_ts=thread_ts,
                blocks=_bloques_sugerencias(pregunta or "", _sug, user_id) + fila_atras("atras_negocio", "bm.back_btn", estado, estado.get("idioma", "es")),
                text=pregunta or "",
            )
        else:
            _enviar_pregunta_texto(dm_channel, thread_ts, pregunta if pregunta else "", estado, estado.get("idioma", "es"))
        return
    if accion == "pedir_modificacion":
        _enviar_menu_modificacion_area(dm_channel, thread_ts, estado)
        return
    if accion in _ACCIONES_PREGUNTA_SIN_ATRAS:
        reply(pregunta if pregunta else "")
        return
    if accion == "pedir_primer_miembro":
        _enviar_pedir_primer_miembro(dm_channel, thread_ts, pregunta if pregunta else "", estado, estado.get("idioma", "es"))
        return
    if accion in _ACCIONES_PREGUNTA:
        _enviar_pregunta_texto(dm_channel, thread_ts, pregunta if pregunta else "", estado, estado.get("idioma", "es"))
        return
    if accion == "pedir_mas_proyectos":
        _enviar_mas_proyectos(dm_channel, thread_ts, estado.get("idioma", "es"), estado=estado)
        return
    if accion == "preguntar_valoracion":
        slack_app.client.chat_postMessage(
            channel=dm_channel,
            thread_ts=thread_ts,
            blocks=_bloques_valoracion(pregunta, user_id, estado=estado),
            text=pregunta,
        )
        return
    if accion == "mostrar_resumen":
        _enviar_resumen_con_botones(dm_channel, thread_ts, pregunta, estado.get("idioma", "es"), estado=estado)
        return
    if accion == "guardar":
        nombre = _nombre_real(user_id, logger)
        with lock:
            respuestas_finales = dict(estado.get("respuestas", {}))
            relacion_final = estado.get("relacion_jerarquica", "igual")
            _AREA_DISPLAY = {"negocio": "Negocio", "middleoffice": "MiddleOffice", "palantir": "Palantir"}
            area_final = _AREA_DISPLAY.get(estado.get("area", "negocio"), "Negocio")
            editando_page_id = estado.get("editando_page_id")
        if editando_page_id:
            with AnimacionCargando(dm_channel, thread_ts, estado.get("idioma", "es")):
                ok = actualizar_en_notion(editando_page_id, nombre, respuestas_finales, relacion=relacion_final, area=area_final)
            if ok:
                with lock:
                    estado.pop("editando_page_id", None)
                    estado["modo"] = "preguntar_mas_modificaciones"
                    limpiar_historial(estado)
                    for ev in estado.get("evaluaciones_guardadas", []):
                        if ev["page_id"] == editando_page_id:
                            ev["respuestas"] = dict(respuestas_finales)
                            ev["ts"] = time.time()
                            break
                _enviar_pregunta_mas_modificaciones(dm_channel, thread_ts, estado.get("idioma", "es"))
            else:
                reply(t("bm.err_update_notion", estado.get("idioma", "es")))
            return
        with AnimacionCargando(dm_channel, thread_ts, estado.get("idioma", "es")):
            page_id = guardar_en_notion(nombre, respuestas_finales, relacion=relacion_final, area=area_final)
        if page_id:
            with lock:
                clave_guardada = (
                    normalizar_nombre(respuestas_finales.get("proyecto", "")),
                    normalizar_nombre(respuestas_finales.get("evaluado", "")),
                )
                estado.setdefault("evaluados_en_sesion", set()).add(clave_guardada)
                # Si estabas solo en el proyecto (autoevaluación), no hay más miembros a
                # los que evaluar: pasamos directos a "¿algún otro proyecto?".
                _fue_autoevaluacion = estado.pop("es_autoevaluacion", False)
                estado["modo"] = "preguntar_mas_proyectos" if _fue_autoevaluacion else "preguntar_mas_personas"
                limpiar_historial(estado)
                estado.setdefault("evaluaciones_guardadas", []).append({
                    "page_id": page_id,
                    "evaluado": respuestas_finales.get("evaluado", ""),
                    "proyecto": respuestas_finales.get("proyecto", ""),
                    "ts": time.time(),
                    "preguntas_area": list(estado.get("preguntas_area", [])),
                    "relacion_jerarquica": relacion_final,
                    "area": estado.get("area", "negocio"),
                    "respuestas": dict(respuestas_finales),
                })
            quitar_pendiente("mensual", user_id)
            marcar_completada_por_slack_id(user_id, "mensual")
            if _fue_autoevaluacion:
                _enviar_mas_proyectos(dm_channel, thread_ts, estado.get("idioma", "es"), estado=estado)
            else:
                _enviar_mas_miembros(dm_channel, thread_ts, estado.get("idioma", "es"), estado=estado)
            return
        reply(t("bm.err_save_notion", estado.get("idioma", "es")))
        return
    if accion == "pedir_persona_mo":
        nombre_ev = obtener_nombre_por_id_usuario(user_id)
        mo_ev = obtener_evaluados_middleoffice(nombre_ev or user_id, [user_id])
        if mo_ev:
            lista = "\n".join(f"- {e}" for e in mo_ev)
            _enviar_pregunta_texto(dm_channel, thread_ts, t("bm.ask_who_list", estado.get("idioma", "es"), lista=lista), estado, estado.get("idioma", "es"))
        else:
            _enviar_pregunta_texto(dm_channel, thread_ts, t("bm.ask_who", estado.get("idioma", "es")), estado, estado.get("idioma", "es"))
        return
    if accion == "ya_respondido":
        reply(t("bm.already_completed", estado.get("idioma", "es")))
        return
    if accion == "terminar":
        reply(t("bm.thanks_end", estado.get("idioma", "es")))
        if estado.get("evaluaciones_guardadas"):
            _editar_dm_inicial_mensual(user_id, estado.get("idioma", "es"))
        _ahora_t = time.time()
        _evs_t = [e for e in (estado.get("evaluaciones_guardadas") or []) if _ahora_t - e["ts"] <= 2 * 24 * 3600]
        if _evs_t:
            _enviar_boton_modificar(dm_channel, thread_ts, estado.get("idioma", "es"))
        return
    if accion == "mostrar_seleccion_modificar":
        _ahora_s = time.time()
        _evs_s = [e for e in (estado.get("evaluaciones_guardadas") or []) if _ahora_s - e["ts"] <= 2 * 24 * 3600]
        if _evs_s:
            _enviar_lista_modificar(dm_channel, thread_ts, _evs_s, estado.get("idioma", "es"))
        return
    if accion == "terminar_modificacion":
        reply(t("bm.done_finished", estado.get("idioma", "es")))
        _ahora_tm = time.time()
        _evs_tm = [e for e in (estado.get("evaluaciones_guardadas") or []) if _ahora_tm - e["ts"] <= 2 * 24 * 3600]
        if _evs_tm:
            _enviar_boton_modificar(dm_channel, thread_ts, estado.get("idioma", "es"))
        return
    if accion == "ya_terminado":
        reply(t("bm.already_concluded", estado.get("idioma", "es")))
        return
    if pregunta:
        reply(pregunta)


@slack_app.action("proyecto_confirmar")
def handle_proyecto_confirmar(ack, body, logger):
    ack()
    user_id = body["user"]["id"]
    channel = body["channel"]["id"]
    msg = body.get("message", {})
    thread_ts = msg.get("thread_ts") or msg.get("ts", "")
    dm_channel = evaluacion_dm_canal.get(user_id, channel)

    def reply(text):
        slack_app.client.chat_postMessage(channel=dm_channel, thread_ts=thread_ts, text=text)

    with lock:
        es_activo = user_id in evaluaciones_dm_activas
        estado = conversaciones.get(user_id)
        if not es_activo or not estado or estado.get("modo") != "confirmacion":
            return
        estado["modo"] = "guardar"

    nombre = _nombre_real(user_id, logger)
    with lock:
        respuestas_finales = dict(estado.get("respuestas", {}))
        relacion_final = estado.get("relacion_jerarquica", "igual")
        _AREA_DISPLAY = {"negocio": "Negocio", "middleoffice": "MiddleOffice", "palantir": "Palantir"}
        area_final = _AREA_DISPLAY.get(estado.get("area", "negocio"), "Negocio")
        editando_page_id = estado.get("editando_page_id")
    if editando_page_id:
        with AnimacionCargando(dm_channel, thread_ts, estado.get("idioma", "es")):
            ok = actualizar_en_notion(editando_page_id, nombre, respuestas_finales, relacion=relacion_final, area=area_final)
        if ok:
            with lock:
                estado.pop("editando_page_id", None)
                estado["modo"] = "preguntar_mas_modificaciones"
                limpiar_historial(estado)
                for ev in estado.get("evaluaciones_guardadas", []):
                    if ev["page_id"] == editando_page_id:
                        ev["respuestas"] = dict(respuestas_finales)
                        ev["ts"] = time.time()
                        break
            _enviar_pregunta_mas_modificaciones(dm_channel, thread_ts)
        else:
            reply(t("bm.err_update_notion", estado.get("idioma", "es")))
        return
    with AnimacionCargando(dm_channel, thread_ts, estado.get("idioma", "es")):
        page_id = guardar_en_notion(nombre, respuestas_finales, relacion=relacion_final, area=area_final)
    if page_id:
        with lock:
            clave_guardada = (
                normalizar_nombre(respuestas_finales.get("proyecto", "")),
                normalizar_nombre(respuestas_finales.get("evaluado", "")),
            )
            estado.setdefault("evaluados_en_sesion", set()).add(clave_guardada)
            # Si estabas solo en el proyecto (autoevaluación), no hay más miembros a
            # los que evaluar: pasamos directos a "¿algún otro proyecto?".
            _fue_autoevaluacion = estado.pop("es_autoevaluacion", False)
            estado["modo"] = "preguntar_mas_proyectos" if _fue_autoevaluacion else "preguntar_mas_personas"
            limpiar_historial(estado)
            estado.setdefault("evaluaciones_guardadas", []).append({
                "page_id": page_id,
                "evaluado": respuestas_finales.get("evaluado", ""),
                "proyecto": respuestas_finales.get("proyecto", ""),
                "ts": time.time(),
                "preguntas_area": list(estado.get("preguntas_area", [])),
                "relacion_jerarquica": relacion_final,
                "area": estado.get("area", "negocio"),
                "respuestas": dict(respuestas_finales),
            })
        quitar_pendiente("mensual", user_id)
        marcar_completada_por_slack_id(user_id, "mensual")
        if _fue_autoevaluacion:
            _enviar_mas_proyectos(dm_channel, thread_ts, estado.get("idioma", "es"), estado=estado)
        else:
            _enviar_mas_miembros(dm_channel, thread_ts, estado.get("idioma", "es"), estado=estado)
        return
    reply(t("bm.err_save_notion", estado.get("idioma", "es")))


@slack_app.action("proyecto_mas_si")
def handle_proyecto_mas_si(ack, body):
    ack()
    user_id = body["user"]["id"]
    channel = body["channel"]["id"]
    msg = body.get("message", {})
    thread_ts = msg.get("thread_ts") or msg.get("ts", "")
    dm_channel = evaluacion_dm_canal.get(user_id, channel)

    def reply(text):
        slack_app.client.chat_postMessage(channel=dm_channel, thread_ts=thread_ts, text=text)

    with lock:
        es_activo = user_id in evaluaciones_dm_activas
        estado = conversaciones.get(user_id)
        if not es_activo or not estado or estado.get("modo") != "preguntar_mas_personas":
            return
        _area_mp = estado.get("area", "negocio")
        push_historial(estado)
        estado["modo"] = "esperando_persona"

    if _area_mp == "middleoffice":
        nombre_ev = obtener_nombre_por_id_usuario(user_id)
        mo_ev = obtener_evaluados_middleoffice(nombre_ev or user_id, [user_id])
        if mo_ev:
            lista = "\n".join(f"- {e}" for e in mo_ev)
            _enviar_pregunta_texto(dm_channel, thread_ts, t("bm.ask_who_list", estado.get("idioma", "es"), lista=lista), estado, estado.get("idioma", "es"))
        else:
            _enviar_pregunta_texto(dm_channel, thread_ts, t("bm.ask_who", estado.get("idioma", "es")), estado, estado.get("idioma", "es"))
    else:
        proyecto = estado.get("proyecto_actual") or ""
        texto = (
            t("bm.ask_other_member_proj", estado.get("idioma", "es"), proy=proyecto)
            if proyecto
            else t("bm.ask_other_member", estado.get("idioma", "es"))
        )
        _enviar_pregunta_texto(dm_channel, thread_ts, texto, estado, estado.get("idioma", "es"))


@slack_app.action("proyecto_mas_no")
def handle_proyecto_mas_no(ack, body):
    ack()
    user_id = body["user"]["id"]
    channel = body["channel"]["id"]
    msg = body.get("message", {})
    thread_ts = msg.get("thread_ts") or msg.get("ts", "")
    dm_channel = evaluacion_dm_canal.get(user_id, channel)

    def reply(text):
        slack_app.client.chat_postMessage(channel=dm_channel, thread_ts=thread_ts, text=text)

    with lock:
        es_activo = user_id in evaluaciones_dm_activas
        estado = conversaciones.get(user_id)
        if not es_activo or not estado or estado.get("modo") != "preguntar_mas_personas":
            return
        _area_mp = estado.get("area", "negocio")
        if _area_mp == "middleoffice":
            estado["modo"] = "terminado"
            _evs_mo = [e for e in (estado.get("evaluaciones_guardadas") or []) if time.time() - e["ts"] <= 2 * 24 * 3600]
        else:
            push_historial(estado)
            estado["modo"] = "preguntar_mas_proyectos"
            _evs_mo = []

    if _area_mp == "middleoffice":
        reply(t("bm.thanks_end", estado.get("idioma", "es")))
        _editar_dm_inicial_mensual(user_id, estado.get("idioma", "es"))
        if _evs_mo:
            _enviar_boton_modificar(dm_channel, thread_ts, estado.get("idioma", "es"))
    else:
        _enviar_mas_proyectos(dm_channel, thread_ts, estado.get("idioma", "es"), estado=estado)


@slack_app.action("proyecto_modificar_eval")
def handle_proyecto_modificar_eval(ack, body, logger):
    ack()
    user_id = body["user"]["id"]
    channel = body["channel"]["id"]
    msg = body.get("message", {})
    thread_ts = msg.get("thread_ts") or msg.get("ts", "")
    dm_channel = evaluacion_dm_canal.get(user_id, channel)

    def reply(text):
        slack_app.client.chat_postMessage(channel=dm_channel, thread_ts=thread_ts, text=text)

    _evs_validas = []
    with lock:
        es_activo = user_id in evaluaciones_dm_activas
        estado = conversaciones.get(user_id)
        if not es_activo or not estado:
            reply(t("bm.no_active_eval", estado.get("idioma", "es")))
            return
        _ahora = time.time()
        _evs_validas = [e for e in (estado.get("evaluaciones_guardadas") or []) if _ahora - e["ts"] <= 2 * 24 * 3600]
        if not _evs_validas:
            reply(t("bm.edit_window_expired", estado.get("idioma", "es")))
            return

    _enviar_lista_modificar(dm_channel, thread_ts, _evs_validas, estado.get("idioma", "es"))


@slack_app.action(re.compile(r"^proyecto_sel_mod_\d+$"))
def handle_proyecto_seleccionar_modificar(ack, body, logger):
    ack()
    user_id = body["user"]["id"]
    channel = body["channel"]["id"]
    msg = body.get("message", {})
    thread_ts = msg.get("thread_ts") or msg.get("ts", "")
    dm_channel = evaluacion_dm_canal.get(user_id, channel)

    def reply(text):
        slack_app.client.chat_postMessage(channel=dm_channel, thread_ts=thread_ts, text=text)

    accion_body = next((a for a in body.get("actions", []) if re.match(r"^proyecto_sel_mod_\d+$", a.get("action_id", ""))), None)
    page_id_sel = accion_body.get("value", "") if accion_body else ""

    _ev_data = None
    with lock:
        es_activo = user_id in evaluaciones_dm_activas
        estado = conversaciones.get(user_id)
        if not es_activo or not estado:
            reply(t("bm.no_active_eval_short", estado.get("idioma", "es")))
            return
        ev = next((e for e in (estado.get("evaluaciones_guardadas") or []) if e["page_id"] == page_id_sel), None)
        if not ev or time.time() - ev["ts"] > 2 * 24 * 3600:
            reply(t("bm.edit_window_expired", estado.get("idioma", "es")))
            return
        estado["editando_page_id"] = ev["page_id"]
        estado["respuestas"] = dict(ev.get("respuestas", {"evaluado": ev["evaluado"], "proyecto": ev["proyecto"]}))
        estado["preguntas_area"] = ev["preguntas_area"]
        estado["relacion_jerarquica"] = ev["relacion_jerarquica"]
        estado["area"] = ev["area"]
        estado["modo"] = "confirmacion"
        _ev_data = dict(ev)

    resumen = resumen_respuestas(
        _ev_data["respuestas"] if _ev_data.get("respuestas") else {"evaluado": _ev_data["evaluado"], "proyecto": _ev_data["proyecto"]},
        area=_ev_data["area"],
        preguntas_area=_ev_data["preguntas_area"],
        idioma=estado.get("idioma", "es"),
    )
    _enviar_resumen_con_botones(dm_channel, thread_ts, resumen, estado.get("idioma", "es"))


@slack_app.action("proyecto_modif_mas_si")
def handle_proyecto_modif_mas_si(ack, body, logger):
    ack()
    user_id = body["user"]["id"]
    channel = body["channel"]["id"]
    msg = body.get("message", {})
    thread_ts = msg.get("thread_ts") or msg.get("ts", "")
    dm_channel = evaluacion_dm_canal.get(user_id, channel)

    def reply(text):
        slack_app.client.chat_postMessage(channel=dm_channel, thread_ts=thread_ts, text=text)

    _evs_validas = []
    with lock:
        es_activo = user_id in evaluaciones_dm_activas
        estado = conversaciones.get(user_id)
        if not es_activo or not estado or estado.get("modo") != "preguntar_mas_modificaciones":
            return
        _ahora = time.time()
        _evs_validas = [e for e in (estado.get("evaluaciones_guardadas") or []) if _ahora - e["ts"] <= 2 * 24 * 3600]

    if _evs_validas:
        _enviar_lista_modificar(dm_channel, thread_ts, _evs_validas, estado.get("idioma", "es"))
    else:
        reply(t("bm.edit_window_expired", estado.get("idioma", "es")))


@slack_app.action("proyecto_modif_mas_no")
def handle_proyecto_modif_mas_no(ack, body, logger):
    ack()
    user_id = body["user"]["id"]
    channel = body["channel"]["id"]
    msg = body.get("message", {})
    thread_ts = msg.get("thread_ts") or msg.get("ts", "")
    dm_channel = evaluacion_dm_canal.get(user_id, channel)

    def reply(text):
        slack_app.client.chat_postMessage(channel=dm_channel, thread_ts=thread_ts, text=text)

    _evs_validas = []
    with lock:
        es_activo = user_id in evaluaciones_dm_activas
        estado = conversaciones.get(user_id)
        if not es_activo or not estado or estado.get("modo") != "preguntar_mas_modificaciones":
            return
        estado["modo"] = "terminado"
        _ahora = time.time()
        _evs_validas = [e for e in (estado.get("evaluaciones_guardadas") or []) if _ahora - e["ts"] <= 2 * 24 * 3600]

    reply(t("bm.done_finished", estado.get("idioma", "es")))
    if _evs_validas:
        _enviar_boton_modificar(dm_channel, thread_ts, estado.get("idioma", "es"))


@slack_app.action("proyecto_proyectos_si")
def handle_proyecto_proyectos_si(ack, body):
    ack()
    user_id = body["user"]["id"]
    channel = body["channel"]["id"]
    msg = body.get("message", {})
    thread_ts = msg.get("thread_ts") or msg.get("ts", "")
    dm_channel = evaluacion_dm_canal.get(user_id, channel)

    def reply(text):
        slack_app.client.chat_postMessage(channel=dm_channel, thread_ts=thread_ts, text=text)

    with lock:
        es_activo = user_id in evaluaciones_dm_activas
        estado = conversaciones.get(user_id)
        if not es_activo or not estado or estado.get("modo") != "preguntar_mas_proyectos":
            return
        push_historial(estado)
        estado["modo"] = "esperando_proyecto"
        estado["proyecto_actual"] = None

    _enviar_pregunta_texto(dm_channel, thread_ts, t("bm.ask_project_more", estado.get("idioma", "es")), estado, estado.get("idioma", "es"))


@slack_app.action("proyecto_proyectos_no")
def handle_proyecto_proyectos_no(ack, body):
    ack()
    user_id = body["user"]["id"]
    channel = body["channel"]["id"]
    msg = body.get("message", {})
    thread_ts = msg.get("thread_ts") or msg.get("ts", "")
    dm_channel = evaluacion_dm_canal.get(user_id, channel)

    def reply(text):
        slack_app.client.chat_postMessage(channel=dm_channel, thread_ts=thread_ts, text=text)

    with lock:
        es_activo = user_id in evaluaciones_dm_activas
        estado = conversaciones.get(user_id)
        if not es_activo or not estado or estado.get("modo") != "preguntar_mas_proyectos":
            return
        estado["modo"] = "terminado"
        _evs_pno = [e for e in (estado.get("evaluaciones_guardadas") or []) if time.time() - e["ts"] <= 2 * 24 * 3600]

    reply(t("bm.thanks_end", estado.get("idioma", "es")))
    _editar_dm_inicial_mensual(user_id, estado.get("idioma", "es"))
    if _evs_pno:
        _enviar_boton_modificar(dm_channel, thread_ts, estado.get("idioma", "es"))


@slack_app.action("proyecto_modificar")
def handle_proyecto_modificar(ack, body, logger):
    ack()
    user_id = body["user"]["id"]
    channel = body["channel"]["id"]
    msg = body.get("message", {})
    thread_ts = msg.get("thread_ts") or msg.get("ts", "")
    dm_channel = evaluacion_dm_canal.get(user_id, channel)

    def reply(text):
        slack_app.client.chat_postMessage(channel=dm_channel, thread_ts=thread_ts, text=text)

    with lock:
        es_activo = user_id in evaluaciones_dm_activas
        estado = conversaciones.get(user_id)
        if not es_activo or not estado or estado.get("modo") != "confirmacion":
            return
        estado["modo"] = "seleccionando_modificacion_area"

    _enviar_menu_modificacion_area(dm_channel, thread_ts, estado)


@slack_app.action(re.compile(r"^mod_area_\d+$"))
def _handle_mod_area_opcion(ack, body, logger):
    """Botón del menú '¿Qué respuesta quieres modificar?': reinyecta el número en el flujo normal."""
    ack()
    uid = body.get("user", {}).get("id", "")
    val = ""
    for a in body.get("actions", []):
        val = a.get("value", "") or val
    thread_ts = evaluacion_dm_ts.get(uid)
    channel = body.get("channel", {}).get("id", "") or evaluacion_dm_canal.get(uid, "")
    if not uid or not thread_ts or not channel or not val:
        return
    # Sintetiza el mensaje de texto equivalente → reutiliza todo el state-machine
    handle_message_events(
        {"channel": channel, "thread_ts": thread_ts, "user": uid, "text": val}, logger
    )


_RECORDATORIO_PROYECTO_SEGUNDOS = 7 * 24 * 60 * 60  # 1 semana


# ---------------------------------------------------------------------------
# Ejemplo de guía — modal Mensual
# ---------------------------------------------------------------------------

def _build_ejemplo_mensual_view(idioma="es") -> dict:
    ejemplos = obtener_ejemplos_guia(idioma)
    ejemplo = ejemplos.get("Mensual", t("bm.no_example", idioma))
    return {
        "type": "modal",
        "callback_id": "ejemplo_mensual_ver",
        "title": {"type": "plain_text", "text": t("bm.guide_example_title", idioma)},
        "close": {"type": "plain_text", "text": t("bm.close", idioma)},
        "blocks": [
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": t("bm.guide_example_header", idioma)},
            },
            {"type": "divider"},
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": ejemplo[:3000] if ejemplo else t("bm.no_example", idioma)},
            },
        ],
    }


def _vista_modal_cargando() -> dict:
    """Modal ligero de carga: se abre al instante para no agotar el trigger_id de Slack."""
    return {
        "type": "modal",
        "title": {"type": "plain_text", "text": "Ejemplo"},
        "close": {"type": "plain_text", "text": "Cerrar"},
        "blocks": [{"type": "section", "text": {"type": "mrkdwn", "text": "⏳ Cargando… / Loading… / A carregar…"}}],
    }


@slack_app.action("mensual_ver_ejemplo")
def _handle_mensual_ver_ejemplo(ack, body, logger):
    ack()
    trigger_id = body.get("trigger_id")
    if not trigger_id:
        return
    # Abrir modal de carga YA (sin lecturas de Notion) para no agotar el trigger_id (~3s).
    try:
        resp = slack_app.client.views_open(trigger_id=trigger_id, view=_vista_modal_cargando())
    except Exception:
        logger.exception("Error abriendo modal de ejemplo mensual")
        return
    try:
        _idi = idioma_por_slack_id(body.get("user", {}).get("id", ""))
        slack_app.client.views_update(view_id=resp["view"]["id"], view=_build_ejemplo_mensual_view(_idi))
    except Exception:
        logger.exception("Error actualizando modal de ejemplo mensual")


def _arrancar_mensual_desde_boton(body, logger, con_ejemplo):
    """Botones Sí/No del DM inicial mensual. 'Sí' publica el ejemplo en el hilo;
    ambos arrancan la evaluación inyectando el evento que antes generaba el primer
    mensaje del usuario. Si la conversación ya está en marcha, 'Sí' solo muestra
    el ejemplo y 'No' no hace nada."""
    user_id = body.get("user", {}).get("id", "")
    channel = (body.get("channel") or {}).get("id") or (body.get("container") or {}).get("channel_id")
    msg = body.get("message") or {}
    thread_ts = msg.get("thread_ts") or msg.get("ts")
    if not (user_id and channel and thread_ts):
        return
    with lock:
        es_activo = user_id in evaluaciones_dm_activas and thread_ts == evaluacion_dm_ts.get(user_id)
        estado = conversaciones.get(user_id)
        ya_empezada = estado is not None and estado.get("modo", "pre_inicial") != "pre_inicial"
    if not es_activo:
        return
    idioma = idioma_por_slack_id(user_id)
    if con_ejemplo:
        with AnimacionCargando(channel, thread_ts, idioma):
            ejemplo = obtener_ejemplos_guia(idioma).get("Mensual") or t("bm.no_example", idioma)
        slack_app.client.chat_postMessage(
            channel=channel,
            thread_ts=thread_ts,
            text=f"{t('bm.guide_example_header', idioma)}\n\n{ejemplo[:2900]}",
        )
    if ya_empezada:
        return
    handle_message_events({"user": user_id, "channel": channel, "thread_ts": thread_ts, "text": ""}, logger)


@slack_app.action("mensual_ejemplo_si")
def _handle_mensual_ejemplo_si(ack, body, logger):
    ack()
    try:
        _arrancar_mensual_desde_boton(body, logger, con_ejemplo=True)
    except Exception:
        logger.exception("Error arrancando evaluación mensual desde el botón Sí")


@slack_app.action("mensual_ejemplo_no")
def _handle_mensual_ejemplo_no(ack, body, logger):
    ack()
    try:
        _arrancar_mensual_desde_boton(body, logger, con_ejemplo=False)
    except Exception:
        logger.exception("Error arrancando evaluación mensual desde el botón No")


@slack_app.action(re.compile(r"^lang_set_mensual_(es|en|pt)$"))
def _handle_lang_set_mensual(ack, body, logger):
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
                text=t("bm.pending_fallback", nuevo),
                blocks=_bloques_dm_mensual(nuevo),
            )
    except Exception:
        logger.exception("Error cambiando idioma (mensual)")


def ciclo_recordatorios_proyecto():
    while True:
        time.sleep(30)
        ahora = time.time()
        with lock:
            pendientes = [
                uid for uid in list(evaluaciones_dm_activas)
                if ahora - max(
                    evaluacion_hora.get(uid, ahora),
                    evaluacion_ultimo_recordatorio.get(uid, 0) or evaluacion_hora.get(uid, ahora),
                ) >= _RECORDATORIO_PROYECTO_SEGUNDOS
            ]
        for uid in pendientes:
            try:
                nombre = _nombre_real(uid, logging)
                if evaluacion_proyecto_guardada_desde(nombre, evaluacion_hora.get(uid, 0)):
                    with lock:
                        evaluaciones_dm_activas.discard(uid)
                    continue
                dm_channel = evaluacion_dm_canal.get(uid)
                if not dm_channel:
                    continue
                slack_app.client.chat_postMessage(
                    channel=dm_channel,
                    text=t("bm.reminder", idioma_por_slack_id(uid)),
                )
                with lock:
                    evaluacion_ultimo_recordatorio[uid] = time.time()
            except Exception:
                logging.exception(f"Error enviando recordatorio DM a {uid}")


def start_socket_mode():
    SocketModeHandler(slack_app, config.SLACK_APP_TOKEN).start()
