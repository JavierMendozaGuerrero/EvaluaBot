"""Integración con Slack Lists: muestra las evaluaciones pendientes de cada
persona como filas de una lista de tareas dentro de Slack, y las quita en
cuanto se completan.

Requiere los scopes de bot token `lists:write` (crear/editar listas e ítems,
dar acceso) y `lists:read` (opcional, solo para depurar). Sin esos scopes
todas las llamadas fallan; se registra el error con logging y se sigue —
esto nunca debe bloquear el envío de una notificación ni el guardado en
Notion, que son la parte crítica del bot.

A diferencia de las bases de datos de Notion (que se pueden re-buscar por
título con `notion.search`), la API de Slack Lists no tiene un método para
listar las listas ya creadas. Por eso el `list_id` se persiste en
`slack_lists_config.json` junto a este fichero (no se sube a git) además de
cachearse en memoria — si solo se cacheara en memoria, cada reinicio del
proceso crearía una lista duplicada.
"""

import json
import logging
import os
import tempfile
import threading

from . import config
from .clients import slack_app

_RUTA_CONFIG = os.path.join(config.BASE_DIR, "slack_lists_config.json")
_NOMBRE_LISTA = "Evaluaciones pendientes"

_lock = threading.Lock()
_cache_lista: dict = {"list_id": None, "columnas": {}}
_cache_team_id: dict = {"id": None}
_filas_pendientes: dict = {}  # (tipo, user_id) -> row_id, solo en memoria


def _cargar_config_local() -> dict:
    if not os.path.exists(_RUTA_CONFIG):
        return {}
    try:
        with open(_RUTA_CONFIG, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        logging.exception("No se pudo leer slack_lists_config.json")
        return {}


def _guardar_config_local(list_id: str, columnas: dict) -> None:
    try:
        with tempfile.NamedTemporaryFile("w", dir=config.BASE_DIR, delete=False, suffix=".tmp", encoding="utf-8") as f:
            json.dump({"list_id": list_id, "columnas": columnas}, f, ensure_ascii=False, indent=2)
            tmp = f.name
        os.replace(tmp, _RUTA_CONFIG)
    except Exception:
        logging.exception("No se pudo guardar slack_lists_config.json")


def _team_id():
    if _cache_team_id["id"]:
        return _cache_team_id["id"]
    try:
        resp = slack_app.client.auth_test()
        _cache_team_id["id"] = resp.get("team_id")
    except Exception:
        logging.exception("No se pudo obtener el team_id de Slack")
    return _cache_team_id["id"]


def _obtener_o_crear_lista():
    """Devuelve (list_id, columnas) o (None, {}) si algo falla."""
    with _lock:
        if _cache_lista["list_id"]:
            return _cache_lista["list_id"], _cache_lista["columnas"]

        local = _cargar_config_local()
        if local.get("list_id") and local.get("columnas"):
            _cache_lista["list_id"] = local["list_id"]
            _cache_lista["columnas"] = local["columnas"]
            return _cache_lista["list_id"], _cache_lista["columnas"]

        try:
            resp = slack_app.client.slackLists_create(name=_NOMBRE_LISTA, todo_mode=True)
            list_id = resp["list_id"]
            columnas = {
                col["key"]: col["id"]
                for col in resp.get("list_metadata", {}).get("schema", [])
            }
            _cache_lista["list_id"] = list_id
            _cache_lista["columnas"] = columnas
            _guardar_config_local(list_id, columnas)
            logging.info("Slack List '%s' creada: %s", _NOMBRE_LISTA, list_id)
            return list_id, columnas
        except Exception:
            logging.exception("No se pudo crear/obtener la Slack List de evaluaciones pendientes")
            return None, {}


def enlace_lista_pendientes() -> str | None:
    if not config.SLACK_LISTAS_PENDIENTES_HABILITADO:
        return None
    list_id, _ = _obtener_o_crear_lista()
    team_id = _team_id()
    if not list_id or not team_id:
        return None
    return f"https://app.slack.com/lists/{team_id}/{list_id}"


def añadir_pendiente(tipo: str, user_id: str, titulo: str) -> None:
    """Crea una fila en la lista de pendientes asignada a user_id. Best-effort."""
    if not config.SLACK_LISTAS_PENDIENTES_HABILITADO:
        return
    list_id, columnas = _obtener_o_crear_lista()
    if not list_id:
        return
    try:
        campos = []
        col_name = columnas.get("name")
        if col_name:
            campos.append({
                "column_id": col_name,
                "rich_text": [{
                    "type": "rich_text",
                    "elements": [{"type": "rich_text_section", "elements": [{"type": "text", "text": titulo}]}],
                }],
            })
        col_assignee = columnas.get("todo_assignee")
        if col_assignee:
            campos.append({"column_id": col_assignee, "user": [user_id]})

        resp = slack_app.client.slackLists_items_create(list_id=list_id, initial_fields=campos)
        row_id = (resp.get("item") or {}).get("id")
        if row_id:
            with _lock:
                _filas_pendientes[(tipo, user_id)] = row_id

        slack_app.client.slackLists_access_set(list_id=list_id, access_level="write", user_ids=[user_id])
    except Exception:
        logging.exception("No se pudo añadir el pendiente '%s' de %s a la Slack List", tipo, user_id)


def quitar_pendiente(tipo: str, user_id: str) -> None:
    """Borra la fila de pendiente de user_id, si existe. Best-effort."""
    if not config.SLACK_LISTAS_PENDIENTES_HABILITADO:
        return
    with _lock:
        row_id = _filas_pendientes.pop((tipo, user_id), None)
    if not row_id:
        return
    list_id, _ = _obtener_o_crear_lista()
    if not list_id:
        return
    try:
        slack_app.client.slackLists_items_delete(list_id=list_id, id=row_id)
    except Exception:
        logging.exception("No se pudo quitar el pendiente '%s' de %s de la Slack List", tipo, user_id)
