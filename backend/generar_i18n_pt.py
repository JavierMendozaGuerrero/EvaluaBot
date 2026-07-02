"""
Generador de traducciones al portugués (PT) de los catálogos i18n.

Traduce con Claude (es -> pt) TODAS las cadenas de:
  - backend/i18n.py   (dict TEXTOS)   -> escribe backend/i18n_pt.py  (TEXTOS_PT)
  - frontend/src/i18n.js (STRINGS)    -> escribe frontend/src/pt.js   (PT)

El motor i18n ya fusiona esos ficheros por encima (si falta una clave, cae a ES).

Uso (desde la raíz del repo, con el entorno del bot y ANTHROPIC_API_KEY):

    python -m backend.generar_i18n_pt            # genera i18n_pt.py y pt.js
    python -m backend.generar_i18n_pt --backend  # solo backend
    python -m backend.generar_i18n_pt --frontend # solo frontend

Cachea las traducciones en traducciones_i18n_pt.json (mapa es->pt), así re-ejecutar
es barato y puedes EDITAR ese JSON para afinar una traducción y re-generar.
"""

import json
import logging
import os
import re
import sys

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))
except ImportError:
    pass

from .clients import anthropic_client
from .i18n import TEXTOS

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("gen_pt")

_REPO = os.path.dirname(os.path.dirname(__file__))
_CACHE = os.path.join(_REPO, "traducciones_i18n_pt.json")
_TAM_LOTE = 15

_SYSTEM = (
    "Eres un traductor profesional de RRHH. Traduce cada cadena del array JSON que "
    "recibes, del ESPAÑOL al PORTUGUÉS EUROPEO, natural y profesional. Conserva "
    "EXACTAMENTE el formato Slack/markdown (*negrita*, _cursiva_), los saltos de línea, "
    "los emojis y cualquier placeholder entre llaves {asi} (no los traduzcas). "
    "Devuelve ÚNICAMENTE un array JSON de la MISMA longitud y en el MISMO orden, sin "
    "texto adicional ni comentarios."
)


def _cargar_cache() -> dict:
    if os.path.exists(_CACHE):
        try:
            with open(_CACHE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            log.warning("No se pudo leer %s; se empieza de cero.", _CACHE)
    return {}


def _guardar_cache(cache: dict) -> None:
    with open(_CACHE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2, sort_keys=True)


_SYSTEM_UNO = (
    "Eres un traductor profesional de RRHH. Traduce del ESPAÑOL al PORTUGUÉS EUROPEO, "
    "natural y profesional, el texto que recibes. Conserva EXACTAMENTE el formato "
    "(*negrita*, _cursiva_), saltos de línea, emojis, comillas y placeholders entre "
    "llaves {asi}. Responde ÚNICAMENTE con la traducción, sin comillas envolventes ni "
    "explicaciones."
)


def _claude_traduce_uno(texto_es: str) -> str:
    """Traduce UNA cadena en crudo (sin JSON), robusto ante comillas/caracteres especiales."""
    if not anthropic_client:
        return texto_es
    resp = anthropic_client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2000,
        system=_SYSTEM_UNO,
        messages=[{"role": "user", "content": texto_es}],
    )
    out = "".join(b.text for b in resp.content if b.type == "text").strip()
    return out or texto_es


def _claude_traduce_lote(textos: list) -> list:
    """Traduce una lista de cadenas (es->pt) en UNA llamada. Devuelve lista misma longitud."""
    if not anthropic_client:
        log.warning("  [!] Sin cliente de Claude; se dejan en español.")
        return list(textos)
    payload = json.dumps(textos, ensure_ascii=False)
    resp = anthropic_client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=8000,
        system=_SYSTEM,
        messages=[{"role": "user", "content": payload}],
    )
    out = "".join(b.text for b in resp.content if b.type == "text").strip()
    out = re.sub(r"^```(?:json)?\s*|\s*```$", "", out).strip()
    arr = json.loads(out)
    if isinstance(arr, list) and len(arr) == len(textos):
        return [str(x) for x in arr]
    raise ValueError("respuesta con longitud distinta")


def _traducir_todos(textos_es: list, cache: dict) -> None:
    """Rellena la cache con las traducciones que falten (por lotes, con fallback 1 a 1)."""
    faltan = list(dict.fromkeys(t for t in textos_es if t and t not in cache))
    if not faltan:
        return
    log.info("  Traduciendo %d cadenas nuevas...", len(faltan))
    for i in range(0, len(faltan), _TAM_LOTE):
        lote = faltan[i:i + _TAM_LOTE]
        try:
            traducciones = _claude_traduce_lote(lote)
            for src, dst in zip(lote, traducciones):
                cache[src] = dst
        except Exception:
            log.warning("  Lote falló; traduciendo 1 a 1 (en crudo)...")
            for src in lote:
                try:
                    cache[src] = _claude_traduce_uno(src)  # crudo, robusto a comillas
                except Exception:
                    log.exception("  [!] No se pudo traducir; se deja ES: %s", src[:50])
                    cache[src] = src
        _guardar_cache(cache)
        log.info("  ...%d/%d", min(i + _TAM_LOTE, len(faltan)), len(faltan))


# ---------------------------------------------------------------------------
# Backend: TEXTOS -> i18n_pt.py
# ---------------------------------------------------------------------------

def generar_backend(cache: dict) -> None:
    log.info("\n=== Backend (i18n.py -> i18n_pt.py) ===")
    # Se saltan las claves que ya traen 'pt' escrito a mano en i18n.py (p. ej. report.prompt,
    # con directiva de idioma explícita) para no pisarlas con una traducción literal.
    fuentes = {clave: e.get("es", "") for clave, e in TEXTOS.items() if e.get("es") and not e.get("pt")}
    _traducir_todos(list(fuentes.values()), cache)
    pt = {clave: cache.get(es, es) for clave, es in fuentes.items()}
    ruta = os.path.join(os.path.dirname(__file__), "i18n_pt.py")
    with open(ruta, "w", encoding="utf-8") as f:
        f.write('"""Traducciones PT del catálogo backend. GENERADO por generar_i18n_pt.py."""\n\n')
        f.write("TEXTOS_PT = ")
        f.write(json.dumps(pt, ensure_ascii=False, indent=4, sort_keys=True))
        f.write("\n")
    log.info("  Escrito %s (%d claves).", ruta, len(pt))


# ---------------------------------------------------------------------------
# Frontend: STRINGS (i18n.js) -> pt.js
# ---------------------------------------------------------------------------

_RE_ENTRADA = re.compile(r'"([^"]+)":\s*\{\s*es:\s*"((?:[^"\\]|\\.)*)"')


def _extraer_strings_front() -> dict:
    """Extrae {clave: texto_es} de frontend/src/i18n.js (formato de una entrada por línea)."""
    ruta = os.path.join(_REPO, "frontend", "src", "i18n.js")
    with open(ruta, "r", encoding="utf-8") as f:
        txt = f.read()
    entradas = {}
    for m in _RE_ENTRADA.finditer(txt):
        clave = m.group(1)
        crudo = m.group(2)
        try:
            es = json.loads('"' + crudo + '"')  # decodifica escapes JS/JSON
        except Exception:
            es = crudo
        entradas[clave] = es
    return entradas


def generar_frontend(cache: dict) -> None:
    log.info("\n=== Frontend (i18n.js -> pt.js) ===")
    fuentes = _extraer_strings_front()
    if not fuentes:
        log.warning("  No se extrajo ninguna cadena del frontend (¿cambió el formato?).")
        return
    _traducir_todos(list(fuentes.values()), cache)
    pt = {clave: cache.get(es, es) for clave, es in fuentes.items()}
    ruta = os.path.join(_REPO, "frontend", "src", "pt.js")
    with open(ruta, "w", encoding="utf-8") as f:
        f.write("// Traducciones PT del catálogo i18n de la web. GENERADO por backend/generar_i18n_pt.py\n")
        f.write("// Se fusiona sobre STRINGS en i18n.js. Las claves sin PT caen a ES.\n")
        f.write("export const PT = ")
        f.write(json.dumps(pt, ensure_ascii=False, indent=2, sort_keys=True))
        f.write(";\n")
    log.info("  Escrito %s (%d claves).", ruta, len(pt))


def main():
    solo_back = "--backend" in sys.argv
    solo_front = "--frontend" in sys.argv
    cache = _cargar_cache()
    if not solo_front:
        generar_backend(cache)
    if not solo_back:
        generar_frontend(cache)
    _guardar_cache(cache)
    log.info("\nHecho. Cache en %s. Revisa/edita ese JSON y re-ejecuta para afinar.", _CACHE)


if __name__ == "__main__":
    main()
