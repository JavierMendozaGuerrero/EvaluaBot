"""
Migración de UN SOLO USO: internacionaliza (ES/EN) todas las BDs de preguntas y
criterios en Notion.

Por cada BD objetivo:
  1. Añade la columna «Idioma» (select con opciones ES / EN) si no existe.
  2. Marca como «ES» las filas que aún no tengan idioma.
  3. Crea una fila «EN» por cada fila ES (copiando las columnas no traducibles:
     Clave, Tipo, Orden, Categoría, Opciones…) con el TEXTO traducido al inglés.

Uso (desde la raíz del repo, con el entorno del bot cargado):

    python -m backend.migracion_idioma_preguntas            # DRY-RUN: no escribe nada.
    python -m backend.migracion_idioma_preguntas --apply    # Escribe en Notion.

El dry-run traduce con Claude y guarda un mapa {texto_es: texto_en} en
«traducciones_idioma.json» (raíz del repo). Puedes REVISAR y EDITAR ese archivo
antes de ejecutar con --apply; el --apply reutiliza esas traducciones tal cual.

Es idempotente: la clave de cada fila (Clave/Tipo/Orden/Categoría/Criterio) NO se
traduce, así que volver a ejecutar no duplica las filas EN ya creadas.
"""

import json
import logging
import os
import sys

# Cargar .env de la raíz del repo ANTES de importar el backend (config exige NOTION_TOKEN al importarse).
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))
except ImportError:
    pass  # python-dotenv no instalado; las vars deben estar ya en el entorno

from .clients import notion, anthropic_client
from . import notion_service as ns
from . import project_evals as pe

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("migracion_idioma")

_RUTA_TRADUCCIONES = os.path.join(os.path.dirname(os.path.dirname(__file__)), "traducciones_idioma.json")

_SYSTEM_TRAD = (
    "Eres un traductor profesional de RRHH. Traduce del español al inglés natural y "
    "profesional el texto de evaluación que se te da. Conserva el formato de Slack "
    "(*negrita*, _cursiva_), los saltos de línea, los emojis y cualquier placeholder "
    "entre llaves {asi}. Responde ÚNICAMENTE con la traducción, sin comillas ni "
    "explicaciones."
)


# ---------------------------------------------------------------------------
# Traducciones (cacheadas en JSON, editable por el usuario)
# ---------------------------------------------------------------------------

def _cargar_traducciones() -> dict:
    if os.path.exists(_RUTA_TRADUCCIONES):
        try:
            with open(_RUTA_TRADUCCIONES, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            log.warning("No se pudo leer %s; se empezará de cero.", _RUTA_TRADUCCIONES)
    return {}


def _guardar_traducciones(mapa: dict) -> None:
    with open(_RUTA_TRADUCCIONES, "w", encoding="utf-8") as f:
        json.dump(mapa, f, ensure_ascii=False, indent=2, sort_keys=True)


def _traducir_con_claude(texto_es: str) -> str:
    if not anthropic_client:
        log.warning("  [!] Sin cliente de Claude; se deja el texto en español.")
        return texto_es
    try:
        resp = anthropic_client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1500,
            system=_SYSTEM_TRAD,
            messages=[{"role": "user", "content": texto_es}],
        )
        out = "".join(b.text for b in resp.content if b.type == "text").strip()
        return out or texto_es
    except Exception:
        log.exception("  [!] Error traduciendo; se deja el texto en español.")
        return texto_es


class Traductor:
    def __init__(self):
        self.mapa = _cargar_traducciones()
        self.nuevas = 0

    def traducir(self, texto_es: str) -> str:
        texto_es = (texto_es or "").strip()
        if not texto_es:
            return ""
        if texto_es in self.mapa and self.mapa[texto_es].strip():
            return self.mapa[texto_es]
        en = _traducir_con_claude(texto_es)
        self.mapa[texto_es] = en
        self.nuevas += 1
        return en

    def guardar(self):
        _guardar_traducciones(self.mapa)


# ---------------------------------------------------------------------------
# Utilidades de propiedades de Notion
# ---------------------------------------------------------------------------

def _plain(prop: dict, kind: str) -> str:
    """Texto plano de una propiedad title/rich_text."""
    if not prop:
        return ""
    return "".join(t.get("plain_text", "") for t in prop.get(kind, [])).strip()


def _valor_clave(props: dict, campos: list) -> str:
    """Clave estable de una fila (a partir de campos NO traducibles)."""
    partes = []
    for c in campos:
        p = props.get(c, {})
        tipo = p.get("type")
        if tipo == "number":
            n = p.get("number")
            partes.append("" if n is None else str(n))
        else:
            partes.append(ns._texto_propiedad(props, c))
    return "||".join(partes)


def _prop_a_payload(prop: dict):
    """Convierte una propiedad LEÍDA en payload de escritura (para copiarla tal cual)."""
    tipo = prop.get("type")
    if tipo == "title":
        return {"title": [{"type": "text", "text": {"content": _plain(prop, "title")}}]}
    if tipo == "rich_text":
        return {"rich_text": [{"type": "text", "text": {"content": _plain(prop, "rich_text")}}]}
    if tipo == "select":
        sel = prop.get("select")
        return {"select": {"name": sel["name"]} if sel else None}
    if tipo == "multi_select":
        return {"multi_select": [{"name": o.get("name", "")} for o in prop.get("multi_select", [])]}
    if tipo == "number":
        return {"number": prop.get("number")}
    if tipo == "checkbox":
        return {"checkbox": bool(prop.get("checkbox", False))}
    if tipo == "url":
        return {"url": prop.get("url")}
    if tipo == "date":
        return {"date": prop.get("date")}
    # Tipos de solo lectura (formula, rollup, created_time, people…) se ignoran.
    return None


def _idioma_de_fila(props: dict) -> str:
    """'es' | 'en' | '' (vacío = sin marcar)."""
    crudo = ns._texto_propiedad(props, "Idioma")
    if not crudo:
        return ""
    return ns._normalizar_idioma(crudo)


def _leer_filas(db_id: str) -> list:
    filas, cursor = [], None
    while True:
        kwargs = {"page_size": 100}
        if cursor:
            kwargs["start_cursor"] = cursor
        resp = ns._query_bbdd(db_id, **kwargs)
        filas.extend(resp.get("results", []))
        if not resp.get("has_more"):
            break
        cursor = resp.get("next_cursor")
    return filas


def _asegurar_columna_idioma(db_id: str, aplicar: bool) -> bool:
    """Añade la columna Idioma (select ES/EN) si falta. Devuelve True si (ya) existe/creada."""
    schema = {"Idioma": {"select": {"options": [{"name": "ES"}, {"name": "EN"}]}}}
    try:
        if ns._usa_data_sources():
            bbdd = notion.data_sources.retrieve(data_source_id=db_id)
            existe = "Idioma" in bbdd.get("properties", {})
            if not existe and aplicar:
                notion.data_sources.update(data_source_id=db_id, properties=schema)
        else:
            bbdd = notion.databases.retrieve(database_id=db_id)
            existe = "Idioma" in bbdd.get("properties", {})
            if not existe and aplicar:
                notion.databases.update(database_id=db_id, properties=schema)
        return existe
    except Exception:
        log.exception("  [!] No se pudo comprobar/crear la columna Idioma")
        return False


# ---------------------------------------------------------------------------
# Migración de una BD
# ---------------------------------------------------------------------------

def _migrar_bbdd(nombre: str, db_id: str, campos_traducir: list, campos_clave: list,
                 traductor: Traductor, aplicar: bool) -> None:
    """campos_traducir: [(prop, 'title'|'rich_text')]. campos_clave: [prop, ...] (no traducibles)."""
    log.info("\n=== %s ===", nombre)
    if not db_id:
        log.info("  [SKIP] No se encontró la base de datos.")
        return

    existia = _asegurar_columna_idioma(db_id, aplicar)
    log.info("  Columna Idioma: %s", "ya existía" if existia else ("creada" if aplicar else "SE CREARÍA"))

    try:
        filas = _leer_filas(db_id)
    except Exception:
        log.exception("  [!] Error leyendo filas")
        return

    es_rows, sin_marcar, en_keys = [], [], set()
    for fila in filas:
        props = fila.get("properties", {})
        idi = _idioma_de_fila(props)
        if idi == "en":
            en_keys.add(_valor_clave(props, campos_clave))
        else:
            es_rows.append(fila)
            if idi == "":
                sin_marcar.append(fila)

    # 1) Marcar ES las filas sin idioma
    for fila in sin_marcar:
        if aplicar:
            try:
                notion.pages.update(page_id=fila["id"], properties={"Idioma": {"select": {"name": "ES"}}})
            except Exception:
                log.exception("  [!] Error marcando ES una fila")
    log.info("  Filas marcadas como ES: %d %s", len(sin_marcar), "" if aplicar else "(se marcarían)")

    # 2) Crear filas EN
    creadas, ya_existen, traducidas = 0, 0, 0
    nombres_traducir = {p for p, _ in campos_traducir}
    for fila in es_rows:
        props = fila.get("properties", {})
        clave = _valor_clave(props, campos_clave)
        if clave in en_keys:
            ya_existen += 1
            continue

        payload = {}
        # Copiar columnas NO traducibles (incluye la clave/título si no se traduce)
        for pname, pobj in props.items():
            if pname in nombres_traducir or pname == "Idioma":
                continue
            pl = _prop_a_payload(pobj)
            if pl is not None:
                payload[pname] = pl
        # Traducir columnas de texto
        for pname, kind in campos_traducir:
            es_txt = _plain(props.get(pname), kind)
            if not es_txt:
                continue
            en_txt = traductor.traducir(es_txt)
            traducidas += 1
            payload[pname] = {kind: [{"type": "text", "text": {"content": en_txt}}]}
        payload["Idioma"] = {"select": {"name": "EN"}}

        if aplicar:
            try:
                ns._crear_pagina_en_bbdd(db_id, payload)
                creadas += 1
            except Exception:
                log.exception("  [!] Error creando fila EN (clave=%s)", clave)
        else:
            creadas += 1  # contaría como "se crearía"

    log.info("  Filas EN %s: %d | ya existían: %d | textos traducidos: %d",
             "creadas" if aplicar else "que se crearían", creadas, ya_existen, traducidas)


# ---------------------------------------------------------------------------
# Objetivos
# ---------------------------------------------------------------------------

def _construir_objetivos() -> list:
    """[(nombre, db_id, campos_traducir, campos_clave)]."""
    objetivos = []

    # 1. Preguntas Negocio (mensual). Texto = título; clave = Tipo+Clave.
    objetivos.append(("Preguntas Negocio", ns._obtener_o_crear_bbdd_preguntas(),
                      [("Texto", "title")], ["Tipo", "Clave"]))

    # 2. Preguntas MiddleOffice. Texto = rich_text; clave = Clave (título).
    objetivos.append(("Preguntas MiddleOffice", ns._obtener_o_crear_bbdd_preguntas_mo(),
                      [("Texto", "rich_text")], ["Clave"]))

    # 3. Preguntas Palantir. Texto = rich_text; clave = Tipo+Clave.
    objetivos.append(("Preguntas Palantir", ns._obtener_o_crear_bbdd_preguntas_palantir(),
                      [("Texto", "rich_text")], ["Tipo", "Clave"]))

    # 4. Preguntas evaluación personal. Texto = rich_text; clave = Clave (título).
    db_personal = ns._buscar_o_crear_bbdd_en_personales(
        "Preguntas", ns._PROPS_PERSONAL_PREGUNTAS, ns._cache_personal_preguntas_db,
        poblar=ns._poblar_bbdd_preguntas_personal,
    )
    objetivos.append(("Preguntas evaluación personal", db_personal,
                      [("Texto", "rich_text")], ["Clave"]))

    # 5. Preguntas seguimiento CA. Texto = rich_text; clave = Clave (título).
    objetivos.append(("Preguntas seguimiento CA", ns._obtener_o_crear_bbdd_preguntas_ca(),
                      [("Texto", "rich_text")], ["Clave"]))

    # 6. Evaluaciones de proyecto (4). Texto = título; clave = Categoria+Orden.
    for tipo_clave, nombre_bbdd in pe.TIPOS_EVALUACION.items():
        objetivos.append((f"Proyecto · {nombre_bbdd}", pe._obtener_o_crear_bbdd_preguntas_tipo(tipo_clave),
                          [("Texto", "title")], ["Categoria", "Orden"]))

    # 7. Criterios de evaluaciones (3 grupos). Se traducen SOLO las columnas de nivel;
    #    'Criterio' (título) se mantiene como clave estable (no se traduce).
    niveles = [("Analista", "rich_text"), ("Asociado", "rich_text"),
               ("Asociado Sr", "rich_text"), ("Manager", "rich_text")]
    for grupo in ("Negocio", "Palantir", "MiddleOffice"):
        objetivos.append((f"Criterios · {grupo}", ns._obtener_db_criterios(grupo),
                          niveles, ["Criterio"]))

    # 8. Ejemplos de Guia para bot. El título (tipo) es la clave estable (no se traduce);
    #    la primera columna rich_text (el ejemplo) se traduce. Nombres de columna dinámicos.
    db_ejemplos = ns._obtener_db_ejemplos()
    if db_ejemplos:
        title_prop, rt_prop = _props_ejemplos(db_ejemplos)
        if title_prop and rt_prop:
            objetivos.append(("Ejemplos de Guia para bot", db_ejemplos,
                              [(rt_prop, "rich_text")], [title_prop]))
        else:
            log.warning("Ejemplos: no se detectaron columnas título/rich_text; se omite.")

    return objetivos


def _props_ejemplos(db_id: str):
    """Detecta (nombre_columna_titulo, nombre_columna_rich_text_ejemplo) de la BD de ejemplos."""
    try:
        if ns._usa_data_sources():
            bbdd = notion.data_sources.retrieve(data_source_id=db_id)
        else:
            bbdd = notion.databases.retrieve(database_id=db_id)
        title_prop, rt_prop = None, None
        for nombre, meta in bbdd.get("properties", {}).items():
            tipo = meta.get("type")
            if tipo == "title" and not title_prop:
                title_prop = nombre
            elif tipo == "rich_text" and not rt_prop:
                rt_prop = nombre
        return title_prop, rt_prop
    except Exception:
        logging.exception("No se pudieron leer las columnas de la BD de ejemplos")
        return None, None


def main():
    aplicar = "--apply" in sys.argv
    log.info("MIGRACIÓN IDIOMA — modo: %s", "APPLY (escribe en Notion)" if aplicar else "DRY-RUN (no escribe)")
    if not aplicar:
        log.info("Revisa/edita %s antes de ejecutar con --apply.\n", _RUTA_TRADUCCIONES)

    traductor = Traductor()
    for nombre, db_id, campos_trad, campos_clave in _construir_objetivos():
        try:
            _migrar_bbdd(nombre, db_id, campos_trad, campos_clave, traductor, aplicar)
        except Exception:
            log.exception("[!] Error migrando '%s'", nombre)

    traductor.guardar()
    log.info("\nTraducciones guardadas en %s (%d nuevas en esta ejecución).",
             _RUTA_TRADUCCIONES, traductor.nuevas)
    if not aplicar:
        log.info("DRY-RUN terminado. Nada escrito en Notion. Ejecuta con --apply cuando lo hayas revisado.")


if __name__ == "__main__":
    main()