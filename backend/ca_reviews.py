"""
Flujo de revisión para Career Advisors (CA).

El bot envía al canal "¿Eres CA de alguien?" cada INTERVALO_CA_SEGUNDOS.
El usuario responde en hilo: sí → bot pide nombre del advisee → muestra todas
las evaluaciones desde la última revisión del CA → pide opinión → guarda en
Notion → pregunta si hay otro advisee.
"""

import logging
import threading
import time
from datetime import datetime, timezone

from . import config
from .clients import notion, slack_app
from .notion_service import (
    _coincide_parent_bbdd,
    _crear_pagina_en_bbdd,
    _data_source_id,
    _extraer_titulo_bbdd,
    _parent_bbdd_referencia,
    _query_bbdd,
    _tipo_objeto_busqueda_bbdd,
    _usa_data_sources,
    obtener_evaluaciones_por_evaluado,
)
from .utils import normalizar_nombre

# ---------------------------------------------------------------------------
# Estado compartido
# ---------------------------------------------------------------------------

ca_ts: set = set()
conversaciones_ca: dict = {}
_lock = threading.Lock()
_cache_bbdd: dict = {}

PREFIJO_BBDD = "Opiniones CA - "

_PROPS_CA = {
    "Name":    {"title": {}},
    "Opinion": {"rich_text": {}},
    "Advisee": {"rich_text": {}},
    "Fecha":   {"date": {}},
}


# ---------------------------------------------------------------------------
# Notion: base de datos de opiniones del CA
# ---------------------------------------------------------------------------

def _asegurar_propiedades_ca(database_id: str) -> None:
    """Añade al esquema de la BD las propiedades que falten."""
    try:
        if _usa_data_sources():
            bbdd = notion.data_sources.retrieve(data_source_id=database_id)
            faltantes = {k: v for k, v in _PROPS_CA.items() if k not in bbdd.get("properties", {})}
            if faltantes:
                notion.data_sources.update(data_source_id=database_id, properties=faltantes)
        else:
            bbdd = notion.databases.retrieve(database_id=database_id)
            faltantes = {k: v for k, v in _PROPS_CA.items() if k not in bbdd.get("properties", {})}
            if faltantes:
                notion.databases.update(database_id=database_id, properties=faltantes)
    except Exception:
        logging.exception(f"Error asegurando propiedades de BD CA {database_id}")


def _obtener_o_crear_bbdd_ca(ca_nombre: str) -> str:
    titulo = f"{PREFIJO_BBDD}{ca_nombre.strip()}"
    with _lock:
        if titulo in _cache_bbdd:
            return _cache_bbdd[titulo]

    parent = _parent_bbdd_referencia()
    resultado = notion.search(
        query=titulo,
        filter={"value": _tipo_objeto_busqueda_bbdd(), "property": "object"},
        page_size=100,
    )
    for bbdd in resultado.get("results", []):
        if _extraer_titulo_bbdd(bbdd) == titulo and _coincide_parent_bbdd(bbdd, parent):
            db_id = _data_source_id(bbdd)
            _asegurar_propiedades_ca(db_id)  # repara propiedades si la BD es antigua
            with _lock:
                _cache_bbdd[titulo] = db_id
            return db_id

    if _usa_data_sources():
        nueva = notion.databases.create(
            parent=parent,
            title=[{"type": "text", "text": {"content": titulo}}],
            initial_data_source={
                "title": [{"type": "text", "text": {"content": titulo}}],
                "properties": _PROPS_CA,
            },
        )
        nueva = notion.databases.retrieve(database_id=nueva["id"])
    else:
        nueva = notion.databases.create(
            parent=parent,
            title=[{"type": "text", "text": {"content": titulo}}],
            properties=_PROPS_CA,
        )

    db_id = _data_source_id(nueva)
    with _lock:
        _cache_bbdd[titulo] = db_id
    logging.info(f"Base de datos CA creada: {titulo}")
    return db_id


def _guardar_opinion(ca_nombre: str, advisee: str, opinion: str) -> tuple[bool, str]:
    """Devuelve (éxito, mensaje_error)."""
    try:
        db_id = _obtener_o_crear_bbdd_ca(ca_nombre)
        fecha_str = datetime.now(config.ZONA_HORARIA_MADRID).strftime("%Y-%m-%d %H:%M")
        _crear_pagina_en_bbdd(
            db_id,
            {
                "Name":    {"title":     [{"text": {"content": f"Opinion {fecha_str}"}}]},
                "Opinion": {"rich_text": [{"text": {"content": opinion[:2000]}}]},
                "Advisee": {"rich_text": [{"text": {"content": advisee}}]},
                "Fecha":   {"date":      {"start": datetime.now(timezone.utc).isoformat()}},
            },
        )
        return True, ""
    except Exception as exc:
        logging.exception(f"Error guardando opinion CA '{ca_nombre}'")
        return False, str(exc)


# ---------------------------------------------------------------------------
# Fecha de la última opinión del CA sobre un advisee
# ---------------------------------------------------------------------------

def _fecha_ultima_opinion(ca_nombre: str, advisee: str) -> str | None:
    titulo = f"{PREFIJO_BBDD}{ca_nombre.strip()}"
    try:
        resultado = notion.search(
            query=titulo,
            filter={"value": _tipo_objeto_busqueda_bbdd(), "property": "object"},
            page_size=10,
        )
        db_id = None
        for bbdd in resultado.get("results", []):
            if _extraer_titulo_bbdd(bbdd) == titulo:
                db_id = _data_source_id(bbdd)
                break
        if not db_id:
            return None

        filas = _query_bbdd(db_id, page_size=100).get("results", [])
        fechas = []
        for fila in filas:
            props = fila.get("properties", {})
            advisee_texto = "".join(
                p.get("plain_text", "")
                for p in props.get("Advisee", {}).get("rich_text", [])
            )
            if normalizar_nombre(advisee_texto) == normalizar_nombre(advisee):
                fecha = (props.get("Fecha", {}).get("date") or {}).get("start", "")
                if fecha:
                    fechas.append(fecha)
        return max(fechas) if fechas else None
    except Exception:
        logging.exception(f"Error buscando ultima opinion de '{ca_nombre}' sobre '{advisee}'")
        return None


# ---------------------------------------------------------------------------
# Resumen de evaluaciones
# ---------------------------------------------------------------------------

def _resumen_advisee(advisee: str, desde_fecha: str | None) -> str:
    try:
        evaluaciones = obtener_evaluaciones_por_evaluado(advisee)
    except RuntimeError:
        return f"No hay evaluaciones registradas para *{advisee}*."
    except Exception:
        logging.exception(f"Error leyendo evaluaciones de '{advisee}'")
        return f"Error al leer evaluaciones de *{advisee}*."

    if not evaluaciones:
        return f"No hay evaluaciones registradas para *{advisee}*."

    if desde_fecha:
        nuevas = [e for e in evaluaciones if (e.get("fecha") or "") > desde_fecha]
        if not nuevas:
            return (
                f"*{advisee}*: no hay evaluaciones nuevas desde tu última revisión "
                f"({desde_fecha[:10]})."
            )
        evaluaciones = nuevas

    ordenadas = sorted(evaluaciones, key=lambda e: e.get("fecha", ""))
    lineas = []
    for ev in ordenadas:
        fecha = ev.get("fecha", "")[:10] if ev.get("fecha") else "?"
        lineas.append(
            f"• [{fecha}] *{ev.get('persona_que_evalua', '?')}* en {ev.get('proyecto', '?')} — "
            f"Satisfacción {ev.get('satisfaccion', '?')}/5 | "
            f"Mejor: {ev.get('mejor_aspecto', '?')} | "
            f"Peor: {ev.get('peor_aspecto', '?')}"
        )

    n = len(lineas)
    cabecera = f"*{advisee}* — {n} evaluación{'es' if n != 1 else ''}"
    if desde_fecha:
        cabecera += f" desde {desde_fecha[:10]}"
    return cabecera + ":\n" + "\n".join(lineas)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _es_si(texto: str) -> bool:
    return normalizar_nombre(texto) in {"si", "sí", "s", "yes", "y", "claro", "sip", "vale"}


def _es_no(texto: str) -> bool:
    return normalizar_nombre(texto) in {"no", "n", "nope", "nel"}


def _nombre_real(user_id: str, logger) -> str:
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
    except Exception as exc:
        logger.error(f"users_info falló para {user_id}: {exc}")
        return user_id


# ---------------------------------------------------------------------------
# Envío del mensaje inicial
# ---------------------------------------------------------------------------

def enviar_pregunta_inicial_ca() -> None:
    try:
        resp = slack_app.client.chat_postMessage(
            channel=config.CHANNEL_ID,
            text="👥 *¿Eres Career Advisor de alguien?* Responde `sí` o `no` en este hilo.",
        )
        with _lock:
            ca_ts.add(resp["ts"])
        logging.info(f"Pregunta CA enviada, ts={resp['ts']}")
    except Exception:
        logging.exception("Error enviando pregunta CA")


# ---------------------------------------------------------------------------
# Lógica de conversación — llamada desde slack_bot.py
# ---------------------------------------------------------------------------

def manejar_mensaje_ca(event, logger) -> None:
    user_id = event.get("user")
    thread_ts = event.get("thread_ts")
    channel = event.get("channel")
    texto = (event.get("text") or "").strip()
    clave = (thread_ts, user_id)

    accion = None
    payload = {}

    with _lock:
        estado = conversaciones_ca.get(clave)
        if estado is None:
            estado = {"modo": "inicial", "ca_nombre": None}
            conversaciones_ca[clave] = estado

        modo = estado["modo"]

        if modo == "inicial":
            if _es_si(texto):
                estado["modo"] = "esperando_advisee"
                accion = "pedir_advisee"
            elif _es_no(texto):
                conversaciones_ca.pop(clave, None)
                accion = "terminar_sin_ca"
            else:
                accion = "aclarar_inicial"

        elif modo == "esperando_advisee":
            estado["advisee_actual"] = texto
            # el modo definitivo (esperando_opinion o esperando_otro) se fija
            # fuera del lock, una vez sabemos si hay evaluaciones nuevas
            payload["advisee"] = texto
            payload["ca_nombre"] = estado.get("ca_nombre")
            accion = "mostrar_resumen"

        elif modo == "esperando_opinion":
            payload["advisee"] = estado.get("advisee_actual", "?")
            payload["ca_nombre"] = estado.get("ca_nombre")
            payload["opinion"] = texto
            estado["modo"] = "esperando_otro"
            accion = "guardar_y_preguntar_otro"

        elif modo == "esperando_otro":
            if _es_si(texto):
                estado["modo"] = "esperando_advisee"
                accion = "pedir_siguiente_advisee"
            elif _es_no(texto):
                conversaciones_ca.pop(clave, None)
                accion = "terminar"
            else:
                accion = "aclarar_otro"

    def reply(text):
        slack_app.client.chat_postMessage(channel=channel, thread_ts=thread_ts, text=text)

    if accion == "pedir_advisee":
        reply("¿Cuál es el nombre de tu advisee?")

    elif accion == "terminar_sin_ca":
        reply("¡Perfecto, gracias! 👋")

    elif accion == "aclarar_inicial":
        reply("Responde `sí` o `no`. ¿Eres Career Advisor de alguien?")

    elif accion == "mostrar_resumen":
        ca_nombre = _nombre_real(user_id, logger)
        with _lock:
            if clave in conversaciones_ca:
                conversaciones_ca[clave]["ca_nombre"] = ca_nombre
        desde_fecha = _fecha_ultima_opinion(ca_nombre, payload["advisee"])
        resumen = _resumen_advisee(payload["advisee"], desde_fecha)
        sin_novedades = "no hay evaluaciones nuevas" in resumen or "No hay evaluaciones registradas" in resumen
        with _lock:
            if clave in conversaciones_ca:
                conversaciones_ca[clave]["modo"] = "esperando_otro" if sin_novedades else "esperando_opinion"
        if sin_novedades:
            reply(f"{resumen}\n\n¿Tienes otro advisee? (`sí` / `no`)")
        else:
            reply(f"{resumen}\n\n*¿Qué opinas de esto?*")

    elif accion == "guardar_y_preguntar_otro":
        ca_nombre = payload["ca_nombre"] or _nombre_real(user_id, logger)
        ok, error = _guardar_opinion(ca_nombre, payload["advisee"], payload["opinion"])
        if ok:
            reply("✅ Opinión guardada en Notion.\n\n¿Tienes otro advisee? (`sí` / `no`)")
        else:
            reply(f"⚠️ No se pudo guardar en Notion: `{error[:300]}`\n\n¿Tienes otro advisee? (`sí` / `no`)")

    elif accion == "pedir_siguiente_advisee":
        reply("¿Cuál es el nombre de tu próximo advisee?")

    elif accion == "terminar":
        reply("¡Perfecto, gracias por tu tiempo! 🎉")

    elif accion == "aclarar_otro":
        reply("Responde `sí` si tienes otro advisee, o `no` para terminar.")


# ---------------------------------------------------------------------------
# Ciclo principal
# ---------------------------------------------------------------------------

def ciclo_envio_ca() -> None:
    time.sleep(60)
    while True:
        try:
            enviar_pregunta_inicial_ca()
        except Exception:
            logging.exception("Error en ciclo CA")
        time.sleep(config.INTERVALO_CA_SEGUNDOS)
