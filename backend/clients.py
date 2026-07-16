from slack_bolt import App
from notion_client import Client as NotionClient

from . import config
from .ia import ClienteIA

try:
    from anthropic import Anthropic
except ImportError:
    Anthropic = None

try:
    from docx import Document
except ImportError:
    Document = None


slack_app = App(token=config.SLACK_BOT_TOKEN, token_verification_enabled=False)
notion = NotionClient(auth=config.NOTION_TOKEN)
# Envuelto en ClienteIA: mismo `.messages.create(...)`, pero los fallos de la API salen
# como ErrorIA con un mensaje para el usuario (ver backend/ia.py). Sigue siendo None sin
# clave, que es lo que comprueban los `if not anthropic_client` repartidos por el código.
anthropic_client = (
    ClienteIA(Anthropic(api_key=config.ANTHROPIC_API_KEY))
    if Anthropic and config.ANTHROPIC_API_KEY
    else None
)
