import re
import unicodedata


def normalizar_nombre(valor):
    # Pliega tildes/diacríticos (NFD + descartar marcas 'Mn') para que las
    # búsquedas de nombres en Notion ignoren los acentos: "Pedrós" == "Pedros".
    texto = " ".join((valor or "").strip().lower().split())
    return "".join(
        char for char in unicodedata.normalize("NFD", texto)
        if unicodedata.category(char) != "Mn"
    )


def slug_archivo(valor):
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "_", valor.strip()).strip("_")
    return slug or "todas"

