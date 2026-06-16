import logging
import sys
import threading

from . import config
from .clients import Document
from .slack_bot import enviar_evaluaciones_programadas, start_socket_mode
from .web_server import iniciar_servidor_web


def validar_configuracion():
    if not config.NOTION_PARENT_PAGE_ID:
        print("❌ Falta NOTION_PARENT_PAGE_ID.")
        print('Ejemplo: $env:NOTION_PARENT_PAGE_ID="https://www.notion.so/tu-pagina..."')
        return False
    if not config.ANTHROPIC_API_KEY:
        print("⚠️ Falta ANTHROPIC_API_KEY. La web no podrá generar informes con Claude.")
    if not config.ADMIN_ACCESS_CODE:
        print("⚠️ Falta ADMIN_ACCESS_CODE. Ana no podrá desbloquear la vista administradora.")
    if Document is None:
        print("⚠️ Falta python-docx. Instala: pip install python-docx")
    return True


def main():
    logging.basicConfig(level=logging.INFO)
    if not validar_configuracion():
        sys.exit(1)

    threading.Thread(target=enviar_evaluaciones_programadas, daemon=True).start()
    threading.Thread(target=iniciar_servidor_web, daemon=True).start()

    if config.APP_MODE == "produccion":
        print("🤖 Bot activo en modo producción. Enviará la evaluación los viernes a las 10:00 hora de Madrid.")
    else:
        print(f"🤖 Bot activo en modo prueba. Enviará una evaluación ahora y luego cada {config.INTERVALO_PRUEBA_SEGUNDOS // 60} minutos.")
    if config.REVIEW_BEFORE_SEND:
        print("👀 Revisión previa activada: las evaluaciones quedan pendientes en la web antes de enviarse a Slack.")
    print("📝 Las preguntas se hacen una a una en el hilo y el resultado se guarda en Notion tras confirmación.")
    print(f"📄 Informes disponibles en http://localhost:{config.PUERTO_WEB}")
    start_socket_mode()
