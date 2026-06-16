import re


def normalizar_nombre(valor):
    return " ".join((valor or "").strip().lower().split())


def slug_archivo(valor):
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "_", valor.strip()).strip("_")
    return slug or "todas"
