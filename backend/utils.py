import re
from datetime import datetime, timedelta, timezone


def normalizar_nombre(valor):
    return " ".join((valor or "").strip().lower().split())


def sanear_fecha_limite(valor, dias_defecto: int = 14) -> str:
    """Normaliza una fecha límite a 'YYYY-MM-DD'.

    Devuelve la fecha dada si es válida con ese formato; si falta o es inválida, usa
    hoy + `dias_defecto` (por defecto 2 semanas)."""
    s = (valor or "").strip()[:10]
    if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        try:
            datetime.strptime(s, "%Y-%m-%d")
            return s
        except ValueError:
            pass
    return (datetime.now(timezone.utc) + timedelta(days=dias_defecto)).date().isoformat()


def slug_archivo(valor):
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "_", valor.strip()).strip("_")
    return slug or "todas"


class _Pendiente:
    """Sentinel: hay audio pero la transcripción todavía no está lista."""


TRANSCRIPCION_PENDIENTE = _Pendiente()


def texto_de_audio(files):
    """Devuelve el texto transcrito si el audio ya está listo.
    Devuelve TRANSCRIPCION_PENDIENTE si el audio existe pero aún se está procesando.
    Devuelve None si no hay archivos de audio.
    """
    for f in (files or []):
        if (f.get("mimetype") or "").startswith("audio/"):
            t = f.get("transcription") or {}
            if t.get("status") == "complete":
                return (t.get("preview") or {}).get("content", "").strip()
            return TRANSCRIPCION_PENDIENTE
    return None
