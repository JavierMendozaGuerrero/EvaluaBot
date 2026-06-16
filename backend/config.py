import os
from datetime import time as datetime_time
from zoneinfo import ZoneInfo


def env_bool(name, default="false"):
    return os.environ.get(name, default).strip().lower() in {"1", "true", "yes", "si", "sí"}


CHANNEL_ID = "C0BBFRM14SU"

APP_MODE = os.environ.get("APP_MODE", "prueba").strip().lower()
REVIEW_BEFORE_SEND = env_bool("REVIEW_BEFORE_SEND")
INTERVALO_PRUEBA_SEGUNDOS = 300
ZONA_HORARIA_MADRID = ZoneInfo("Europe/Madrid")
DIA_ENVIO_PRODUCCION = 4
HORA_ENVIO_PRODUCCION = datetime_time(10, 0)

PUERTO_WEB = 8000
CARPETA_WEB = "dashboard_web"
PREFIJO_BBDD_EVALUADO = "Evaluaciones - "
FRONTEND_ORIGIN = os.environ.get("FRONTEND_ORIGIN", "http://localhost:5173").strip()
WEB_MODE = os.environ.get("WEB_MODE", "api").strip().lower()

SLACK_BOT_TOKEN = os.environ["SLACK_BOT_TOKEN"]
SLACK_APP_TOKEN = os.environ["SLACK_APP_TOKEN"]
NOTION_TOKEN = os.environ["NOTION_TOKEN"]
NOTION_DATABASE_ID = os.environ["NOTION_DATABASE_ID"]
NOTION_PARENT_PAGE_ID = os.environ.get("NOTION_PARENT_PAGE_ID", "").strip()
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()
ADMIN_NAME = os.environ.get("ADMIN_NAME", "Ana").strip()
ADMIN_ACCESS_CODE = os.environ.get("ADMIN_ACCESS_CODE", "").strip()

PREGUNTAS = [
    {"clave": "evaluado", "texto": "1️⃣ ¿A quién estás evaluando?"},
    {"clave": "proyecto", "texto": "2️⃣ ¿A qué proyecto corresponde esta evaluación?"},
    {
        "clave": "satisfaccion",
        "texto": "3️⃣ ¿Cómo de satisfecho estás con esa persona? (responde un número del 1 al 5)",
    },
    {"clave": "mejor_aspecto", "texto": "4️⃣ Indica el mejor aspecto de esa persona"},
    {"clave": "peor_aspecto", "texto": "5️⃣ Indica el peor aspecto de esa persona"},
]

IGENERIS_CSS = """
  :root { color-scheme: light; --ink: #101010; --muted: #5e5e5e; --line: #d8d8d8; --paper: #ffffff; --soft: #f4f4f1; --accent: #101010; }
  * { box-sizing: border-box; }
  body { margin: 0; font-family: Arial, Helvetica, sans-serif; color: var(--ink); background: var(--paper); }
  .page { min-height: 100vh; padding: 28px clamp(18px, 4vw, 56px) 56px; }
  .nav { display: flex; align-items: center; justify-content: space-between; gap: 24px; padding-bottom: 28px; border-bottom: 1px solid var(--line); }
  .brand { font-size: 24px; font-weight: 800; letter-spacing: -0.02em; text-decoration: none; color: var(--ink); }
  .nav-links { display: flex; align-items: center; gap: 18px; flex-wrap: wrap; }
  .nav-links a { color: var(--ink); text-decoration: none; font-size: 14px; }
  .hero { display: grid; grid-template-columns: minmax(0, 1.08fr) minmax(280px, .92fr); gap: clamp(28px, 6vw, 86px); padding-top: clamp(42px, 8vw, 96px); align-items: start; }
  h1 { margin: 0; font-size: clamp(46px, 9vw, 112px); line-height: .92; letter-spacing: -0.055em; font-weight: 800; }
  h2 { margin: 0 0 18px; font-size: clamp(28px, 4vw, 52px); line-height: 1; letter-spacing: -0.035em; }
  p { color: var(--muted); line-height: 1.55; }
  .kicker { color: var(--ink); font-weight: 700; font-size: 14px; margin-bottom: 18px; }
  .panel { border-top: 1px solid var(--ink); padding-top: 22px; }
  label { display: block; margin: 18px 0 7px; font-size: 13px; color: var(--ink); font-weight: 700; }
  input, select { width: 100%; padding: 13px 0; border: 0; border-bottom: 1px solid var(--line); border-radius: 0; font-size: 16px; background: transparent; color: var(--ink); outline: none; }
  input:focus, select:focus { border-bottom-color: var(--ink); }
  button, .button { display: inline-flex; justify-content: center; align-items: center; gap: 10px; min-height: 48px; padding: 13px 18px; border: 1px solid var(--ink); background: var(--ink); color: white; text-decoration: none; border-radius: 0; cursor: pointer; font-weight: 800; font-size: 14px; }
  button.secondary, .button.secondary { background: white; color: var(--ink); }
  .actions { display: flex; gap: 12px; flex-wrap: wrap; margin-top: 26px; }
  .fine { font-size: 13px; color: var(--muted); }
  .error { color: #9f1239; }
  .card-line { border-top: 1px solid var(--line); padding: 18px 0; }
  @media (max-width: 820px) { .hero { grid-template-columns: 1fr; } .nav { align-items: flex-start; flex-direction: column; } }
"""
