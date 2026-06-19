import os
from datetime import time as datetime_time
from zoneinfo import ZoneInfo


BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def env_bool(name, default="false"):
    return os.environ.get(name, default).strip().lower() in {"1", "true", "yes", "si", "sí"}


CHANNEL_ID = os.environ.get("SLACK_CHANNEL_ID", "C0BBFRM14SU")

APP_MODE = os.environ.get("APP_MODE", "prueba").strip().lower()
INTERVALO_PRUEBA_SEGUNDOS = 300
INTERVALO_CA_SEGUNDOS = int(os.environ.get("INTERVALO_CA_SEGUNDOS", "120"))
ZONA_HORARIA_MADRID = ZoneInfo("Europe/Madrid")
DIA_ENVIO_PRODUCCION = 4
HORA_ENVIO_PRODUCCION = datetime_time(10, 0)

PUERTO_WEB = int(os.environ.get("PUERTO_WEB", "8000"))
CARPETA_WEB = os.path.join(BASE_DIR, "dashboard_web")
PREFIJO_BBDD_EVALUADO = "Evaluaciones - "
FRONTEND_ORIGIN = os.environ.get("FRONTEND_ORIGIN", "http://localhost:5173").strip()
APP_PUBLIC_URL = os.environ.get("APP_PUBLIC_URL", FRONTEND_ORIGIN).strip().rstrip("/")
WEB_MODE = os.environ.get("WEB_MODE", "api").strip().lower()
SMTP_HOST = os.environ.get("SMTP_HOST", "").strip()
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "").strip()
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "").strip()
SMTP_FROM = os.environ.get("SMTP_FROM", SMTP_USER).strip()
SMTP_USE_TLS = env_bool("SMTP_USE_TLS", "true")
INSTRUCCIONES_RESPONDER_EN_HILO = (
    "\n\nResponde siempre en el hilo de esta notificación, no en el canal principal. "
    "Aquí solo mando notificaciones cuando toca evaluar. "
    "No soy un bot inteligente: solo registro respuestas simples."
)

def _require_env(name):
    value = os.environ.get(name)
    if value is None:
        raise SystemExit(f"ERROR: falta la variable de entorno {name}. Configúrala antes de iniciar.")
    return value


SLACK_BOT_TOKEN = _require_env("SLACK_BOT_TOKEN")
SLACK_APP_TOKEN = _require_env("SLACK_APP_TOKEN")
NOTION_TOKEN = _require_env("NOTION_TOKEN")
NOTION_DATABASE_ID = _require_env("NOTION_DATABASE_ID")
NOTION_EMPLOYEES_DATABASE_ID = os.environ.get("NOTION_EMPLOYEES_DATABASE_ID", NOTION_DATABASE_ID).strip()
NOTION_DATA_LISTS_PAGE_NAME = os.environ.get("NOTION_DATA_LISTS_PAGE_NAME", "Listas de datos").strip()
NOTION_INDIVIDUAL_EVALUATIONS_PAGE_NAME = os.environ.get("NOTION_INDIVIDUAL_EVALUATIONS_PAGE_NAME", "Evaluaciones Individuales").strip()
NOTION_CA_TRACKING_PAGE_NAME = os.environ.get("NOTION_CA_TRACKING_PAGE_NAME", "Seguimiento CA").strip()
NOTION_EMPLOYEES_DATABASE_NAME = os.environ.get("NOTION_EMPLOYEES_DATABASE_NAME", "Lista de empleados").strip()
NOTION_USERS_DATABASE_ID = os.environ.get("NOTION_USERS_DATABASE_ID", "").strip()
NOTION_USERS_DATABASE_NAME = os.environ.get("NOTION_USERS_DATABASE_NAME", "Usuarios web").strip()
NOTION_PARENT_PAGE_ID = os.environ.get("NOTION_PARENT_PAGE_ID", "").strip()
NOTION_ANNUAL_DATABASE_ID = os.environ.get("NOTION_ANNUAL_DATABASE_ID", "").strip()
NOTION_ANNUAL_DATABASE_NAME = os.environ.get("NOTION_ANNUAL_DATABASE_NAME", "Evaluaciones anuales").strip()
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()
PREGUNTAS = [
    {"clave": "proyecto", "texto": "1️⃣ ¿En qué proyecto estás trabajando ahora?"},
    {"clave": "evaluado", "texto": "2️⃣ Indica el nombre del miembro del proyecto"},
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
