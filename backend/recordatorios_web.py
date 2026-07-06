"""
Recordatorios Slack para las evaluaciones que se lanzan desde la web y son de larga duración:
  - Evaluaciones de proyecto (tipo "proyecto")
  - Evaluaciones extra fuera de proyecto (tipo "extra")

A diferencia de los recordatorios de las evaluaciones por DM (mensual/personal/CA), que viven
en memoria, estas se rastrean de forma duradera en la BD de `eval_tracking` ('Evaluaciones
recibidas y completadas'). Así el recordatorio sobrevive a reinicios del bot: cada 2 semanas
que una asignación sigue pendiente (Completada=False) se envía un DM al evaluador.
"""

import logging
import time

from .clients import slack_app
from .i18n import t
from .notion_service import idioma_por_slack_id, obtener_slack_id_por_nombre
from .eval_tracking import marcar_recordatorio_enviado, pendientes_para_recordatorio

# Tipos de evaluación lanzados desde la web que reciben este recordatorio.
_TIPOS_WEB = ("proyecto", "extra")

_UMBRAL_DIAS = 14                 # recuerda cada 2 semanas sin contestar
_INTERVALO_CHEQUEO = 60 * 60      # revisa la BD una vez por hora


def _mensaje(tipo: str, idioma: str, detalle: str) -> str:
    if tipo == "proyecto":
        return t("web.reminder_proyecto", idioma, proyecto=detalle or "")
    return t("web.reminder_extra", idioma)


def _enviar_recordatorio(pendiente: dict) -> None:
    persona = pendiente.get("persona", "")
    slack_id = obtener_slack_id_por_nombre(persona)
    if not slack_id:
        logging.info("Sin Slack ID para '%s'; no se envía recordatorio web", persona)
        return
    dm = slack_app.client.conversations_open(users=[slack_id])
    channel = dm["channel"]["id"]
    idioma = idioma_por_slack_id(slack_id)
    slack_app.client.chat_postMessage(
        channel=channel,
        text=_mensaje(pendiente.get("tipo", ""), idioma, pendiente.get("detalle", "")),
    )
    marcar_recordatorio_enviado(pendiente.get("page_id", ""))


def ciclo_recordatorios_web() -> None:
    """Bucle de fondo: cada hora comprueba evaluaciones de proyecto/extra pendientes y, si
    llevan ≥2 semanas sin contestar (y sin recordar en ese plazo), envía un DM de recordatorio."""
    while True:
        time.sleep(_INTERVALO_CHEQUEO)
        try:
            pendientes = pendientes_para_recordatorio(_TIPOS_WEB, _UMBRAL_DIAS)
        except Exception:
            logging.exception("Error obteniendo pendientes para recordatorio web")
            continue
        for pendiente in pendientes:
            try:
                _enviar_recordatorio(pendiente)
            except Exception:
                logging.exception("Error enviando recordatorio web a '%s'", pendiente.get("persona"))