"""
Evaluaciones extra (fuera de proyecto) — lógica de negocio y operaciones Notion.

Un empleado puede pedir a otro compañero que le evalúe sobre un tema concreto en el que
trabajaron juntos, que no es un proyecto formal. El compañero recibe una notificación por
Slack y decide libremente si responde (no es obligatorio).

Estructura en Notion:
  Activaciones de permisos/
    Solicitudes Evaluaciones Extra  (BD de solicitudes pendientes)

  TO-SEE → Resultados Evaluaciones/
    Resultados evaluaciones extra (fuera de proyecto)  (BD plana de resultados)
"""

import logging
import threading
from datetime import datetime, timezone

from . import config
from .clients import notion, slack_app
from .i18n import t
from .notion_service import (
    _buscar_bbdd_en_pagina_id,
    _parent_bbdd_en_pagina,
    _query_bbdd,
    idioma_por_slack_id,
    obtener_registros_empleados,
)
from .project_evals import _crear_bbdd, _crear_pagina_en_bbdd
from .eval_tracking import registrar_envio, marcar_completada
from .utils import normalizar_nombre

# ---------------------------------------------------------------------------
# Constantes de nombres Notion
# ---------------------------------------------------------------------------

_NOMBRE_BBDD_SOLICITUDES = "Solicitudes Evaluaciones Extra"
_NOMBRE_BBDD_RESULTADOS = "Resultados evaluaciones extra (fuera de proyecto)"

_PROPS_SOLICITUDES = {
    "Evaluador": {"title": {}},
    "Solicitante": {"rich_text": {}},
    "Contexto": {"rich_text": {}},
    "Fecha_solicitud": {"date": {}},
    "Fecha_limite": {"date": {}},
    "Completada": {"checkbox": {}},
}

_PROPS_RESULTADOS = {
    "Name": {"title": {}},
    "Fecha": {"date": {}},
    "Solicitante": {"rich_text": {}},
    "Evaluador": {"rich_text": {}},
    "Contexto": {"rich_text": {}},
    "Nota": {"number": {"format": "number"}},
    "Justificacion": {"rich_text": {}},
}

# ---------------------------------------------------------------------------
# Caches
# ---------------------------------------------------------------------------

_lock_bbdd_solicitudes = threading.Lock()
_cache_bbdd_solicitudes_id: dict = {"db_id": None}

_lock_bbdd_resultados = threading.Lock()
_cache_bbdd_resultados_id: dict = {"db_id": None}


def _obtener_o_crear_bbdd_solicitudes() -> str | None:
    with _lock_bbdd_solicitudes:
        cached = _cache_bbdd_solicitudes_id["db_id"]
    if cached:
        return cached

    parent = _parent_bbdd_en_pagina(config.NOTION_ACTIVACIONES_PERMISOS_PAGE_NAME, crear=True)
    if parent.get("type") != "page_id":
        return None
    parent_id = parent["page_id"]

    db_id = _buscar_bbdd_en_pagina_id(parent_id, _NOMBRE_BBDD_SOLICITUDES)
    if not db_id:
        try:
            db_id = _crear_bbdd(parent_id, _NOMBRE_BBDD_SOLICITUDES, _PROPS_SOLICITUDES)
            logging.info("BD '%s' creada en Notion", _NOMBRE_BBDD_SOLICITUDES)
        except Exception:
            logging.exception("Error creando BD '%s'", _NOMBRE_BBDD_SOLICITUDES)
            return None

    with _lock_bbdd_solicitudes:
        _cache_bbdd_solicitudes_id["db_id"] = db_id
    return db_id


def _obtener_o_crear_bbdd_resultados() -> str | None:
    with _lock_bbdd_resultados:
        cached = _cache_bbdd_resultados_id["db_id"]
    if cached:
        return cached

    parent = _parent_bbdd_en_pagina(config.NOTION_RESULTADOS_EVAL_PAGE_NAME, crear=True)
    if parent.get("type") != "page_id":
        return None
    parent_id = parent["page_id"]

    db_id = _buscar_bbdd_en_pagina_id(parent_id, _NOMBRE_BBDD_RESULTADOS)
    if not db_id:
        try:
            db_id = _crear_bbdd(parent_id, _NOMBRE_BBDD_RESULTADOS, _PROPS_RESULTADOS)
            logging.info("BD '%s' creada en Notion", _NOMBRE_BBDD_RESULTADOS)
        except Exception:
            logging.exception("Error creando BD '%s'", _NOMBRE_BBDD_RESULTADOS)
            return None

    with _lock_bbdd_resultados:
        _cache_bbdd_resultados_id["db_id"] = db_id
    return db_id


# ---------------------------------------------------------------------------
# Slack
# ---------------------------------------------------------------------------

def _slack_id_de(nombre: str) -> str:
    objetivo = normalizar_nombre(nombre)
    try:
        for r in obtener_registros_empleados():
            if normalizar_nombre(r.get("nombre", "")) == objetivo:
                return r.get("id_usuario", "") or ""
    except Exception:
        logging.warning("No se pudieron obtener registros de empleados para notificaciones Slack")
    return ""


def _notificar_solicitud_evaluacion_extra(evaluado: str, evaluador: str, contexto: str, slack_id: str) -> None:
    try:
        idioma = idioma_por_slack_id(slack_id)
        dm = slack_app.client.conversations_open(users=[slack_id])
        channel = dm["channel"]["id"]
        slack_app.client.chat_postMessage(
            channel=channel,
            text=t("evex.slack_solicitud", idioma, evaluado=evaluado, contexto=contexto),
        )
        logging.info("Notificación de evaluación extra enviada a '%s' (Slack: %s)", evaluador, slack_id)
    except Exception:
        logging.exception("Error enviando notificación Slack a '%s'", evaluador)


# ---------------------------------------------------------------------------
# Solicitar una evaluación extra
# ---------------------------------------------------------------------------

def solicitar_evaluacion_extra(evaluado: str, evaluador: str, contexto: str, idioma: str = "es", fecha_limite: str = "") -> dict:
    """Crea la solicitud pendiente en Notion y notifica por Slack al evaluador.
    `fecha_limite` (YYYY-MM-DD) es la fecha tope que fija quien la pide."""
    db_id = _obtener_o_crear_bbdd_solicitudes()
    if not db_id:
        return {"ok": False, "error": t("evex.err_db_access", idioma)}

    try:
        props = {
            "Evaluador": {"title": [{"type": "text", "text": {"content": evaluador}}]},
            "Solicitante": {"rich_text": [{"type": "text", "text": {"content": evaluado}}]},
            "Contexto": {"rich_text": [{"type": "text", "text": {"content": contexto}}]},
            "Fecha_solicitud": {"date": {"start": datetime.now(timezone.utc).isoformat()}},
            "Completada": {"checkbox": False},
        }
        if fecha_limite:
            props["Fecha_limite"] = {"date": {"start": fecha_limite}}
        _crear_pagina_en_bbdd(db_id, props)
    except Exception:
        logging.exception("Error creando solicitud de evaluación extra de '%s' a '%s'", evaluado, evaluador)
        return {"ok": False, "error": t("evex.err_request", idioma)}

    registrar_envio(evaluador, "extra", detalle=contexto)

    slack_id = _slack_id_de(evaluador)
    if slack_id:
        _notificar_solicitud_evaluacion_extra(evaluado, evaluador, contexto, slack_id)

    return {"ok": True}


def obtener_solicitudes_pendientes(evaluador: str) -> list:
    """Devuelve [{page_id, evaluado, contexto, fecha}] de solicitudes pendientes para `evaluador`."""
    db_id = _obtener_o_crear_bbdd_solicitudes()
    if not db_id:
        return []
    objetivo = normalizar_nombre(evaluador)
    try:
        resp = _query_bbdd(db_id, filter={
            "property": "Completada", "checkbox": {"equals": False},
        })
        pendientes = []
        for fila in resp.get("results", []):
            props = fila.get("properties", {})
            evaluador_titulo = "".join(
                p.get("plain_text", "") for p in (props.get("Evaluador") or {}).get("title", [])
            ).strip()
            if normalizar_nombre(evaluador_titulo) != objetivo:
                continue
            solicitante = "".join(
                p.get("plain_text", "") for p in (props.get("Solicitante") or {}).get("rich_text", [])
            ).strip()
            contexto = "".join(
                p.get("plain_text", "") for p in (props.get("Contexto") or {}).get("rich_text", [])
            ).strip()
            fecha = ((props.get("Fecha_solicitud") or {}).get("date") or {}).get("start", "")
            fecha_limite = ((props.get("Fecha_limite") or {}).get("date") or {}).get("start", "")
            pendientes.append({
                "page_id": fila.get("id", ""),
                "evaluado": solicitante,
                "contexto": contexto,
                "fecha": (fecha or "")[:10],
                "fecha_limite": (fecha_limite or "")[:10],
            })
        pendientes.sort(key=lambda x: x.get("fecha", ""))
        return pendientes
    except Exception:
        logging.exception("Error obteniendo solicitudes pendientes de '%s'", evaluador)
        return []


# ---------------------------------------------------------------------------
# Guardar resultado de una evaluación extra
# ---------------------------------------------------------------------------

def guardar_evaluacion_extra(
    evaluado: str,
    evaluador: str,
    contexto: str,
    nota,
    justificacion: str,
    solicitud_page_id: str = "",
) -> bool:
    """Guarda el resultado en Notion y marca la solicitud como completada."""
    db_id = _obtener_o_crear_bbdd_resultados()
    if not db_id:
        return False

    fecha = datetime.now(timezone.utc)
    try:
        _crear_pagina_en_bbdd(db_id, {
            "Name": {"title": [{"type": "text", "text": {"content": evaluador}}]},
            "Fecha": {"date": {"start": fecha.isoformat()}},
            "Solicitante": {"rich_text": [{"type": "text", "text": {"content": evaluado}}]},
            "Evaluador": {"rich_text": [{"type": "text", "text": {"content": evaluador}}]},
            "Contexto": {"rich_text": [{"type": "text", "text": {"content": contexto}}]},
            "Nota": {"number": nota},
            "Justificacion": {"rich_text": [{"type": "text", "text": {"content": justificacion[:2000]}}]},
        })
    except Exception:
        logging.exception("Error guardando evaluación extra de '%s' sobre '%s'", evaluador, evaluado)
        return False

    marcar_completada(evaluador, "extra")

    if solicitud_page_id:
        try:
            notion.pages.update(page_id=solicitud_page_id, properties={"Completada": {"checkbox": True}})
        except Exception:
            logging.exception("Error marcando como completada la solicitud '%s'", solicitud_page_id)

    return True


# ---------------------------------------------------------------------------
# Lectura para reporting (fuente [X#] del informe anual)
# ---------------------------------------------------------------------------

def obtener_evaluaciones_extra_por_evaluado(evaluado: str) -> list[dict]:
    """Devuelve TODAS las evaluaciones extra (fuera de proyecto) recibidas por `evaluado`.

    Cada elemento: {contexto, evaluador, nota, justificacion, respuestas, fecha, page_id, url}.
    """
    db_id = _obtener_o_crear_bbdd_resultados()
    if not db_id:
        return []
    objetivo = normalizar_nombre(evaluado)
    resultado: list[dict] = []
    try:
        cursor = None
        while True:
            kwargs: dict = {"page_size": 100}
            if cursor:
                kwargs["start_cursor"] = cursor
            resp = _query_bbdd(db_id, **kwargs)
            for fila in resp.get("results", []):
                props = fila.get("properties", {})
                solicitante = "".join(
                    p.get("plain_text", "") for p in (props.get("Solicitante") or {}).get("rich_text", [])
                ).strip()
                if normalizar_nombre(solicitante) != objetivo:
                    continue
                evaluador = "".join(
                    p.get("plain_text", "") for p in (props.get("Evaluador") or {}).get("rich_text", [])
                ).strip()
                contexto = "".join(
                    p.get("plain_text", "") for p in (props.get("Contexto") or {}).get("rich_text", [])
                ).strip()
                justificacion = "".join(
                    p.get("plain_text", "") for p in (props.get("Justificacion") or {}).get("rich_text", [])
                ).strip()
                nota = (props.get("Nota") or {}).get("number")
                fecha = ((props.get("Fecha") or {}).get("date") or {}).get("start", "")
                respuestas = f"Nota: {nota}/5. Justificación: {justificacion}" if nota is not None else justificacion
                resultado.append({
                    "contexto": contexto,
                    "evaluador": evaluador,
                    "nota": nota,
                    "justificacion": justificacion,
                    "respuestas": respuestas,
                    "fecha": (fecha or "")[:10],
                    "page_id": fila.get("id", ""),
                    "url": fila.get("url", ""),
                })
            if not resp.get("has_more"):
                break
            cursor = resp.get("next_cursor")
    except Exception:
        logging.exception("Error leyendo evaluaciones extra de '%s'", evaluado)
    resultado.sort(key=lambda x: x.get("fecha", ""))
    return resultado
