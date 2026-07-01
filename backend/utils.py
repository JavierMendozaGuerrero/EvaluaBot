import re


def normalizar_nombre(valor):
    return " ".join((valor or "").strip().lower().split())


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
