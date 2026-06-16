from slack_bolt import App
from notion_client import Client as NotionClient

from . import config

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
anthropic_client = Anthropic(api_key=config.ANTHROPIC_API_KEY) if Anthropic and config.ANTHROPIC_API_KEY else None
