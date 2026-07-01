import logging
import re
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
    evaluacion_proyecto_guardada_desde,
    guardar_barbecho_en_notion,
    actualizar_en_notion,
    guardar_en_notion,
    obtener_cargo_por_slack_id,
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
                    text="📍 Tienes una evaluación mensual pendiente",
                    blocks=[
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": (
                                    "📍 *Tienes una evaluación mensual pendiente.*\n\n"
                                    "_Esta evaluación es totalmente privada, solo podrá verla el CA de la persona evaluada._\n"
                                    "_Si en algún momento quieres cancelar, escribe SOS en el hilo._"
                                ),
                            },
                        },
                        {
                            "type": "section",
                            "text": {"type": "mrkdwn", "text": ":point_right: Ejemplo:"},
                            "accessory": {
                                "type": "button",
                                "text": {"type": "plain_text", "text": "Ver ejemplo"},
                                "action_id": "mensual_ver_ejemplo",
                            },
                        },
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": ":point_right: *Envía cualquier mensaje en el hilo para comenzar la evaluación*",
                            },
                        },
                        {"type": "divider"},
                    ],
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


def resumen_respuestas(respuestas, area="negocio", preguntas_area=None, tras_modificacion=False):
    if tras_modificacion:
        _sufijo = (
            "\n\n✅ Respuesta actualizada. ¿Quieres cambiar algo más o sigo?\n"
            "Haz click en *Modificar* para cambiar otra respuesta o en *Sí, guardar* para continuar."
        )
    else:
        _sufijo = (
            "\n\n¿Estás satisfecho con tus respuestas?\n"
            "Responde o haz click en sí para guardar en Notion o modificar para cambiar una respuesta concreta."
        )
    lineas = ["*Resumen de tus respuestas:*"]
    lineas.append(f"- *Persona evaluada*: {respuestas.get('evaluado', '')}")
    if respuestas.get("proyecto"):
        lineas.append(f"- *Proyecto*: {respuestas.get('proyecto', '')}")
    if respuestas.get("satisfaccion"):
        lineas.append(f"- *Satisfacción*: {respuestas.get('satisfaccion', '')}")
    if preguntas_area:
        for q in preguntas_area:
            val = respuestas.get(q["clave"], "")
            label = q["texto"].split("\n")[0][:55].strip()
            lineas.append(f"- *{label}*: {val}")
    return "\n".join(lineas) + _sufijo


def _texto_menu_modificacion_area(estado):
    preguntas_area = estado.get("preguntas_area", [])
    lineas = ["¿Qué respuesta quieres modificar?", "1. Persona evaluada", "2. Proyecto"]
    for i, q in enumerate(preguntas_area, start=3):
        lineas.append(f"{i}. {q['texto'].split(chr(10))[0][:55]}")
    lineas.append("\nResponde con el número.")
    return "\n".join(lineas)


def _bloques_menu_modificacion_area(estado):
    """Menú '¿Qué respuesta quieres modificar?' como botones (value = número de opción)."""
    preguntas_area = estado.get("preguntas_area", [])
    opciones = [("1", "Persona evaluada"), ("2", "Proyecto")]
    for i, q in enumerate(preguntas_area, start=3):
        opciones.append((str(i), q["texto"].split(chr(10))[0][:70]))
    bloques = [{"type": "section", "text": {"type": "mrkdwn", "text": "*¿Qué respuesta quieres modificar?*"}}]
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
        text="¿Qué respuesta quieres modificar?",
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
    return normalizar_nombre(texto) in {"si", "sí", "s", "ok", "okay", "confirmar", "guardar", "correcto"}


def respuesta_es_modificacion(texto):
    return normalizar_nombre(texto) in {"modificar", "cambiar", "editar", "repetir"}


def _es_si(texto):
    return normalizar_nombre(texto) in {"si", "sí", "s", "yes", "y", "ok", "okay", "claro", "vale"}


def _es_no(texto):
    return normalizar_nombre(texto) in {"no", "n", "nope", "nel"}


_Q5_EJEMPLO = "Indica un ejemplo concreto que justifique tu valoración"

_PALABRAS_NUMERO = {"uno": "1", "dos": "2", "tres": "3", "cuatro": "4"}

_sugerencias_por_usuario: dict = {}  # user_id -> [nombre, ...]

_VALORACION_CLAVES = {"q1", "mo_contribucion"}


def _bloques_valoracion(texto_pregunta: str, user_id: str) -> list:
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
    ]


def _bloques_area(texto: str, user_id: str = "") -> list:
    return [
        {"type": "section", "text": {"type": "mrkdwn", "text": texto}},
        {
            "type": "actions",
            "block_id": f"blq_area_{user_id}" if user_id else "blq_area",
            "elements": [
                {"type": "button", "text": {"type": "plain_text", "text": "Negocio"}, "value": "negocio", "action_id": "area_negocio"},
                {"type": "button", "text": {"type": "plain_text", "text": "MiddleOffice"}, "value": "middleoffice", "action_id": "area_middleoffice"},
                {"type": "button", "text": {"type": "plain_text", "text": "Palantir"}, "value": "palantir", "action_id": "area_palantir"},
            ],
        },
    ]


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
                area=estado.get("area", "negocio"),
                preguntas_area=todas,
                tras_modificacion=True,
            )

        if modo != "preguntando_area_secuencial":
            return None, None
        idx = estado.get("pregunta_actual", 0)
        if idx >= len(todas) or todas[idx]["clave"] not in _VALORACION_CLAVES:
            return None, None
        estado["respuestas"][todas[idx]["clave"]] = valor
        idx += 1
        estado["pregunta_actual"] = idx
        if idx < len(todas):
            return "preguntar", todas[idx]["texto"]
        estado["modo"] = "confirmacion"
        return "mostrar_resumen", resumen_respuestas(
            estado["respuestas"],
            area=estado.get("area", "negocio"),
            preguntas_area=todas,
        )


@slack_app.action(re.compile(r"^valoracion_[1-4]$"))
def _handle_valoracion_interactiva(ack, body, client, logger):
    ack()
    try:
        user_id = body["user"]["id"]
        valor = body["actions"][0]["value"]
        channel = body["channel"]["id"]
        msg = body.get("message", {})
        thread_ts = msg.get("thread_ts") or msg.get("ts", "")
        try:
            client.chat_update(
                channel=channel,
                ts=msg["ts"],
                blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": f"Valoración: *{valor} / 4* ✅"}}],
                text=f"Valoración: {valor} / 4",
            )
        except Exception:
            logger.warning("No se pudo actualizar el mensaje de valoración interactiva")
        accion, texto = _aplicar_respuesta_valoracion(user_id, valor)
        if accion == "mostrar_resumen" and texto:
            _enviar_resumen_con_botones(channel, thread_ts, texto)
        elif accion and texto:
            client.chat_postMessage(channel=channel, thread_ts=thread_ts, text=texto)
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

        def reply(text):
            client.chat_postMessage(channel=dm_channel, thread_ts=thread_ts, text=text)

        _AREA_DISPLAY = {"negocio": "Negocio", "middleoffice": "MiddleOffice", "palantir": "Palantir"}
        try:
            client.chat_update(
                channel=channel,
                ts=msg["ts"],
                blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": f"Área: *{_AREA_DISPLAY[area_elegida]}* ✅"}}],
                text=f"Área: {_AREA_DISPLAY[area_elegida]}",
            )
        except Exception:
            logger.warning("No se pudo actualizar el mensaje de área")

        accion = None
        with lock:
            es_activo = user_id in evaluaciones_dm_activas
            estado = conversaciones.get(user_id)
            if not es_activo or not estado or estado.get("modo") != "esperando_area":
                return
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
                reply(f"¿A quién quieres evaluar?\n{lista}")
            else:
                reply("¿A quién quieres evaluar? Dime el nombre de la persona.")
        elif accion == "pedir_situacion":
            client.chat_postMessage(
                channel=dm_channel,
                thread_ts=thread_ts,
                text="¿Estás actualmente en proyecto o en barbecho?",
                blocks=[
                    {"type": "section", "text": {"type": "mrkdwn", "text": "¿Estás actualmente en proyecto o en barbecho?"}},
                    {
                        "type": "actions",
                        "elements": [
                            {"type": "button", "text": {"type": "plain_text", "text": "🏗️ En proyecto"}, "value": "proyecto", "action_id": "situacion_proyecto"},
                            {"type": "button", "text": {"type": "plain_text", "text": "⏸️ En barbecho"}, "value": "barbecho", "action_id": "situacion_barbecho"},
                        ],
                    },
                ],
            )
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

        _SITUACION_DISPLAY = {"proyecto": "En proyecto 🏗️", "barbecho": "En barbecho ⏸️"}
        try:
            client.chat_update(
                channel=channel,
                ts=msg["ts"],
                blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": f"Situación: *{_SITUACION_DISPLAY[situacion]}* ✅"}}],
                text=f"Situación: {_SITUACION_DISPLAY[situacion]}",
            )
        except Exception:
            logger.warning("No se pudo actualizar el mensaje de situación")

        with lock:
            es_activo = user_id in evaluaciones_dm_activas
            estado = conversaciones.get(user_id)
            if not es_activo or not estado or estado.get("modo") != "esperando_situacion":
                return
            if situacion == "proyecto":
                estado["modo"] = "esperando_proyecto"
                client.chat_postMessage(
                    channel=dm_channel,
                    thread_ts=thread_ts,
                    text="Escribe el nombre de uno de los proyectos en los que estás trabajando. Más adelante podrás evaluar el resto",
                )
            else:
                estado["modo"] = "esperando_labores_barbecho"
                client.chat_postMessage(
                    channel=dm_channel,
                    thread_ts=thread_ts,
                    text="¿Qué labores estás realizando?",
                )
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

        try:
            slack_app.client.chat_update(
                channel=channel,
                ts=msg["ts"],
                blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": "✅ Entregado"}}],
                text="✅ Entregado",
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
            slack_app.client.chat_postMessage(channel=dm_channel, thread_ts=thread_ts, text="✅ Registrado. Muchas gracias, ya puedes salir del hilo 👋")
        else:
            slack_app.client.chat_postMessage(channel=dm_channel, thread_ts=thread_ts, text="⚠️ No se pudo guardar en Notion. Revisa permisos/logs.")
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

        try:
            slack_app.client.chat_update(
                channel=channel,
                ts=msg["ts"],
                blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": "✏️ Modificando..."}}],
                text="✏️ Modificando...",
            )
        except Exception:
            pass

        with lock:
            es_activo = user_id in evaluaciones_dm_activas
            estado = conversaciones.get(user_id)
            if not es_activo or not estado or estado.get("modo") != "confirmacion_barbecho":
                return
            estado["modo"] = "esperando_labores_barbecho"
            estado.pop("labores_barbecho", None)

        slack_app.client.chat_postMessage(channel=dm_channel, thread_ts=thread_ts, text="Escribe de nuevo tus labores:")
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

        try:
            client.chat_update(
                channel=channel,
                ts=msg["ts"],
                blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": f"Empleado seleccionado: *{nombre_elegido}* ✅"}}],
                text=f"Empleado seleccionado: {nombre_elegido}",
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
        _empleado, _cargo = buscar_empleado_y_cargo(nombre_elegido)
        if not _empleado:
            reply(f"No encontré a *{nombre_elegido}* en la base de datos. Escribe nombre y apellido completos.")
            return

        _cargo_evaluador = _cargo_ev_peek
        _relacion = "igual"
        _preguntas_pre = {}
        _preguntas_area_pre = []
        _mo_invalido = False

        if _area_peek == "middleoffice":
            _nombre_ev = obtener_nombre_por_id_usuario(user_id)
            _mo_evaluados = obtener_evaluados_middleoffice(_nombre_ev or "") if _nombre_ev else []
            _preguntas_area_pre = obtener_preguntas_mo()
            if _mo_evaluados and not any(normalizar_nombre(_empleado) == normalizar_nombre(e) for e in _mo_evaluados):
                _mo_invalido = True
        elif _area_peek == "palantir":
            if _cargo_ev_peek is None:
                _cargo_evaluador = obtener_cargo_por_slack_id(user_id)
            _relacion = comparar_jerarquia(_cargo_evaluador or "", _cargo or "")
            _preguntas_area_pre = obtener_preguntas_palantir(tipo_relacion(_relacion))
        else:
            if _cargo_ev_peek is None:
                _cargo_evaluador = obtener_cargo_por_slack_id(user_id)
            _relacion = comparar_jerarquia(_cargo_evaluador or "", _cargo or "")
            _preguntas_pre = obtener_preguntas_desde_notion(tipo_relacion(_relacion))

        if _mo_invalido:
            nombre_ev = obtener_nombre_por_id_usuario(user_id)
            mo_ev = obtener_evaluados_middleoffice(nombre_ev or user_id, [user_id])
            if mo_ev:
                lista = "\n".join(f"- {e}" for e in mo_ev)
                reply(f"¿A quién quieres evaluar?\n{lista}")
            else:
                reply("¿A quién quieres evaluar? Dime el nombre de la persona.")
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
                    pregunta = (
                        f"Ya has evaluado a *{_empleado}* en *{proyecto_actual or '?'}* en esta sesión. "
                        "Dime el nombre de otro miembro del proyecto."
                    )
                else:
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
                            pregunta = _primera["texto"] if _primera else "⚠️ No hay preguntas configuradas en Notion para esta área."
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
                    area=estado.get("area", "negocio"),
                    preguntas_area=estado.get("preguntas_area"),
                )

        if accion == "preguntar_valoracion":
            client.chat_postMessage(
                channel=dm_channel,
                thread_ts=thread_ts,
                blocks=_bloques_valoracion(pregunta, user_id),
                text=pregunta,
            )
        elif accion == "mostrar_resumen":
            _enviar_resumen_con_botones(dm_channel, thread_ts, pregunta)
        elif pregunta:
            reply(pregunta)
    except Exception:
        logger.exception("Error procesando sugerencia de empleado")


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


def _mensaje_empleado_no_encontrado(texto):
    sugerencias = sugerir_empleados_parecidos(texto)
    if sugerencias:
        return (
            f"*{texto}* no aparece en la lista de empleados.\n"
            f"¿Querías decir alguno de estos nombres?"
        ), sugerencias
    return (
        f"*{texto}* no aparece en la lista de empleados. "
        "Escribe nombre y apellido como aparece en la lista."
    ), []


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


def _enviar_mas_proyectos(channel, thread_ts):
    texto = "¿Estás trabajando en algún otro proyecto?"
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
                        "action_id": "proyecto_proyectos_si",
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "❌ No"},
                        "action_id": "proyecto_proyectos_no",
                    },
                ],
            },
        ],
    )


def _enviar_mas_miembros(channel, thread_ts):
    texto = "✅ *Evaluación guardada en Notion*.\n\n¿Hay más miembros en el equipo que quieras evaluar?"
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
                        "action_id": "proyecto_mas_si",
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "❌ No"},
                        "action_id": "proyecto_mas_no",
                    },
                ],
            },
        ],
    )


def _enviar_boton_modificar(channel: str, thread_ts: str) -> None:
    texto = "💬 Si quieres modificar tus respuestas, tienes un plazo de 2 días."
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
                        "text": {"type": "plain_text", "text": "✏️ Modificar respuestas"},
                        "action_id": "proyecto_modificar_eval",
                    }
                ],
            },
        ],
    )


def _enviar_lista_modificar(channel: str, thread_ts: str, evaluaciones: list) -> None:
    texto = "✏️ ¿La evaluación de quién quieres modificar?"
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


def _enviar_pregunta_mas_modificaciones(channel: str, thread_ts: str) -> None:
    texto = "✅ ¡Respuestas actualizadas! ¿Quieres modificar la evaluación de alguien más?"
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
                        "action_id": "proyecto_modif_mas_si",
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "❌ No"},
                        "action_id": "proyecto_modif_mas_no",
                    },
                ],
            },
        ],
    )


def _enviar_resumen_con_botones(channel, thread_ts, text):
    slack_app.client.chat_postMessage(
        channel=channel,
        thread_ts=thread_ts,
        text=text,
        blocks=[
            {"type": "section", "text": {"type": "mrkdwn", "text": text}},
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "✅ Sí, guardar"},
                        "style": "primary",
                        "action_id": "proyecto_confirmar",
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "✏️ Modificar"},
                        "action_id": "proyecto_modificar",
                    },
                ],
            },
        ],
    )


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
            text="Por favor, no contestes a las evaluaciones fuera de los hilos 😊",
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
                    text="Este hilo no es una evaluación. Por favor, ve al mensaje de la evaluación y contesta ahí.",
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
            reply("Esta evaluación ya ha concluido, por favor salga del hilo. 👋")
            return
        with lock:
            conversaciones.pop(user_id, None)
        reply("Evaluación *cancelada* voluntariamente. Si quieres volver a empezar, escribe cualquier mensaje en este hilo.")
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
            # Resolver selección numérica de sugerencias previas
            _sugerencias_actuales = _sugerencias_por_usuario.get(user_id, [])
            texto_busqueda = texto
            if texto.strip().isdigit() and _sugerencias_actuales:
                idx = int(texto.strip()) - 1
                if 0 <= idx < len(_sugerencias_actuales):
                    texto_busqueda = _sugerencias_actuales[idx]

            if _area_peek == "middleoffice":
                _nombre_ev = obtener_nombre_por_id_usuario(user_id)
                _mo_evaluados = obtener_evaluados_middleoffice(_nombre_ev or "") if _nombre_ev else []
            _empleado_pre, _cargo_pre = buscar_empleado_y_cargo(texto_busqueda)
            if _empleado_pre:
                _sugerencias_por_usuario.pop(user_id, None)
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
                    _invalido_pre, _nuevas_sugerencias = _mensaje_empleado_no_encontrado(texto_busqueda)
                    _sugerencias_por_usuario[user_id] = _nuevas_sugerencias
        except Exception:
            logger.exception("Error en Notion al buscar empleado")
            reply("⚠️ Error temporal consultando datos. Vuelve a intentarlo.")
            return

    # Comprobar si ya completó la evaluación en este ciclo (solo para conversaciones nuevas)
    _ya_respondio = False
    if _modo_peek == "pre_inicial":
        try:
            _nombre_ya = _nombre_real(user_id, logger)
            _hora_env = evaluacion_hora.get(user_id, 0)
            if _hora_env:
                _ya_respondio = evaluacion_proyecto_guardada_desde(_nombre_ya, _hora_env)
        except Exception:
            logger.exception("Error comprobando si ya respondió en este ciclo")

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
            pregunta = "¿A qué área perteneces?"

        elif modo == "esperando_area":
            _AREA_MAP = {
                "1": "negocio", "uno": "negocio", "negocio": "negocio",
                "2": "middleoffice", "dos": "middleoffice", "middleoffice": "middleoffice",
                "middle office": "middleoffice", "middle": "middleoffice", "mo": "middleoffice",
                "3": "palantir", "tres": "palantir", "palantir": "palantir",
            }
            _area_elegida = _AREA_MAP.get(normalizar_nombre(texto))
            if _area_elegida:
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
                pregunta = "Por favor, pulsa el botón del área al que perteneces 😊"

        elif modo == "esperando_situacion":
            _SITUACION_MAP = {
                "proyecto": "proyecto", "en proyecto": "proyecto",
                "barbecho": "barbecho", "en barbecho": "barbecho",
            }
            _situacion = _SITUACION_MAP.get(normalizar_nombre(texto))
            if _situacion == "proyecto":
                estado["modo"] = "esperando_proyecto"
                accion = "pedir_proyecto"
                pregunta = "Escribe el nombre de uno de los proyectos en los que estás trabajando. Más adelante podrás evaluar el resto"
            elif _situacion == "barbecho":
                estado["modo"] = "esperando_labores_barbecho"
                accion = "preguntar"
                pregunta = "¿Qué labores estás realizando?"
            else:
                accion = "pedir_situacion"

        elif modo == "esperando_labores_barbecho":
            if texto:
                estado["labores_barbecho"] = texto
                estado["modo"] = "confirmacion_barbecho"
                accion = "mostrar_resumen_barbecho"
                pregunta = texto
            else:
                accion = "preguntar"
                pregunta = "¿Qué labores estás realizando?"

        elif modo == "confirmacion_barbecho":
            if _es_si(texto) or normalizar_nombre(texto) in {"entregar", "guardar", "confirmar"}:
                accion = "guardar_barbecho"
            elif normalizar_nombre(texto) in {"modificar", "cambiar", "editar"}:
                estado["modo"] = "esperando_labores_barbecho"
                estado.pop("labores_barbecho", None)
                accion = "preguntar"
                pregunta = "Escribe de nuevo tus labores:"
            else:
                accion = "mostrar_resumen_barbecho"
                pregunta = estado.get("labores_barbecho", "")

        elif modo == "esperando_proyecto":
            if texto:
                estado["respuestas"]["proyecto"] = texto
                estado["proyecto_actual"] = texto
                estado["modo"] = "esperando_persona"
                accion = "pedir_persona"
                pregunta = (
                    f"Perfecto 😊, vamos con el proyecto *{texto}*. "
                    "Dime el nombre de uno de los miembros de tu equipo, podrás evaluar al resto después."
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
                                pregunta = _primera["texto"] if _primera else "⚠️ No hay preguntas configuradas en Notion para esta área."
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
                    pregunta = "¿Qué miembro del proyecto quieres evaluar?"

        elif modo == "preguntando_area_secuencial":
            todas = estado.get("preguntas_area", [])
            idx = estado.get("pregunta_actual", 0)
            if texto and todas and idx < len(todas):
                clave_actual = todas[idx]["clave"]
                valor_normalizado = _normalizar_valoracion(texto) if clave_actual in {"q1", "mo_contribucion"} else None
                if clave_actual in {"q1", "mo_contribucion"} and valor_normalizado is None:
                    accion = "preguntar"
                    pregunta = "Por favor, responde con un número del 1 al 4 🔢"
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
                        area=estado.get("area", "negocio"),
                        preguntas_area=estado.get("preguntas_area"),
                        tras_modificacion=True,
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
                    accion = "preguntar_valoracion" if campo in _VALORACION_CLAVES else "pedir_valor_modificacion"
            else:
                _max_opcion = 2 + len(estado.get("preguntas_area", []))
                accion = "pedir_modificacion"
                pregunta = f"Por favor, responde con un número del 1 al {_max_opcion} 🔢"

        elif modo == "modificando_respuesta_area":
            campo = estado.get("campo_modificando")
            if campo and texto:
                if campo in {"q1", "mo_contribucion"}:
                    valor_norm = _normalizar_valoracion(texto)
                    if valor_norm is None:
                        accion = "pedir_valor_modificacion"
                        todas = estado.get("preguntas_area", [])
                        pregunta_base = next((q["texto"] for q in todas if q["clave"] == campo), "")
                        pregunta = "Por favor, responde con un número del 1 al 4 🔢"
                    else:
                        estado["respuestas"][campo] = valor_norm
                        estado.pop("campo_modificando", None)
                        estado["modo"] = "confirmacion"
                        accion = "mostrar_resumen"
                        pregunta = resumen_respuestas(
                            estado["respuestas"],
                            area=estado.get("area", "negocio"),
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
                        area=estado.get("area", "negocio"),
                        preguntas_area=estado.get("preguntas_area"),
                        tras_modificacion=True,
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
            _ahora_fin = time.time()
            _evs_fin = [e for e in (estado.get("evaluaciones_guardadas") or []) if _ahora_fin - e["ts"] <= 2 * 24 * 3600]
            if normalizar_nombre(texto) in {"modificar", "modificar respuestas", "editar"} and _evs_fin:
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
                pregunta = "Responde `sí` o `no`."

    # Despacho de acciones — fuera del lock
    _ACCIONES_PREGUNTA = {
        "preguntar",
        "pedir_persona", "pedir_persona_mismo_proyecto",
        "pedir_proyecto",
        "pedir_valor_modificacion", "pedir_mas_personas",
    }
    if accion == "pedir_situacion":
        slack_app.client.chat_postMessage(
            channel=dm_channel,
            thread_ts=thread_ts,
            text="¿Estás actualmente en proyecto o en barbecho?",
            blocks=[
                {"type": "section", "text": {"type": "mrkdwn", "text": "¿Estás actualmente en proyecto o en barbecho?"}},
                {
                    "type": "actions",
                    "elements": [
                        {"type": "button", "text": {"type": "plain_text", "text": "🏗️ En proyecto"}, "value": "proyecto", "action_id": "situacion_proyecto"},
                        {"type": "button", "text": {"type": "plain_text", "text": "⏸️ En barbecho"}, "value": "barbecho", "action_id": "situacion_barbecho"},
                    ],
                },
            ],
        )
        return
    if accion == "mostrar_resumen_barbecho":
        labores = pregunta or estado.get("labores_barbecho", "")
        texto_resumen = f"📋 Tus labores:\n_{labores}_\n\n¿Lo entrego o prefieres modificarlo?"
        slack_app.client.chat_postMessage(
            channel=dm_channel,
            thread_ts=thread_ts,
            text=texto_resumen,
            blocks=[
                {"type": "section", "text": {"type": "mrkdwn", "text": texto_resumen}},
                {
                    "type": "actions",
                    "elements": [
                        {"type": "button", "text": {"type": "plain_text", "text": "✅ Entregar"}, "style": "primary", "action_id": "barbecho_entregar"},
                        {"type": "button", "text": {"type": "plain_text", "text": "✏️ Modificar"}, "action_id": "barbecho_modificar"},
                    ],
                },
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
            reply("✅ Registrado. Muchas gracias, ya puedes salir del hilo 👋")
        else:
            reply("⚠️ No se pudo guardar en Notion. Revisa permisos/logs.")
        return
    if accion == "pedir_area":
        _texto_area = pregunta or "¿A qué área perteneces?"
        slack_app.client.chat_postMessage(
            channel=dm_channel,
            thread_ts=thread_ts,
            blocks=_bloques_area(_texto_area, user_id),
            text=_texto_area,
        )
        return
    if accion == "pedir_persona_invalida":
        _sug = _sugerencias_por_usuario.get(user_id, [])
        if _sug:
            slack_app.client.chat_postMessage(
                channel=dm_channel,
                thread_ts=thread_ts,
                blocks=_bloques_sugerencias(pregunta or "", _sug, user_id),
                text=pregunta or "",
            )
        else:
            reply(pregunta if pregunta else "")
        return
    if accion == "pedir_modificacion":
        _enviar_menu_modificacion_area(dm_channel, thread_ts, estado)
        return
    if accion in _ACCIONES_PREGUNTA:
        reply(pregunta if pregunta else "")
        return
    if accion == "pedir_mas_proyectos":
        _enviar_mas_proyectos(dm_channel, thread_ts)
        return
    if accion == "preguntar_valoracion":
        slack_app.client.chat_postMessage(
            channel=dm_channel,
            thread_ts=thread_ts,
            blocks=_bloques_valoracion(pregunta, user_id),
            text=pregunta,
        )
        return
    if accion == "mostrar_resumen":
        _enviar_resumen_con_botones(dm_channel, thread_ts, pregunta)
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
            ok = actualizar_en_notion(editando_page_id, nombre, respuestas_finales, relacion=relacion_final, area=area_final)
            if ok:
                with lock:
                    estado.pop("editando_page_id", None)
                    estado["modo"] = "preguntar_mas_modificaciones"
                    for ev in estado.get("evaluaciones_guardadas", []):
                        if ev["page_id"] == editando_page_id:
                            ev["respuestas"] = dict(respuestas_finales)
                            ev["ts"] = time.time()
                            break
                _enviar_pregunta_mas_modificaciones(dm_channel, thread_ts)
            else:
                reply("⚠️ No se pudo actualizar en Notion. Revisa permisos/logs.")
            return
        page_id = guardar_en_notion(nombre, respuestas_finales, relacion=relacion_final, area=area_final)
        if page_id:
            with lock:
                clave_guardada = (
                    normalizar_nombre(respuestas_finales.get("proyecto", "")),
                    normalizar_nombre(respuestas_finales.get("evaluado", "")),
                )
                estado.setdefault("evaluados_en_sesion", set()).add(clave_guardada)
                estado["modo"] = "preguntar_mas_personas"
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
            _enviar_mas_miembros(dm_channel, thread_ts)
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
    if accion == "ya_respondido":
        reply("Ya has completado tu evaluación mensual 👏 ¡Muchas gracias por tu tiempo! 👋")
        return
    if accion == "terminar":
        reply("Perfecto, muchas gracias por tu tiempo ❤️. Ya puedes salir del hilo 👋")
        _ahora_t = time.time()
        _evs_t = [e for e in (estado.get("evaluaciones_guardadas") or []) if _ahora_t - e["ts"] <= 2 * 24 * 3600]
        if _evs_t:
            _enviar_boton_modificar(dm_channel, thread_ts)
        return
    if accion == "mostrar_seleccion_modificar":
        _ahora_s = time.time()
        _evs_s = [e for e in (estado.get("evaluaciones_guardadas") or []) if _ahora_s - e["ts"] <= 2 * 24 * 3600]
        if _evs_s:
            _enviar_lista_modificar(dm_channel, thread_ts, _evs_s)
        return
    if accion == "terminar_modificacion":
        reply("✅ ¡Listo! Evaluación finalizada. Muchas gracias 👋")
        _ahora_tm = time.time()
        _evs_tm = [e for e in (estado.get("evaluaciones_guardadas") or []) if _ahora_tm - e["ts"] <= 2 * 24 * 3600]
        if _evs_tm:
            _enviar_boton_modificar(dm_channel, thread_ts)
        return
    if accion == "ya_terminado":
        reply("Esta evaluación ya ha concluido, por favor salga del hilo. 👋")
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
        ok = actualizar_en_notion(editando_page_id, nombre, respuestas_finales, relacion=relacion_final, area=area_final)
        if ok:
            with lock:
                estado.pop("editando_page_id", None)
                estado["modo"] = "preguntar_mas_modificaciones"
                for ev in estado.get("evaluaciones_guardadas", []):
                    if ev["page_id"] == editando_page_id:
                        ev["respuestas"] = dict(respuestas_finales)
                        ev["ts"] = time.time()
                        break
            _enviar_pregunta_mas_modificaciones(dm_channel, thread_ts)
        else:
            reply("⚠️ No se pudo actualizar en Notion. Revisa permisos/logs.")
        return
    page_id = guardar_en_notion(nombre, respuestas_finales, relacion=relacion_final, area=area_final)
    if page_id:
        with lock:
            clave_guardada = (
                normalizar_nombre(respuestas_finales.get("proyecto", "")),
                normalizar_nombre(respuestas_finales.get("evaluado", "")),
            )
            estado.setdefault("evaluados_en_sesion", set()).add(clave_guardada)
            estado["modo"] = "preguntar_mas_personas"
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
        _enviar_mas_miembros(dm_channel, thread_ts)
        return
    reply("⚠️ No se pudo guardar en Notion. Revisa permisos/logs.")


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
        estado["modo"] = "esperando_persona"

    if _area_mp == "middleoffice":
        nombre_ev = obtener_nombre_por_id_usuario(user_id)
        mo_ev = obtener_evaluados_middleoffice(nombre_ev or user_id, [user_id])
        if mo_ev:
            lista = "\n".join(f"- {e}" for e in mo_ev)
            reply(f"¿A quién quieres evaluar?\n{lista}")
        else:
            reply("¿A quién quieres evaluar? Dime el nombre de la persona.")
    else:
        proyecto = estado.get("proyecto_actual") or ""
        reply(
            f"Perfecto. ¿Qué otro miembro del proyecto *{proyecto}* quieres evaluar?"
            if proyecto
            else "Perfecto. ¿Qué otro miembro quieres evaluar?"
        )


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
            estado["modo"] = "preguntar_mas_proyectos"
            _evs_mo = []

    if _area_mp == "middleoffice":
        reply("Perfecto, muchas gracias por tu tiempo ❤️. Ya puedes salir del hilo 👋")
        if _evs_mo:
            _enviar_boton_modificar(dm_channel, thread_ts)
    else:
        _enviar_mas_proyectos(dm_channel, thread_ts)


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
            reply("⚠️ No hay ninguna evaluación activa en este momento.")
            return
        _ahora = time.time()
        _evs_validas = [e for e in (estado.get("evaluaciones_guardadas") or []) if _ahora - e["ts"] <= 2 * 24 * 3600]
        if not _evs_validas:
            reply("⚠️ El plazo de modificación de 2 días ha expirado.")
            return

    _enviar_lista_modificar(dm_channel, thread_ts, _evs_validas)


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
            reply("⚠️ No hay ninguna evaluación activa.")
            return
        ev = next((e for e in (estado.get("evaluaciones_guardadas") or []) if e["page_id"] == page_id_sel), None)
        if not ev or time.time() - ev["ts"] > 2 * 24 * 3600:
            reply("⚠️ El plazo de modificación de 2 días ha expirado.")
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
    )
    _enviar_resumen_con_botones(dm_channel, thread_ts, resumen)


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
        _enviar_lista_modificar(dm_channel, thread_ts, _evs_validas)
    else:
        reply("⚠️ El plazo de modificación de 2 días ha expirado.")


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

    reply("✅ ¡Listo! Evaluación finalizada. Muchas gracias 👋")
    if _evs_validas:
        _enviar_boton_modificar(dm_channel, thread_ts)


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
        estado["modo"] = "esperando_proyecto"
        estado["proyecto_actual"] = None

    reply(
        "Perfecto. Escribe el nombre de uno de los proyectos en los que estás trabajando. "
        "Más adelante podrás evaluar el resto"
    )


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

    reply("Perfecto, muchas gracias por tu tiempo ❤️. Ya puedes salir del hilo 👋")
    if _evs_pno:
        _enviar_boton_modificar(dm_channel, thread_ts)


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

def _build_ejemplo_mensual_view() -> dict:
    ejemplos = obtener_ejemplos_guia()
    ejemplo = ejemplos.get("Mensual", "_No hay ejemplo disponible_")
    return {
        "type": "modal",
        "callback_id": "ejemplo_mensual_ver",
        "title": {"type": "plain_text", "text": "Ejemplo de guía"},
        "close": {"type": "plain_text", "text": "Cerrar"},
        "blocks": [
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": "💡 *Ejemplo de guía — Evaluación Mensual*"},
            },
            {"type": "divider"},
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": ejemplo[:3000] if ejemplo else "_No hay ejemplo disponible_"},
            },
        ],
    }


@slack_app.action("mensual_ver_ejemplo")
def _handle_mensual_ver_ejemplo(ack, body, logger):
    ack()
    trigger_id = body.get("trigger_id")
    if not trigger_id:
        return
    try:
        slack_app.client.views_open(trigger_id=trigger_id, view=_build_ejemplo_mensual_view())
    except Exception:
        logger.exception("Error abriendo modal de ejemplo mensual")


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
                    text="*⏰ Recuerda realizar tu evaluación mensual.* Abre el hilo del mensaje de evaluación y responde.",
                )
                with lock:
                    evaluacion_ultimo_recordatorio[uid] = time.time()
            except Exception:
                logging.exception(f"Error enviando recordatorio DM a {uid}")


def start_socket_mode():
    SocketModeHandler(slack_app, config.SLACK_APP_TOKEN).start()
