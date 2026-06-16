import logging
import secrets
import time
from datetime import datetime, timedelta

from slack_bolt.adapter.socket_mode import SocketModeHandler

from . import config
from .clients import slack_app
from .notion_service import guardar_en_notion
from .state import conversaciones, evaluacion_ts, evaluaciones_pendientes, lock
from .utils import normalizar_nombre


def enviar_una_evaluacion():
    try:
        resp = slack_app.client.chat_postMessage(channel=config.CHANNEL_ID, text=config.PREGUNTAS[0]["texto"])
        with lock:
            evaluacion_ts.add(resp["ts"])
        logging.info(f"Evaluación iniciada, ts={resp['ts']}")
    except Exception:
        logging.exception("Error enviando mensaje de evaluación")


def crear_revision_pendiente(origen):
    pendiente = {
        "id": secrets.token_urlsafe(12),
        "origen": origen,
        "creada": datetime.now(config.ZONA_HORARIA_MADRID).strftime("%Y-%m-%d %H:%M:%S %Z"),
    }
    with lock:
        evaluaciones_pendientes.append(pendiente)
    logging.info(f"Evaluación pendiente de revisión creada: {pendiente['id']}")


def enviar_o_crear_revision(origen):
    if config.REVIEW_BEFORE_SEND:
        crear_revision_pendiente(origen)
    else:
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


def texto_pregunta_por_clave(clave):
    for pregunta in config.PREGUNTAS:
        if pregunta["clave"] == clave:
            return pregunta["texto"]
    return "Escribe la nueva respuesta."


def respuesta_es_confirmacion(texto):
    return normalizar_nombre(texto) in {"si", "sí", "s", "ok", "okay", "confirmar", "guardar", "correcto"}


def respuesta_es_modificacion(texto):
    return normalizar_nombre(texto) in {"modificar", "cambiar", "editar", "no", "n", "repetir"}


@slack_app.event("message")
def handle_message_events(event, logger):
    if event.get("bot_id"):
        return
    thread_ts = event.get("thread_ts")
    if not thread_ts:
        return
    with lock:
        if thread_ts not in evaluacion_ts:
            return

    user_id = event.get("user")
    texto = event.get("text", "").strip()
    clave_conv = (thread_ts, user_id)

    with lock:
        estado = conversaciones.get(clave_conv)
        if estado is None:
            estado = {"indice": 0, "respuestas": {}, "modo": "preguntas"}
            conversaciones[clave_conv] = estado

        if estado.get("modo") == "confirmacion":
            respuestas_finales = dict(estado["respuestas"])
            if respuesta_es_confirmacion(texto):
                del conversaciones[clave_conv]
                accion = "guardar"
            elif respuesta_es_modificacion(texto):
                estado["modo"] = "elegir_modificacion"
                accion = "menu_modificacion"
            else:
                accion = "aclarar"
            terminado = False
        elif estado.get("modo") == "elegir_modificacion":
            clave = clave_modificacion(texto)
            if clave:
                estado["modo"] = "capturar_modificacion"
                estado["modificando"] = clave
                siguiente_pregunta = texto_pregunta_por_clave(clave)
                accion = "pedir_nuevo_valor"
            else:
                accion = "menu_modificacion"
            terminado = False
        elif estado.get("modo") == "capturar_modificacion":
            estado["respuestas"][estado.get("modificando")] = texto
            estado.pop("modificando", None)
            estado["modo"] = "confirmacion"
            respuestas_finales = dict(estado["respuestas"])
            accion = "mostrar_resumen"
            terminado = False
        else:
            accion = None
            if estado["indice"] >= len(config.PREGUNTAS):
                return
            pregunta_actual = config.PREGUNTAS[estado["indice"]]
            estado["respuestas"][pregunta_actual["clave"]] = texto
            estado["indice"] += 1
            terminado = estado["indice"] >= len(config.PREGUNTAS)
            if terminado:
                respuestas_finales = dict(estado["respuestas"])
                estado["modo"] = "confirmacion"
            else:
                siguiente_pregunta = config.PREGUNTAS[estado["indice"]]["texto"]

    if accion == "menu_modificacion":
        slack_app.client.chat_postMessage(channel=config.CHANNEL_ID, thread_ts=thread_ts, text=texto_menu_modificacion())
        return
    if accion == "pedir_nuevo_valor":
        slack_app.client.chat_postMessage(channel=config.CHANNEL_ID, thread_ts=thread_ts, text="Perfecto. Escribe la nueva respuesta para este campo:\n" + siguiente_pregunta)
        return
    if accion == "mostrar_resumen":
        slack_app.client.chat_postMessage(channel=config.CHANNEL_ID, thread_ts=thread_ts, text="Respuesta actualizada.\n\n" + resumen_respuestas(respuestas_finales))
        return
    if accion == "aclarar":
        slack_app.client.chat_postMessage(channel=config.CHANNEL_ID, thread_ts=thread_ts, text="Responde `sí` para guardar en Notion o `modificar` para cambiar una respuesta concreta.")
        return
    if accion == "guardar":
        nombre = user_id
        try:
            nombre = slack_app.client.users_info(user=user_id)["user"]["real_name"]
        except Exception:
            logger.warning(f"No se pudo obtener el nombre del usuario {user_id}")
        guardado = guardar_en_notion(nombre, respuestas_finales)
        texto_confirmacion = "¡Gracias! Tus respuestas han sido registradas en Notion. ✅" if guardado else "He recibido tu confirmación, pero no he podido guardar en Notion. Revisa permisos/logs."
        slack_app.client.chat_postMessage(channel=config.CHANNEL_ID, thread_ts=thread_ts, text=texto_confirmacion)
        return

    if not terminado:
        slack_app.client.chat_postMessage(channel=config.CHANNEL_ID, thread_ts=thread_ts, text=siguiente_pregunta)
        return
    slack_app.client.chat_postMessage(channel=config.CHANNEL_ID, thread_ts=thread_ts, text=resumen_respuestas(respuestas_finales))


def preguntas_revision_html():
    return "\n".join(f"<li>{pregunta['texto']}</li>" for pregunta in config.PREGUNTAS)


def pendientes_revision_html():
    with lock:
        pendientes = list(evaluaciones_pendientes)
    if not config.REVIEW_BEFORE_SEND:
        return "<p class='fine'>La revisión previa está desactivada. Las evaluaciones se envían automáticamente.</p>"
    if not pendientes:
        return "<p class='fine'>No hay evaluaciones pendientes de revisión.</p>"
    return "\n".join(
        f"""<div class="card-line"><p><strong>Pendiente:</strong> {p['creada']} · {p['origen']}</p>
<form method="post" action="/enviar_pendiente" data-loading="Enviando evaluación a Slack">
<input type="hidden" name="pending_id" value="{p['id']}"><button type="submit">Enviar evaluación</button></form></div>"""
        for p in pendientes
    )


def enviar_revision_pendiente(pending_id):
    with lock:
        indice = next((i for i, item in enumerate(evaluaciones_pendientes) if item["id"] == pending_id), None)
        if indice is None:
            raise RuntimeError("Esa evaluación pendiente ya no existe.")
        evaluaciones_pendientes.pop(indice)
    enviar_una_evaluacion()


def start_socket_mode():
    SocketModeHandler(slack_app, config.SLACK_APP_TOKEN).start()
