import logging
import os
import sys
import threading
from logging.handlers import RotatingFileHandler

from . import config
from .api_server import iniciar_api_backend
from .clients import Document
from .notion_service import aplicar_estetica_notion, inicializar_bbdd_middleoffice
from .ca_reviews import ciclo_envio_ca, ciclo_recordatorios_ca  # noqa: F401 — registra el handler de Slack al importar
from .personal_eval import ciclo_envio_personal, ciclo_recordatorios_personal
from .slack_bot import ciclo_recordatorios_proyecto, enviar_evaluaciones_programadas, start_socket_mode
from .recordatorios_web import ciclo_recordatorios_web


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


def _configurar_logging():
    """Consola como siempre, más un fichero rotativo en dashboard_web/evaluabot.log.

    Sin esto los logs solo viven en la consola del proceso: cuando algo va lento o falla
    en el NAS no queda ni rastro que mirar después, y las líneas [perf] que miden dónde
    se va el tiempo se pierden en cuanto se cierra la terminal.
    """
    raiz = logging.getLogger()
    raiz.setLevel(logging.INFO)
    raiz.addHandler(logging.StreamHandler())
    try:
        os.makedirs(config.CARPETA_WEB, exist_ok=True)
        fichero = RotatingFileHandler(
            os.path.join(config.CARPETA_WEB, "evaluabot.log"),
            maxBytes=5_000_000, backupCount=3, encoding="utf-8",
        )
        fichero.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        raiz.addHandler(fichero)
    except Exception:
        # Un log que no se puede escribir no debe impedir arrancar el bot.
        logging.exception("No se pudo abrir el fichero de log; se sigue solo con la consola")


def main():
    _configurar_logging()
    if not validar_configuracion():
        sys.exit(1)

    try:
        aplicar_estetica_notion()
    except Exception:
        logging.exception("No se pudo aplicar la estetica inicial de Notion")

    try:
        inicializar_bbdd_middleoffice()
    except Exception:
        logging.exception("No se pudo inicializar las BDs de MiddleOffice")

    threading.Thread(target=enviar_evaluaciones_programadas, daemon=True).start()
    threading.Thread(target=ciclo_envio_ca, daemon=True).start()
    threading.Thread(target=ciclo_envio_personal, daemon=True).start()
    threading.Thread(target=ciclo_recordatorios_proyecto, daemon=True).start()
    threading.Thread(target=ciclo_recordatorios_ca, daemon=True).start()
    threading.Thread(target=ciclo_recordatorios_personal, daemon=True).start()
    threading.Thread(target=ciclo_recordatorios_web, daemon=True).start()
    threading.Thread(target=iniciar_api_backend, daemon=True).start()

    if config.APP_MODE == "produccion":
        print("Bot activo en modo produccion. Enviara las evaluaciones segun la fecha configurada en Notion (personal cada 2 semanas, CA y mensuales cada 4 semanas).")
    else:
        print(f"Bot activo en modo prueba. Enviara los 3 hilos ahora y luego cada {config.INTERVALO_PRUEBA_DIAS} dias.")
    print("Las preguntas se hacen una a una en el hilo y el resultado se guarda en Notion tras confirmacion.")
    print(f"API backend disponible en http://localhost:{config.PUERTO_WEB}")
    start_socket_mode()
