"""Helper compartido para el botón "⬅️ Atrás" de las conversaciones de Slack.

Los 3 flujos de DM (slack_bot.py, personal_eval.py, ca_reviews.py) guardan su
estado de conversación en un dict en memoria por usuario. Antes de este módulo
no existía histórico: cada paso sobrescribía el anterior. Estas funciones
apilan una copia del estado antes de cada avance, para poder restaurarla.

Una vez el flujo guarda en Notion, se llama a limpiar_historial() para que el
botón "Atrás" deje de ofrecerse (guardar es un punto sin retorno).
"""

from .i18n import t


def _copiar(valor):
    if isinstance(valor, dict):
        return dict(valor)
    if isinstance(valor, list):
        return list(valor)
    if isinstance(valor, set):
        return set(valor)
    return valor


def push_historial(estado: dict) -> None:
    """Apila una copia del estado actual antes de avanzar de paso."""
    historial = estado.setdefault("_historial", [])
    snap = {k: _copiar(v) for k, v in estado.items() if k != "_historial"}
    historial.append(snap)


def pop_historial(estado: dict) -> bool:
    """Restaura el paso anterior sobre el propio estado. True si había algo que restaurar."""
    historial = estado.get("_historial")
    if not historial:
        return False
    snap = historial.pop()
    for k in list(estado.keys()):
        if k != "_historial":
            del estado[k]
    estado.update(snap)
    estado["_historial"] = historial
    return True


def tiene_historial(estado: dict) -> bool:
    return bool(estado.get("_historial"))


def limpiar_historial(estado: dict) -> None:
    estado.pop("_historial", None)


def boton_atras(action_id: str, clave_texto: str, idioma: str = "es") -> dict:
    return {
        "type": "button",
        "text": {"type": "plain_text", "text": t(clave_texto, idioma), "emoji": True},
        "action_id": action_id,
    }


def fila_atras(action_id: str, clave_texto: str, estado: dict, idioma: str = "es") -> list:
    """Devuelve [] o una fila de bloque `actions` con el botón Atrás, según haya historial."""
    if not tiene_historial(estado):
        return []
    return [{"type": "actions", "elements": [boton_atras(action_id, clave_texto, idioma)]}]
