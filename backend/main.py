import logging
import sys
import threading

from . import config
from .api_server import iniciar_api_backend
from .clients import Document
from .notion_service import aplicar_estetica_notion
from .ca_reviews import ciclo_envio_ca, ciclo_recordatorios_ca  # noqa: F401 — registra el handler de Slack al importar
from .slack_bot import ciclo_recordatorios_proyecto, enviar_evaluaciones_programadas, start_socket_mode
from .web_server import iniciar_servidor_web


def validar_configuracion():
    if not config.NOTION_PARENT_PAGE_ID:
        print("Falta NOTION_PARENT_PAGE_ID.")
        print('Ejemplo: $env:NOTION_PARENT_PAGE_ID="https://www.notion.so/tu-pagina..."')
        return False
    if not config.ANTHROPIC_API_KEY:
        print("Falta ANTHROPIC_API_KEY. La web no podra generar informes con Claude.")
    if Document is None:
        print("Falta python-docx. Instala: pip install python-docx")
    return True


def main():
    logging.basicConfig(level=logging.INFO)
    if not validar_configuracion():
        sys.exit(1)

    try:
        aplicar_estetica_notion()
    except Exception:
        logging.exception("No se pudo aplicar la estetica inicial de Notion")

    threading.Thread(target=enviar_evaluaciones_programadas, daemon=True).start()
    threading.Thread(target=ciclo_envio_ca, daemon=True).start()
    threading.Thread(target=ciclo_recordatorios_proyecto, daemon=True).start()
    threading.Thread(target=ciclo_recordatorios_ca, daemon=True).start()
    servidor_web = iniciar_servidor_web if config.WEB_MODE == "legacy" else iniciar_api_backend
    threading.Thread(target=servidor_web, daemon=True).start()

    if config.APP_MODE == "produccion":
        print("Bot activo en modo produccion. Enviara la evaluacion los viernes a las 10:00 hora de Madrid.")
    else:
        minutos = config.INTERVALO_PRUEBA_SEGUNDOS // 60
        print(f"Bot activo en modo prueba. Enviara una evaluacion ahora y luego cada {minutos} minutos.")
    print("Las preguntas se hacen una a una en el hilo y el resultado se guarda en Notion tras confirmacion.")
    if config.WEB_MODE == "legacy":
        print(f"Web legacy disponible en http://localhost:{config.PUERTO_WEB}")
    else:
        print(f"API backend disponible en http://localhost:{config.PUERTO_WEB}")
    start_socket_mode()
