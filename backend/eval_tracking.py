"""
Registro de cumplimiento de evaluaciones: cuántas evaluaciones se le han ASIGNADO a cada
persona (como evaluador) y cuántas ha REALIZADO, por ciclo de 4 semanas y por tipo.

Cada fila = "a la persona P se le asignó una evaluación de tipo T en el ciclo C".
Granularidad: mensual/personal/ca = 1 asignación por ciclo; proyecto = 1 por activación;
extra = 1 por solicitud. `Completada` se marca al finalizar el flujo correspondiente.

Estructura en Notion:
  TO-SEE/
    Evaluaciones recibidas y completadas   (BD plana de asignaciones)

Todas las operaciones son best-effort: envueltas en try/except para no romper los flujos de
Slack ni las peticiones web si Notion falla.
"""

import logging
import threading
from datetime import datetime, timedelta, timezone

from . import config
from .clients import notion
from .notion_service import (
    _buscar_bbdd_en_pagina_id,
    _parent_bbdd_en_pagina,
    _query_bbdd,
    _usa_data_sources,
    obtener_config_calendario,
    obtener_nombre_por_id_usuario,
    siguiente_envio_calendario,
)
from .project_evals import _crear_bbdd, _crear_pagina_en_bbdd
from .utils import normalizar_nombre

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

_NOMBRE_BBDD = "Evaluaciones recibidas y completadas"

# Tipos válidos (deben coincidir con las opciones del select y con el frontend)
TIPOS = ["mensual", "personal", "ca", "proyecto", "extra"]

_PROPS = {
    "Persona": {"title": {}},
    "Tipo": {"select": {"options": [{"name": tp} for tp in TIPOS]}},
    "Fecha_envio": {"date": {}},
    "Ciclo": {"rich_text": {}},
    "Detalle": {"rich_text": {}},
    "Completada": {"checkbox": {}},
    "Fecha_completada": {"date": {}},
    "Fecha_recordatorio": {"date": {}},  # último recordatorio enviado (evals web pendientes)
}

_SEMANAS_CICLO = 4

# ---------------------------------------------------------------------------
# Caché de la BD
# ---------------------------------------------------------------------------

_lock_bbdd = threading.Lock()
_cache_bbdd_id: dict = {"db_id": None}


def _obtener_o_crear_bbdd() -> str | None:
    # El lock se mantiene durante TODA la búsqueda/creación para que dos flujos
    # concurrentes (mensual, personal, CA...) no creen bases de datos duplicadas.
    with _lock_bbdd:
        if _cache_bbdd_id["db_id"]:
            return _cache_bbdd_id["db_id"]

        parent = _parent_bbdd_en_pagina(config.NOTION_TOSEE_PAGE_NAME, crear=True)
        if parent.get("type") != "page_id":
            return None
        parent_id = parent["page_id"]

        db_id = _buscar_bbdd_en_pagina_id(parent_id, _NOMBRE_BBDD)
        if not db_id:
            try:
                db_id = _crear_bbdd(parent_id, _NOMBRE_BBDD, _PROPS)
                logging.info("BD '%s' creada en Notion", _NOMBRE_BBDD)
            except Exception:
                logging.exception("Error creando BD '%s'", _NOMBRE_BBDD)
                return None

        _asegurar_prop_recordatorio(db_id)
        _cache_bbdd_id["db_id"] = db_id
        return db_id


def _asegurar_prop_recordatorio(db_id: str) -> None:
    """Añade la columna 'Fecha_recordatorio' si la BD ya existía sin ella (BDs antiguas)."""
    try:
        if _usa_data_sources():
            bd = notion.data_sources.retrieve(data_source_id=db_id)
            if "Fecha_recordatorio" not in bd.get("properties", {}):
                notion.data_sources.update(data_source_id=db_id, properties={"Fecha_recordatorio": {"date": {}}})
        else:
            bd = notion.databases.retrieve(database_id=db_id)
            if "Fecha_recordatorio" not in bd.get("properties", {}):
                notion.databases.update(database_id=db_id, properties={"Fecha_recordatorio": {"date": {}}})
    except Exception:
        logging.exception("Error asegurando la propiedad 'Fecha_recordatorio' en la BD de tracking")


# ---------------------------------------------------------------------------
# Cálculo de ciclo (4 semanas)
# ---------------------------------------------------------------------------

def clave_ciclo_actual() -> str:
    """Devuelve la clave 'YYYY-MM-DD' del inicio del ciclo de 4 semanas en curso.

    Usa la fecha base del calendario ('proyecto_ca'); si no hay calendario, cae a una
    ventana de 4 semanas anclada a la fecha actual.
    """
    try:
        cal = obtener_config_calendario()
        base = cal.get("proyecto_ca")
        if base:
            siguiente = siguiente_envio_calendario(base, _SEMANAS_CICLO)
            inicio = siguiente - timedelta(weeks=_SEMANAS_CICLO)
            return inicio.date().isoformat()
    except Exception:
        logging.exception("Error calculando el ciclo actual; usando fallback")
    # Fallback: inicio de la ventana de 4 semanas terminada en hoy
    return (datetime.now(timezone.utc) - timedelta(weeks=_SEMANAS_CICLO)).date().isoformat()


# ---------------------------------------------------------------------------
# Utilidades de lectura de propiedades
# ---------------------------------------------------------------------------

def _titulo(props: dict, prop: str) -> str:
    return "".join(p.get("plain_text", "") for p in (props.get(prop) or {}).get("title", [])).strip()


def _rich(props: dict, prop: str) -> str:
    return "".join(p.get("plain_text", "") for p in (props.get(prop) or {}).get("rich_text", [])).strip()


def _select(props: dict, prop: str) -> str:
    sel = (props.get(prop) or {}).get("select") or {}
    return (sel.get("name") or "").strip()


def _checkbox(props: dict, prop: str) -> bool:
    return bool((props.get(prop) or {}).get("checkbox"))


def _fecha(props: dict, prop: str):
    """Devuelve un datetime tz-aware de una propiedad date, o None."""
    val = ((props.get(prop) or {}).get("date") or {}).get("start")
    if not val:
        return None
    try:
        d = datetime.fromisoformat(val.replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _iter_filas(db_id: str, filter=None):
    cursor = None
    while True:
        kwargs: dict = {"page_size": 100}
        if filter is not None:
            kwargs["filter"] = filter
        if cursor:
            kwargs["start_cursor"] = cursor
        resp = _query_bbdd(db_id, **kwargs)
        for fila in resp.get("results", []):
            yield fila
        if not resp.get("has_more"):
            break
        cursor = resp.get("next_cursor")


# ---------------------------------------------------------------------------
# Escritura: registrar envío / marcar completada
# ---------------------------------------------------------------------------

def registrar_envio(persona: str, tipo: str, detalle: str = "") -> None:
    """Registra que a `persona` se le asignó una evaluación de tipo `tipo` en el ciclo actual.

    Idempotencia suave: si ya existe una fila pendiente (persona, tipo, ciclo) no duplica.
    """
    if not persona or tipo not in TIPOS:
        return
    db_id = _obtener_o_crear_bbdd()
    if not db_id:
        return
    ciclo = clave_ciclo_actual()
    try:
        if _buscar_fila(db_id, persona, tipo, ciclo, solo_pendientes=True):
            return  # ya hay una asignación pendiente para este ciclo
        _crear_pagina_en_bbdd(db_id, {
            "Persona": {"title": [{"type": "text", "text": {"content": persona}}]},
            "Tipo": {"select": {"name": tipo}},
            "Fecha_envio": {"date": {"start": datetime.now(timezone.utc).isoformat()}},
            "Ciclo": {"rich_text": [{"type": "text", "text": {"content": ciclo}}]},
            "Detalle": {"rich_text": [{"type": "text", "text": {"content": (detalle or "")[:2000]}}]},
            "Completada": {"checkbox": False},
        })
    except Exception:
        logging.exception("Error registrando envío de evaluación '%s' a '%s'", tipo, persona)


def registrar_envio_por_slack_id(user_id: str, tipo: str, detalle: str = "") -> None:
    """Como registrar_envio pero resolviendo el nombre a partir del Slack user_id."""
    try:
        nombre = obtener_nombre_por_id_usuario(user_id)
    except Exception:
        nombre = None
    if nombre:
        registrar_envio(nombre, tipo, detalle)


def marcar_completada(persona: str, tipo: str) -> None:
    """Marca como completada la asignación pendiente (persona, tipo) del ciclo actual.

    Si no existe (envío no registrado), crea una fila ya completada para no perder la
    'realizada'.
    """
    if not persona or tipo not in TIPOS:
        return
    db_id = _obtener_o_crear_bbdd()
    if not db_id:
        return
    ciclo = clave_ciclo_actual()
    ahora = datetime.now(timezone.utc).isoformat()
    try:
        fila = _buscar_fila(db_id, persona, tipo, ciclo, solo_pendientes=True)
        if fila:
            notion.pages.update(page_id=fila["id"], properties={
                "Completada": {"checkbox": True},
                "Fecha_completada": {"date": {"start": ahora}},
            })
            return
        # No hay pendiente: puede que ya se marcara antes en este ciclo. No duplicar.
        if _buscar_fila(db_id, persona, tipo, ciclo, solo_pendientes=False):
            return
        # No había envío registrado en absoluto: auto-cura creando una fila completada.
        _crear_pagina_en_bbdd(db_id, {
            "Persona": {"title": [{"type": "text", "text": {"content": persona}}]},
            "Tipo": {"select": {"name": tipo}},
            "Fecha_envio": {"date": {"start": ahora}},
            "Ciclo": {"rich_text": [{"type": "text", "text": {"content": ciclo}}]},
            "Completada": {"checkbox": True},
            "Fecha_completada": {"date": {"start": ahora}},
        })
    except Exception:
        logging.exception("Error marcando completada la evaluación '%s' de '%s'", tipo, persona)


def marcar_completada_por_slack_id(user_id: str, tipo: str) -> None:
    """Como marcar_completada pero resolviendo el nombre a partir del Slack user_id."""
    try:
        nombre = obtener_nombre_por_id_usuario(user_id)
    except Exception:
        nombre = None
    if nombre:
        marcar_completada(nombre, tipo)


# ---------------------------------------------------------------------------
# Recordatorios de evaluaciones web pendientes (proyecto / extra)
# ---------------------------------------------------------------------------

def pendientes_para_recordatorio(tipos: tuple, umbral_dias: int) -> list:
    """Filas pendientes (Completada=False) de los `tipos` dados cuyo envío es anterior a
    `umbral_dias` y cuyo último recordatorio (si lo hubo) también lo es.

    Devuelve [{page_id, persona, tipo, detalle}] listo para notificar por Slack.
    """
    db_id = _obtener_o_crear_bbdd()
    if not db_id:
        return []
    limite = datetime.now(timezone.utc) - timedelta(days=umbral_dias)
    pendientes = []
    try:
        for fila in _iter_filas(db_id, filter={"property": "Completada", "checkbox": {"equals": False}}):
            props = fila.get("properties", {})
            tipo = _select(props, "Tipo")
            if tipo not in tipos:
                continue
            fecha_envio = _fecha(props, "Fecha_envio")
            if not fecha_envio or fecha_envio > limite:
                continue  # aún no han pasado `umbral_dias` desde el envío
            fecha_rec = _fecha(props, "Fecha_recordatorio")
            if fecha_rec and fecha_rec > limite:
                continue  # ya se recordó hace menos de `umbral_dias`
            persona = _titulo(props, "Persona")
            if not persona:
                continue
            pendientes.append({
                "page_id": fila.get("id", ""),
                "persona": persona,
                "tipo": tipo,
                "detalle": _rich(props, "Detalle"),
            })
    except Exception:
        logging.exception("Error obteniendo pendientes para recordatorio")
    return pendientes


def marcar_recordatorio_enviado(page_id: str) -> None:
    """Sella la fecha del último recordatorio enviado en la fila dada."""
    if not page_id:
        return
    try:
        notion.pages.update(page_id=page_id, properties={
            "Fecha_recordatorio": {"date": {"start": datetime.now(timezone.utc).isoformat()}},
        })
    except Exception:
        logging.exception("Error marcando recordatorio enviado en fila '%s'", page_id)


def _buscar_fila(db_id: str, persona: str, tipo: str, ciclo: str, solo_pendientes: bool):
    """Devuelve la primera fila que coincide con (persona, tipo, ciclo). None si no hay."""
    objetivo = normalizar_nombre(persona)
    and_filters = [
        {"property": "Tipo", "select": {"equals": tipo}},
        {"property": "Ciclo", "rich_text": {"equals": ciclo}},
    ]
    if solo_pendientes:
        and_filters.append({"property": "Completada", "checkbox": {"equals": False}})
    for fila in _iter_filas(db_id, filter={"and": and_filters}):
        if normalizar_nombre(_titulo(fila.get("properties", {}), "Persona")) == objetivo:
            return fila
    return None


# ---------------------------------------------------------------------------
# Lectura para el frontend
# ---------------------------------------------------------------------------

def resumen_ciclo_actual() -> dict:
    """Devuelve { nombre: {"enviadas": n, "realizadas": m} } para el ciclo en curso."""
    db_id = _obtener_o_crear_bbdd()
    if not db_id:
        return {}
    ciclo = clave_ciclo_actual()
    resumen: dict = {}
    try:
        for fila in _iter_filas(db_id, filter={"property": "Ciclo", "rich_text": {"equals": ciclo}}):
            props = fila.get("properties", {})
            persona = _titulo(props, "Persona")
            if not persona:
                continue
            entrada = resumen.setdefault(persona, {"enviadas": 0, "realizadas": 0})
            entrada["enviadas"] += 1
            if _checkbox(props, "Completada"):
                entrada["realizadas"] += 1
    except Exception:
        logging.exception("Error calculando el resumen de cumplimiento del ciclo")
    return resumen


def detalle_por_persona(nombre: str) -> list:
    """Devuelve el desglose por ciclo y tipo para una persona.

    [{"ciclo": "YYYY-MM-DD",
      "tipos": {"mensual": {"enviadas": 1, "realizadas": 1}, ...}}]
    Ordenado por ciclo descendente (más reciente primero).
    """
    db_id = _obtener_o_crear_bbdd()
    if not db_id or not nombre:
        return []
    objetivo = normalizar_nombre(nombre)
    por_ciclo: dict = {}
    try:
        for fila in _iter_filas(db_id):
            props = fila.get("properties", {})
            if normalizar_nombre(_titulo(props, "Persona")) != objetivo:
                continue
            ciclo = _rich(props, "Ciclo") or "—"
            tipo = _select(props, "Tipo") or "otro"
            tipos = por_ciclo.setdefault(ciclo, {})
            entrada = tipos.setdefault(tipo, {"enviadas": 0, "realizadas": 0})
            entrada["enviadas"] += 1
            if _checkbox(props, "Completada"):
                entrada["realizadas"] += 1
    except Exception:
        logging.exception("Error leyendo el detalle de cumplimiento de '%s'", nombre)
    resultado = [{"ciclo": c, "tipos": t} for c, t in por_ciclo.items()]
    resultado.sort(key=lambda x: x["ciclo"], reverse=True)
    return resultado


_SLACK_TIPOS = ("mensual", "personal", "ca")


def pendientes_slack_de_persona(persona: str) -> list:
    """Tipos de evaluacion de Slack (mensual/personal/ca) pendientes para `persona`
    (filas con Completada=False). Devuelve [{tipo, fecha_envio}] en orden estable;
    fecha_envio (YYYY-MM-DD) es la del último envío pendiente de ese tipo, base para el deadline."""
    db_id = _obtener_o_crear_bbdd()
    if not db_id or not persona:
        return []
    objetivo = normalizar_nombre(persona)
    por_tipo: dict = {}  # tipo -> fecha_envio (la más reciente)
    try:
        for fila in _iter_filas(db_id, filter={"property": "Completada", "checkbox": {"equals": False}}):
            props = fila.get("properties", {})
            if normalizar_nombre(_titulo(props, "Persona")) != objetivo:
                continue
            tipo = _select(props, "Tipo")
            if tipo not in _SLACK_TIPOS:
                continue
            fenv = (((props.get("Fecha_envio") or {}).get("date") or {}).get("start", "") or "")[:10]
            if tipo not in por_tipo or fenv > por_tipo[tipo]:
                por_tipo[tipo] = fenv
    except Exception:
        logging.exception("Error leyendo pendientes de Slack de '%s'", persona)
    return [{"tipo": tp, "fecha_envio": por_tipo[tp]} for tp in _SLACK_TIPOS if tp in por_tipo]
