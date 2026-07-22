import logging

from fastapi import APIRouter, Depends

from ..deps import require_session
from ...eval_tracking import pendientes_slack_de_persona

router = APIRouter()

_slack_deeplink_cache = {"url": None}


def _slack_deeplink() -> str:
    """Deep-link que abre el DM con el bot en la app de Slack (no el chat web). Cacheado."""
    if _slack_deeplink_cache["url"] is None:
        try:
            from ...clients import slack_app

            a = slack_app.client.auth_test()
            _slack_deeplink_cache["url"] = f"slack://user?team={a['team_id']}&id={a['user_id']}"
        except Exception:
            logging.exception("No se pudo obtener el deep-link de Slack")
            _slack_deeplink_cache["url"] = "slack://open"
    return _slack_deeplink_cache["url"]


@router.get("/api/tareas-slack")
def tareas_slack(session=Depends(require_session)):
    persona = session.get("persona", "")
    return {"pendientes": pendientes_slack_de_persona(persona), "slackUrl": _slack_deeplink()}
