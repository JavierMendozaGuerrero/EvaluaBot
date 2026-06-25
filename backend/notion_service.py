import logging
import os
import re
import threading
import time
import unicodedata
from datetime import datetime, timezone
from difflib import SequenceMatcher

from . import config
from .clients import notion
from .state import bbdd_por_evaluado, lock
from .utils import normalizar_nombre


def _titulo_bbdd(titulo):
    return f"{config.PREFIJO_BBDD_EVALUADO}{titulo.strip()}"


def _extraer_titulo_bbdd(bbdd):
    if bbdd.get("name"):
        return bbdd["name"].strip()
    return "".join(parte.get("plain_text", "") for parte in bbdd.get("title", [])).strip()


def _extraer_titulo_pagina(pagina):
    for propiedad in pagina.get("properties", {}).values():
        if propiedad.get("type") == "title":
            return " ".join(item.get("plain_text", "") for item in propiedad.get("title", [])).strip()
    return ""


def _propiedades_bbdd_evaluaciones():
    return {
        "Name": {"title": {}},
        "Evaluador": {"rich_text": {}},
        "Proyecto": {"rich_text": {}},
        "Fecha": {"date": {}},
        "Area": {"select": {"options": [
            {"name": "Negocio"}, {"name": "MiddleOffice"}, {"name": "Palantir"},
        ]}},
        "Valoración de superiores": {"rich_text": {}},
        "Valoración de iguales": {"rich_text": {}},
        "Valoración de inferiores": {"rich_text": {}},
        "Justificación de superiores": {"rich_text": {}},
        "Justificación de iguales": {"rich_text": {}},
        "Justificación de inferiores": {"rich_text": {}},
    }


def _normalizar_notion_id(valor):
    limpio = valor.strip().replace("-", "")
    coincidencias = re.findall(r"[0-9a-fA-F]{32}", limpio)
    return coincidencias[-1] if coincidencias else limpio


def _data_source_id(resultado_bbdd):
    if resultado_bbdd.get("object") == "data_source":
        return resultado_bbdd["id"]
    data_sources = resultado_bbdd.get("data_sources", [])
    return data_sources[0]["id"] if data_sources else resultado_bbdd["id"]


def _usa_data_sources():
    return hasattr(notion, "data_sources") and not hasattr(notion.databases, "query")


def _tipo_objeto_busqueda_bbdd():
    return "data_source" if _usa_data_sources() else "database"


def _coincide_parent_bbdd(bbdd, parent):
    if bbdd.get("object") == "data_source":
        return True
    return bbdd.get("parent") == parent


def _query_bbdd(database_id, **kwargs):
    if hasattr(notion.databases, "query"):
        return notion.databases.query(database_id=database_id, **kwargs)
    return notion.data_sources.query(data_source_id=database_id, **kwargs)


def _titulo_child_database(bloque):
    return (bloque.get("child_database") or {}).get("title", "").strip()


def _titulo_child_page(bloque):
    return (bloque.get("child_page") or {}).get("title", "").strip()


def _target_link_to_page(bloque):
    link = bloque.get("link_to_page") or {}
    if link.get("type") == "page_id":
        return link.get("page_id")
    if link.get("type") == "database_id":
        return link.get("database_id")
    return None


def _menciones_pagina_en_bloque(bloque):
    contenido = bloque.get(bloque.get("type", ""), {})
    for item in contenido.get("rich_text", []):
        mention = item.get("mention") or {}
        if mention.get("type") == "page":
            yield item.get("plain_text", ""), mention.get("page", {}).get("id")
        if mention.get("type") == "database":
            yield item.get("plain_text", ""), mention.get("database", {}).get("id")


def _iter_blocks(page_id):
    cursor = None
    while True:
        kwargs = {"block_id": page_id, "page_size": 100}
        if cursor:
            kwargs["start_cursor"] = cursor
        resp = notion.blocks.children.list(**kwargs)
        for bloque in resp.get("results", []):
            yield bloque
        if not resp.get("has_more"):
            break
        cursor = resp.get("next_cursor")


def _page_or_database_link_by_name(page_id, nombre_objetivo):
    objetivo = normalizar_nombre(nombre_objetivo)
    if not objetivo:
        return None
    for bloque in _iter_blocks(page_id):
        if bloque.get("type") == "child_page":
            titulo = normalizar_nombre(_titulo_child_page(bloque))
            if titulo == objetivo or objetivo in titulo:
                return bloque["id"]
        if bloque.get("type") == "child_database":
            titulo = normalizar_nombre(_titulo_child_database(bloque))
            if titulo == objetivo or objetivo in titulo:
                return bloque["id"]
        if bloque.get("type") == "link_to_page":
            target_id = _target_link_to_page(bloque)
            if not target_id:
                continue
            try:
                pagina = notion.pages.retrieve(page_id=target_id)
                titulo = normalizar_nombre(_extraer_titulo_pagina(pagina))
            except Exception:
                try:
                    db = notion.databases.retrieve(database_id=target_id)
                    titulo = normalizar_nombre(_extraer_titulo_bbdd(db))
                except Exception:
                    titulo = ""
            if titulo == objetivo or objetivo in titulo:
                return target_id
        for texto, target_id in _menciones_pagina_en_bloque(bloque):
            if not target_id:
                continue
            titulo = normalizar_nombre(texto)
            if titulo == objetivo or objetivo in titulo:
                return target_id
    return None


def _elegir_child_database(bases, nombre_objetivo):
    objetivo = normalizar_nombre(nombre_objetivo)
    if objetivo:
        for base in bases:
            if normalizar_nombre(_titulo_child_database(base)) == objetivo:
                return base
        for base in bases:
            if objetivo in normalizar_nombre(_titulo_child_database(base)):
                return base

    palabras_lista = ("lista de empleados", "empleados", "emplead", "miembros", "equipo", "staff", "people")
    for palabra in palabras_lista:
        for base in bases:
            titulo = normalizar_nombre(_titulo_child_database(base))
            if palabra in titulo:
                return base
    return None


def _child_database_preferida(page_id):
    hijos = notion.blocks.children.list(block_id=page_id, page_size=100)
    bases = [item for item in hijos.get("results", []) if item.get("type") == "child_database"]
    if not bases:
        raise RuntimeError("No se encontro ninguna base/lista dentro de la pagina de Notion indicada.")

    elegida = _elegir_child_database(bases, config.NOTION_EMPLOYEES_DATABASE_NAME)
    if elegida:
        logging.info("Lista de empleados elegida dentro de Notion: %s", _titulo_child_database(elegida) or elegida["id"])
        return elegida

    titulos = ", ".join(_titulo_child_database(base) or base["id"] for base in bases)
    raise RuntimeError(
        f"No se encontro la lista de empleados '{config.NOTION_EMPLOYEES_DATABASE_NAME}'. "
        f"Listas disponibles: {titulos}"
    )


def _pagina_objetivo_en_bbdd(database_id, nombre_objetivo):
    objetivo = normalizar_nombre(nombre_objetivo)
    if not objetivo:
        return None
    cursor = None
    while True:
        kwargs = {"page_size": 100}
        if cursor:
            kwargs["start_cursor"] = cursor
        resp = _query_bbdd(database_id, **kwargs)
        for pagina in resp.get("results", []):
            titulo = normalizar_nombre(_extraer_titulo_pagina(pagina))
            if titulo == objetivo or objetivo in titulo:
                return pagina
        if not resp.get("has_more"):
            return None
        cursor = resp.get("next_cursor")


def _buscar_objeto_notion_por_nombre(nombre_objetivo):
    objetivo = normalizar_nombre(nombre_objetivo)
    if not objetivo:
        return None
    for tipo in (_tipo_objeto_busqueda_bbdd(), "page"):
        try:
            resp = notion.search(query=nombre_objetivo, filter={"value": tipo, "property": "object"}, page_size=25)
        except Exception:
            continue
        for item in resp.get("results", []):
            titulo = normalizar_nombre(_extraer_titulo_bbdd(item) if item.get("object") in {"database", "data_source"} else _extraer_titulo_pagina(item))
            if titulo == objetivo or objetivo in titulo:
                logging.info("Objeto de Notion encontrado por busqueda: %s", nombre_objetivo)
                return item["id"]
    return None


def _resolver_ruta_lista_empleados(origen_id):
    origen_id = _normalizar_notion_id(origen_id)
    pagina_listas_id = _page_or_database_link_by_name(origen_id, config.NOTION_DATA_LISTS_PAGE_NAME)
    if pagina_listas_id:
        logging.info("Pagina de listas de datos encontrada: %s", config.NOTION_DATA_LISTS_PAGE_NAME)
        _decorar_pagina_notion(pagina_listas_id, config.NOTION_DATA_LISTS_PAGE_NAME)
        lista_empleados_id = _page_or_database_link_by_name(pagina_listas_id, config.NOTION_EMPLOYEES_DATABASE_NAME)
        if lista_empleados_id:
            logging.info("Link a lista de empleados encontrado: %s", config.NOTION_EMPLOYEES_DATABASE_NAME)
            return lista_empleados_id
        return pagina_listas_id

    lista_empleados_id = _page_or_database_link_by_name(origen_id, config.NOTION_EMPLOYEES_DATABASE_NAME)
    if lista_empleados_id:
        logging.info("Link a lista de empleados encontrado: %s", config.NOTION_EMPLOYEES_DATABASE_NAME)
        return lista_empleados_id

    lista_empleados_id = _buscar_objeto_notion_por_nombre(config.NOTION_EMPLOYEES_DATABASE_NAME)
    if lista_empleados_id:
        return lista_empleados_id

    raise RuntimeError(
        f"No se encontro '{config.NOTION_EMPLOYEES_DATABASE_NAME}' desde el origen configurado. "
        "Configura NOTION_EMPLOYEES_DATABASE_ID con la URL o ID exacto de la lista de empleados."
    )


def _retrieve_bbdd(database_id):
    database_id = _resolver_ruta_lista_empleados(database_id)
    if _usa_data_sources():
        try:
            try:
                db = notion.data_sources.retrieve(data_source_id=database_id)
            except Exception:
                db = notion.databases.retrieve(database_id=database_id)
        except Exception:
            child_db = _child_database_preferida(database_id)
            db = notion.databases.retrieve(database_id=child_db["id"])
        data_source_id = _data_source_id(db)
        data_source = notion.data_sources.retrieve(data_source_id=data_source_id)
        if normalizar_nombre(_extraer_titulo_bbdd(data_source)) != normalizar_nombre(config.NOTION_EMPLOYEES_DATABASE_NAME):
            pagina = _pagina_objetivo_en_bbdd(data_source_id, config.NOTION_EMPLOYEES_DATABASE_NAME)
            if pagina:
                child_db = _child_database_preferida(pagina["id"])
                db = notion.databases.retrieve(database_id=child_db["id"])
                data_source_id = _data_source_id(db)
                data_source = notion.data_sources.retrieve(data_source_id=data_source_id)
        return data_source_id, data_source
    try:
        db = notion.databases.retrieve(database_id=database_id)
    except Exception:
        child_db = _child_database_preferida(database_id)
        return child_db["id"], notion.databases.retrieve(database_id=child_db["id"])
    if normalizar_nombre(_extraer_titulo_bbdd(db)) != normalizar_nombre(config.NOTION_EMPLOYEES_DATABASE_NAME):
        pagina = _pagina_objetivo_en_bbdd(database_id, config.NOTION_EMPLOYEES_DATABASE_NAME)
        if pagina:
            child_db = _child_database_preferida(pagina["id"])
            return child_db["id"], notion.databases.retrieve(database_id=child_db["id"])
    return database_id, db


def _parent_para_nueva_pagina(database_id):
    return {"data_source_id": database_id} if _usa_data_sources() else {"database_id": database_id}


def _crear_pagina_en_bbdd(database_id, properties):
    return notion.pages.create(parent=_parent_para_nueva_pagina(database_id), properties=properties)


def asegurar_propiedades_bbdd(database_id):
    necesarias = _propiedades_bbdd_evaluaciones()
    if _usa_data_sources():
        bbdd = notion.data_sources.retrieve(data_source_id=database_id)
        faltantes = {k: v for k, v in necesarias.items() if k not in bbdd.get("properties", {})}
        if faltantes:
            notion.data_sources.update(data_source_id=database_id, properties=faltantes)
        return

    bbdd = notion.databases.retrieve(database_id=database_id)
    faltantes = {k: v for k, v in necesarias.items() if k not in bbdd.get("properties", {})}
    if faltantes:
        notion.databases.update(database_id=database_id, properties=faltantes)


def _parent_bbdd_referencia():
    if config.NOTION_PARENT_PAGE_ID:
        return {"type": "page_id", "page_id": _normalizar_notion_id(config.NOTION_PARENT_PAGE_ID)}
    bbdd_referencia = notion.databases.retrieve(database_id=config.NOTION_DATABASE_ID)
    parent = bbdd_referencia.get("parent", {})
    if parent.get("type") != "page_id":
        raise RuntimeError("Configura NOTION_PARENT_PAGE_ID con la página donde crear las bases nuevas.")
    return {"type": "page_id", "page_id": parent["page_id"]}


_NOTION_PAGE_STYLE = {
    config.NOTION_DATA_LISTS_PAGE_NAME: {
        "emoji": "🗂️",
        "color": "green",
        "title": "Zona operativa para administracion",
        "body": "Aqui viven las listas maestras que se mantienen a mano: empleados, usuarios, CA y datos de soporte.",
    },
    config.NOTION_INDIVIDUAL_EVALUATIONS_PAGE_NAME: {
        "emoji": "📊",
        "color": "blue",
        "title": "Evaluaciones individuales",
        "body": "Espacio generado por el bot. Cada tabla recoge feedback recibido sobre una persona evaluada.",
    },
    config.NOTION_CA_TRACKING_PAGE_NAME: {
        "emoji": "💬",
        "color": "purple",
        "title": "Seguimiento Career Advisor",
        "body": "Opiniones y revisiones de CA generadas desde Slack. Pensado para consulta, no para mantenimiento diario.",
    },
}


def _bloque_texto(texto, negrita=False):
    return [{
        "type": "text",
        "text": {"content": texto},
        "annotations": {
            "bold": negrita,
            "italic": False,
            "strikethrough": False,
            "underline": False,
            "code": False,
            "color": "default",
        },
    }]


def _decorar_pagina_notion(page_id, nombre_pagina):
    estilo = _NOTION_PAGE_STYLE.get(nombre_pagina)
    if not estilo:
        return
    try:
        notion.pages.update(
            page_id=page_id,
            icon={"type": "emoji", "emoji": estilo["emoji"]},
        )
    except Exception:
        logging.exception("No se pudo actualizar el icono de la pagina %s", nombre_pagina)

    try:
        for bloque in _iter_blocks(page_id):
            if bloque.get("type") == "callout":
                texto = "".join(
                    item.get("plain_text", "")
                    for item in bloque.get("callout", {}).get("rich_text", [])
                )
                if "Evaluabot" in texto:
                    return
        notion.blocks.children.append(
            block_id=page_id,
            children=[
                {
                    "object": "block",
                    "type": "callout",
                    "callout": {
                        "rich_text": _bloque_texto(f"Evaluabot · {estilo['title']}", negrita=True)
                        + _bloque_texto(f"\n{estilo['body']}"),
                        "icon": {"type": "emoji", "emoji": estilo["emoji"]},
                        "color": f"{estilo['color']}_background",
                    },
                },
                {"object": "block", "type": "divider", "divider": {}},
            ],
        )
    except Exception:
        logging.exception("No se pudo decorar la pagina de Notion %s", nombre_pagina)


def aplicar_estetica_notion():
    parent_raiz = _parent_bbdd_referencia()
    for nombre_pagina in (
        config.NOTION_DATA_LISTS_PAGE_NAME,
        config.NOTION_INDIVIDUAL_EVALUATIONS_PAGE_NAME,
        config.NOTION_CA_TRACKING_PAGE_NAME,
    ):
        page_id = _page_or_database_link_by_name(parent_raiz["page_id"], nombre_pagina)
        if page_id:
            _decorar_pagina_notion(page_id, nombre_pagina)
            continue
        if nombre_pagina != config.NOTION_DATA_LISTS_PAGE_NAME:
            _parent_bbdd_en_pagina(nombre_pagina, crear=True)


def _parent_bbdd_en_pagina(nombre_pagina, crear=False):
    parent_raiz = _parent_bbdd_referencia()
    page_id = _page_or_database_link_by_name(parent_raiz["page_id"], nombre_pagina)
    if page_id:
        _decorar_pagina_notion(page_id, nombre_pagina)
        return {"type": "page_id", "page_id": page_id}
    if not crear:
        return parent_raiz

    pagina = notion.pages.create(
        parent=parent_raiz,
        properties={"title": {"title": [{"type": "text", "text": {"content": nombre_pagina}}]}},
    )
    _decorar_pagina_notion(pagina["id"], nombre_pagina)
    return {"type": "page_id", "page_id": pagina["id"]}


def _buscar_bbdd_en_pagina(nombre_pagina, titulo_bbdd):
    parent = _parent_bbdd_en_pagina(nombre_pagina, crear=False)
    if parent.get("type") != "page_id":
        return None

    objetivo = normalizar_nombre(titulo_bbdd)
    for bloque in _iter_blocks(parent["page_id"]):
        if bloque.get("type") == "child_database" and normalizar_nombre(_titulo_child_database(bloque)) == objetivo:
            try:
                return _data_source_id(notion.databases.retrieve(database_id=bloque["id"]))
            except Exception:
                logging.exception("No se pudo resolver la base %s desde %s", titulo_bbdd, nombre_pagina)
                return bloque["id"]

        if bloque.get("type") != "link_to_page":
            continue
        target_id = _target_link_to_page(bloque)
        if not target_id:
            continue
        try:
            db = notion.databases.retrieve(database_id=target_id)
            if normalizar_nombre(_extraer_titulo_bbdd(db)) == objetivo:
                return _data_source_id(db)
        except Exception:
            continue
    return None


def _buscar_bbdd_en_pagina_id(pagina_id: str, titulo_bbdd: str) -> str | None:
    """Como _buscar_bbdd_en_pagina pero recibe el page_id directamente."""
    objetivo = normalizar_nombre(titulo_bbdd)
    for bloque in _iter_blocks(pagina_id):
        if bloque.get("type") == "child_database" and normalizar_nombre(_titulo_child_database(bloque)) == objetivo:
            try:
                return _data_source_id(notion.databases.retrieve(database_id=bloque["id"]))
            except Exception:
                return bloque["id"]
        if bloque.get("type") != "link_to_page":
            continue
        target_id = _target_link_to_page(bloque)
        if not target_id:
            continue
        try:
            db = notion.databases.retrieve(database_id=target_id)
            if normalizar_nombre(_extraer_titulo_bbdd(db)) == objetivo:
                return _data_source_id(db)
        except Exception:
            continue
    return None


_NOMBRE_SUBPAGINA_PREGUNTAS = "Preguntas"
_cache_pagina_preguntas: dict = {"page_id": None}
_lock_pagina_preguntas = threading.Lock()


def _obtener_o_crear_pagina_preguntas_id() -> str | None:
    """Devuelve el page_id de la sub-página 'Preguntas' dentro de 'Listas de datos'."""
    with _lock_pagina_preguntas:
        cached = _cache_pagina_preguntas["page_id"]
    if cached:
        return cached
    listas_parent = _parent_bbdd_en_pagina(config.NOTION_DATA_LISTS_PAGE_NAME, crear=True)
    if listas_parent.get("type") != "page_id":
        return None
    listas_page_id = listas_parent["page_id"]
    page_id = _page_or_database_link_by_name(listas_page_id, _NOMBRE_SUBPAGINA_PREGUNTAS)
    if not page_id:
        try:
            nueva = notion.pages.create(
                parent={"type": "page_id", "page_id": listas_page_id},
                properties={"title": {"title": [{"type": "text", "text": {"content": _NOMBRE_SUBPAGINA_PREGUNTAS}}]}},
            )
            page_id = nueva["id"]
            logging.info("Sub-página '%s' creada en '%s'", _NOMBRE_SUBPAGINA_PREGUNTAS, config.NOTION_DATA_LISTS_PAGE_NAME)
        except Exception:
            logging.exception("Error creando sub-página '%s'", _NOMBRE_SUBPAGINA_PREGUNTAS)
            return None
    with _lock_pagina_preguntas:
        _cache_pagina_preguntas["page_id"] = page_id
    return page_id


def obtener_parent_bbdd_evaluados():
    try:
        return _parent_bbdd_referencia()
    except RuntimeError as error:
        logging.warning(error)
        return None


# ---------------------------------------------------------------------------
# BD Preguntas — configurable desde Notion
# ---------------------------------------------------------------------------

NOTION_QUESTIONS_DATABASE_NAME = "Preguntas"
_preguntas_cache: dict = {}
_preguntas_cache_time: dict = {}
_PREGUNTAS_CACHE_TTL = 300
_lock_preguntas = threading.Lock()


def _propiedades_bbdd_preguntas():
    return {
        "Texto": {"title": {}},
        "Tipo": {"select": {"options": [
            {"name": "Top-Bottom"},
            {"name": "Bottom-Top"},
            {"name": "Same Level"},
        ]}},
        "Clave": {"select": {"options": [
            {"name": "q1"},
            {"name": "q2"},
        ]}},
    }


_NOMBRE_BBDD_PREGUNTAS_NEGOCIO = "Preguntas Negocio"


def _obtener_o_crear_bbdd_preguntas():
    # 1. Buscar "Preguntas Negocio" dentro de la sub-página "Preguntas"
    preguntas_page_id = _obtener_o_crear_pagina_preguntas_id()
    if preguntas_page_id:
        bbdd_id = _buscar_bbdd_en_pagina_id(preguntas_page_id, _NOMBRE_BBDD_PREGUNTAS_NEGOCIO)
        if bbdd_id:
            _poblar_bbdd_preguntas(bbdd_id)
            return bbdd_id
    # 2. Fallback: buscar "Preguntas" en la ubicación antigua
    bbdd_id = _buscar_bbdd_en_pagina(config.NOTION_DATA_LISTS_PAGE_NAME, NOTION_QUESTIONS_DATABASE_NAME)
    if bbdd_id:
        _poblar_bbdd_preguntas(bbdd_id)
        return bbdd_id
    # 3. Crear "Preguntas Negocio" dentro de la sub-página
    parent_page_id = preguntas_page_id
    if not parent_page_id:
        parent = _parent_bbdd_en_pagina(config.NOTION_DATA_LISTS_PAGE_NAME, crear=True)
        if parent.get("type") != "page_id":
            return None
        parent_page_id = parent["page_id"]
    titulo = _NOMBRE_BBDD_PREGUNTAS_NEGOCIO
    props = _propiedades_bbdd_preguntas()
    try:
        if _usa_data_sources():
            nueva = notion.databases.create(
                parent={"type": "page_id", "page_id": parent_page_id},
                title=[{"type": "text", "text": {"content": titulo}}],
                initial_data_source={"title": [{"type": "text", "text": {"content": titulo}}], "properties": props},
            )
            nueva = notion.databases.retrieve(database_id=nueva["id"])
        else:
            nueva = notion.databases.create(
                parent={"type": "page_id", "page_id": parent_page_id},
                title=[{"type": "text", "text": {"content": titulo}}],
                properties=props,
            )
        bbdd_id = _data_source_id(nueva)
        logging.info("BD '%s' creada en Notion: %s", titulo, bbdd_id)
        _poblar_bbdd_preguntas(bbdd_id)
        return bbdd_id
    except Exception:
        logging.exception("Error creando BD de Preguntas en Notion")
        return None


_Q4_BOTTOM_TOP = "¿Cómo valorarías del 1 al 5 la contribución del Project Leader al buen avance del proyecto?"
_Q4_TOP_BOTTOM = "¿Cómo valorarías del 1 al 5 la contribución de {nombre} al buen avance del proyecto?"
_Q4_SAME_LEVEL = "¿Cómo valorarías del 1 al 5 la contribución de {nombre} al buen avance del proyecto?"
_Q5_TEXTO = "Indica un ejemplo concreto que justifique tu valoración"

_PREGUNTAS_INICIALES = [
    ("Top-Bottom", "q1", _Q4_TOP_BOTTOM),
    ("Top-Bottom", "q2", _Q5_TEXTO),
    ("Bottom-Top", "q1", _Q4_BOTTOM_TOP),
    ("Bottom-Top", "q2", _Q5_TEXTO),
    ("Same Level", "q1", _Q4_SAME_LEVEL),
    ("Same Level", "q2", _Q5_TEXTO),
]

_preguntas_bbdd_pobladas: set = set()


def _poblar_bbdd_preguntas(bbdd_id):
    """Añade las entradas que falten en la BD de preguntas. Idempotente."""
    if bbdd_id in _preguntas_bbdd_pobladas:
        return
    existentes: dict = {}
    try:
        resp = _query_bbdd(bbdd_id)
        for pag in resp.get("results", []):
            props = pag.get("properties", {})
            tipo = ((props.get("Tipo") or {}).get("select") or {}).get("name", "")
            clave = ((props.get("Clave") or {}).get("select") or {}).get("name", "")
            texto_actual = "".join(t.get("plain_text", "") for t in (props.get("Texto") or {}).get("title", []))
            if tipo and clave:
                existentes[(tipo, clave)] = {"page_id": pag["id"], "texto": texto_actual}
    except Exception:
        logging.exception("Error leyendo entradas existentes en BD Preguntas")
    for tipo, clave, texto in _PREGUNTAS_INICIALES:
        entrada = existentes.get((tipo, clave))
        if entrada is None:
            try:
                _crear_pagina_en_bbdd(bbdd_id, {
                    "Texto": {"title": [{"text": {"content": texto}}]},
                    "Tipo": {"select": {"name": tipo}},
                    "Clave": {"select": {"name": clave}},
                })
            except Exception:
                logging.exception("Error creando fila '%s'/'%s' en BD Preguntas", tipo, clave)
        elif clave == "q1" and entrada["texto"].startswith("Este mes"):
            try:
                notion.pages.update(
                    page_id=entrada["page_id"],
                    properties={"Texto": {"title": [{"type": "text", "text": {"content": texto}}]}},
                )
            except Exception:
                logging.exception("Error actualizando q1 en BD Preguntas '%s'/'%s'", tipo, clave)
    _preguntas_bbdd_pobladas.add(bbdd_id)


def obtener_preguntas_desde_notion(tipo: str) -> dict:
    """Devuelve {clave: texto} para el tipo dado (Top-Bottom / Bottom-Top / Same Level). Caché 5 min."""
    ahora = time.time()
    with _lock_preguntas:
        if tipo in _preguntas_cache and (ahora - _preguntas_cache_time.get(tipo, 0)) < _PREGUNTAS_CACHE_TTL:
            return _preguntas_cache[tipo]
    try:
        bbdd_id = _obtener_o_crear_bbdd_preguntas()
        if not bbdd_id:
            return {}
        resp = _query_bbdd(bbdd_id, filter={"property": "Tipo", "select": {"equals": tipo}})
        preguntas = {}
        for pagina in resp.get("results", []):
            props = pagina.get("properties", {})
            clave = ((props.get("Clave") or {}).get("select") or {}).get("name", "")
            titulo = "".join(t.get("plain_text", "") for t in (props.get("Texto") or {}).get("title", []))
            if clave and titulo:
                preguntas[clave] = titulo
        with _lock_preguntas:
            _preguntas_cache[tipo] = preguntas
            _preguntas_cache_time[tipo] = ahora
        return preguntas
    except Exception:
        logging.exception("Error obteniendo preguntas de Notion para tipo '%s'", tipo)
        return {}


# ---------------------------------------------------------------------------
# BD Preguntas MiddleOffice — sin jerarquía, configurable desde Notion
# ---------------------------------------------------------------------------

_NOMBRE_BBDD_PREGUNTAS_MO = "Preguntas MiddleOffice"
_lock_preguntas_mo = threading.Lock()
_cache_preguntas_mo_db: dict = {"db_id": None}
_cache_preguntas_mo_data: dict = {}
_PREGUNTAS_MO_TTL = 300

_PROPS_PREGUNTAS_MO = {
    "Clave": {"title": {}},
    "Texto": {"rich_text": {}},
}

_PREGUNTAS_MO_DEFAULT = [
    ("mo_contribucion", "Este mes, ¿cómo valorarías la contribución del evaluado al buen funcionamiento interno de Igeneris? (1-5)"),
    ("mo_justificacion", "Justifica con un ejemplo concreto la puntuación anterior."),
]
_CLAVES_MO_ORDEN = [c for c, _ in _PREGUNTAS_MO_DEFAULT]

_preguntas_mo_bbdd_pobladas: set = set()


def _obtener_o_crear_bbdd_preguntas_mo() -> str | None:
    with _lock_preguntas_mo:
        db_id = _cache_preguntas_mo_db["db_id"]
    if db_id:
        return db_id
    # Buscar en sub-página "Preguntas", luego fallback a ubicación antigua
    preguntas_page_id = _obtener_o_crear_pagina_preguntas_id()
    if preguntas_page_id:
        db_id = _buscar_bbdd_en_pagina_id(preguntas_page_id, _NOMBRE_BBDD_PREGUNTAS_MO)
    if not db_id:
        db_id = _buscar_bbdd_en_pagina(config.NOTION_DATA_LISTS_PAGE_NAME, _NOMBRE_BBDD_PREGUNTAS_MO)
    if db_id:
        _poblar_bbdd_preguntas_mo(db_id)
    if not db_id:
        parent_page_id = preguntas_page_id
        if not parent_page_id:
            parent = _parent_bbdd_en_pagina(config.NOTION_DATA_LISTS_PAGE_NAME, crear=True)
            if parent.get("type") != "page_id":
                return None
            parent_page_id = parent["page_id"]
        try:
            props = _PROPS_PREGUNTAS_MO
            if _usa_data_sources():
                nueva = notion.databases.create(
                    parent={"type": "page_id", "page_id": parent_page_id},
                    title=[{"type": "text", "text": {"content": _NOMBRE_BBDD_PREGUNTAS_MO}}],
                    initial_data_source={
                        "title": [{"type": "text", "text": {"content": _NOMBRE_BBDD_PREGUNTAS_MO}}],
                        "properties": props,
                    },
                )
                nueva = notion.databases.retrieve(database_id=nueva["id"])
            else:
                nueva = notion.databases.create(
                    parent={"type": "page_id", "page_id": parent_page_id},
                    title=[{"type": "text", "text": {"content": _NOMBRE_BBDD_PREGUNTAS_MO}}],
                    properties=props,
                )
            db_id = _data_source_id(nueva)
            logging.info("BD '%s' creada en Notion", _NOMBRE_BBDD_PREGUNTAS_MO)
            _poblar_bbdd_preguntas_mo(db_id)
        except Exception:
            logging.exception("Error creando BD '%s'", _NOMBRE_BBDD_PREGUNTAS_MO)
            return None
    with _lock_preguntas_mo:
        _cache_preguntas_mo_db["db_id"] = db_id
    return db_id


def _poblar_bbdd_preguntas_mo(db_id: str) -> None:
    if db_id in _preguntas_mo_bbdd_pobladas:
        return
    try:
        existentes: set[str] = set()
        cursor = None
        while True:
            kwargs: dict = {"page_size": 100}
            if cursor:
                kwargs["start_cursor"] = cursor
            resp = _query_bbdd(db_id, **kwargs)
            for page in resp.get("results", []):
                props = page.get("properties", {})
                clave_val = "".join(
                    p.get("plain_text", "") for p in props.get("Clave", {}).get("title", [])
                ).strip()
                if clave_val:
                    existentes.add(clave_val)
            if not resp.get("has_more"):
                break
            cursor = resp.get("next_cursor")
    except Exception:
        logging.exception("Error leyendo entradas existentes en BD MO '%s'", db_id)
        existentes = set()
    for clave, texto in _PREGUNTAS_MO_DEFAULT:
        if clave in existentes:
            continue
        try:
            _crear_pagina_en_bbdd(db_id, {
                "Clave": {"title": [{"type": "text", "text": {"content": clave}}]},
                "Texto": {"rich_text": [{"type": "text", "text": {"content": texto}}]},
            })
        except Exception:
            logging.exception("Error poblando pregunta MO '%s'", clave)
    _preguntas_mo_bbdd_pobladas.add(db_id)


def obtener_preguntas_mo() -> list[dict]:
    """Devuelve [{clave, texto}] para MiddleOffice (cacheado 5 min)."""
    ahora = time.time()
    with _lock_preguntas_mo:
        cached = _cache_preguntas_mo_data.get("data")
        ts = _cache_preguntas_mo_data.get("ts", 0.0)
    if cached is not None and (ahora - ts) < _PREGUNTAS_MO_TTL:
        return cached
    db_id = _obtener_o_crear_bbdd_preguntas_mo()
    if not db_id:
        return [{"clave": c, "texto": t} for c, t in _PREGUNTAS_MO_DEFAULT]
    try:
        resultado = []
        cursor = None
        while True:
            kwargs: dict = {"page_size": 100}
            if cursor:
                kwargs["start_cursor"] = cursor
            resp = _query_bbdd(db_id, **kwargs)
            for fila in resp.get("results", []):
                props = fila.get("properties", {})
                clave = "".join(p.get("plain_text", "") for p in props.get("Clave", {}).get("title", [])).strip()
                texto = "".join(p.get("plain_text", "") for p in props.get("Texto", {}).get("rich_text", [])).strip()
                if clave and texto:
                    resultado.append({"clave": clave, "texto": texto})
            if not resp.get("has_more"):
                break
            cursor = resp.get("next_cursor")
        if not resultado:
            resultado = [{"clave": c, "texto": t} for c, t in _PREGUNTAS_MO_DEFAULT]
        # Filtrar solo claves conocidas y ordenar según _PREGUNTAS_MO_DEFAULT
        resultado = [q for q in resultado if q["clave"] in set(_CLAVES_MO_ORDEN)]
        resultado.sort(key=lambda q: _CLAVES_MO_ORDEN.index(q["clave"]))
        if not resultado:
            resultado = [{"clave": c, "texto": t} for c, t in _PREGUNTAS_MO_DEFAULT]
        with _lock_preguntas_mo:
            _cache_preguntas_mo_data["data"] = resultado
            _cache_preguntas_mo_data["ts"] = time.time()
        return resultado
    except Exception:
        logging.exception("Error leyendo preguntas MiddleOffice desde Notion")
        return [{"clave": c, "texto": t} for c, t in _PREGUNTAS_MO_DEFAULT]


# ---------------------------------------------------------------------------
# BD Preguntas Palantir — con jerarquía, configurable desde Notion
# ---------------------------------------------------------------------------

_NOMBRE_BBDD_PREGUNTAS_PALANTIR = "Preguntas Palantir"
_lock_preguntas_palantir = threading.Lock()
_cache_preguntas_palantir_db: dict = {"db_id": None}
_cache_preguntas_palantir_data: dict = {}
_cache_preguntas_palantir_ts: dict = {}
_PREGUNTAS_PALANTIR_TTL = 300

_PROPS_PREGUNTAS_PALANTIR = {
    "Clave": {"title": {}},
    "Tipo": {"select": {"options": [
        {"name": "Top-Bottom"},
        {"name": "Bottom-Top"},
        {"name": "Same Level"},
    ]}},
    "Texto": {"rich_text": {}},
}

_PREGUNTAS_PALANTIR_DEFAULT = [
    ("Top-Bottom", "q1", _Q4_TOP_BOTTOM),
    ("Top-Bottom", "q2", _Q5_TEXTO),
    ("Bottom-Top", "q1", _Q4_BOTTOM_TOP),
    ("Bottom-Top", "q2", _Q5_TEXTO),
    ("Same Level", "q1", _Q4_SAME_LEVEL),
    ("Same Level", "q2", _Q5_TEXTO),
]

_preguntas_palantir_bbdd_pobladas: set = set()


def _obtener_o_crear_bbdd_preguntas_palantir() -> str | None:
    with _lock_preguntas_palantir:
        db_id = _cache_preguntas_palantir_db["db_id"]
    if db_id:
        return db_id
    # Buscar en sub-página "Preguntas", luego fallback a ubicación antigua
    preguntas_page_id = _obtener_o_crear_pagina_preguntas_id()
    if preguntas_page_id:
        db_id = _buscar_bbdd_en_pagina_id(preguntas_page_id, _NOMBRE_BBDD_PREGUNTAS_PALANTIR)
    if not db_id:
        db_id = _buscar_bbdd_en_pagina(config.NOTION_DATA_LISTS_PAGE_NAME, _NOMBRE_BBDD_PREGUNTAS_PALANTIR)
    if db_id:
        _poblar_bbdd_preguntas_palantir(db_id)
        with _lock_preguntas_palantir:
            _cache_preguntas_palantir_db["db_id"] = db_id
        return db_id
    if not db_id:
        parent_page_id = preguntas_page_id
        if not parent_page_id:
            parent = _parent_bbdd_en_pagina(config.NOTION_DATA_LISTS_PAGE_NAME, crear=True)
            if parent.get("type") != "page_id":
                return None
            parent_page_id = parent["page_id"]
        try:
            props = _PROPS_PREGUNTAS_PALANTIR
            if _usa_data_sources():
                nueva = notion.databases.create(
                    parent={"type": "page_id", "page_id": parent_page_id},
                    title=[{"type": "text", "text": {"content": _NOMBRE_BBDD_PREGUNTAS_PALANTIR}}],
                    initial_data_source={
                        "title": [{"type": "text", "text": {"content": _NOMBRE_BBDD_PREGUNTAS_PALANTIR}}],
                        "properties": props,
                    },
                )
                nueva = notion.databases.retrieve(database_id=nueva["id"])
            else:
                nueva = notion.databases.create(
                    parent={"type": "page_id", "page_id": parent_page_id},
                    title=[{"type": "text", "text": {"content": _NOMBRE_BBDD_PREGUNTAS_PALANTIR}}],
                    properties=props,
                )
            db_id = _data_source_id(nueva)
            logging.info("BD '%s' creada en Notion", _NOMBRE_BBDD_PREGUNTAS_PALANTIR)
            _poblar_bbdd_preguntas_palantir(db_id)
        except Exception:
            logging.exception("Error creando BD '%s'", _NOMBRE_BBDD_PREGUNTAS_PALANTIR)
            return None
    with _lock_preguntas_palantir:
        _cache_preguntas_palantir_db["db_id"] = db_id
    return db_id


def _poblar_bbdd_preguntas_palantir(db_id: str) -> None:
    """Añade las entradas que falten en la BD de Palantir. Idempotente."""
    if db_id in _preguntas_palantir_bbdd_pobladas:
        return
    existentes: dict = {}
    try:
        resp = _query_bbdd(db_id)
        for pag in resp.get("results", []):
            props = pag.get("properties", {})
            tipo = ((props.get("Tipo") or {}).get("select") or {}).get("name", "")
            clave = "".join(p.get("plain_text", "") for p in props.get("Clave", {}).get("title", [])).strip()
            texto_actual = "".join(p.get("plain_text", "") for p in props.get("Texto", {}).get("rich_text", [])).strip()
            if tipo and clave:
                existentes[(tipo, clave)] = {"page_id": pag["id"], "texto": texto_actual}
    except Exception:
        logging.exception("Error leyendo entradas existentes en BD Palantir")
    for tipo, clave, texto in _PREGUNTAS_PALANTIR_DEFAULT:
        entrada = existentes.get((tipo, clave))
        if entrada is None:
            try:
                _crear_pagina_en_bbdd(db_id, {
                    "Clave": {"title": [{"type": "text", "text": {"content": clave}}]},
                    "Tipo": {"select": {"name": tipo}},
                    "Texto": {"rich_text": [{"type": "text", "text": {"content": texto}}]},
                })
            except Exception:
                logging.exception("Error poblando pregunta Palantir '%s'/'%s'", tipo, clave)
        elif clave == "q1" and entrada["texto"].startswith("Este mes"):
            try:
                notion.pages.update(
                    page_id=entrada["page_id"],
                    properties={"Texto": {"rich_text": [{"type": "text", "text": {"content": texto}}]}},
                )
            except Exception:
                logging.exception("Error actualizando q1 en BD Palantir '%s'/'%s'", tipo, clave)
    _preguntas_palantir_bbdd_pobladas.add(db_id)


def obtener_preguntas_palantir(tipo: str) -> list[dict]:
    """Devuelve [{clave, texto}] para el tipo de jerarquía dado en Palantir (cacheado 5 min)."""
    ahora = time.time()
    with _lock_preguntas_palantir:
        cached = _cache_preguntas_palantir_data.get(tipo)
        ts = _cache_preguntas_palantir_ts.get(tipo, 0.0)
    if cached is not None and (ahora - ts) < _PREGUNTAS_PALANTIR_TTL:
        return cached
    db_id = _obtener_o_crear_bbdd_preguntas_palantir()
    if not db_id:
        return [{"clave": c, "texto": t} for tp, c, t in _PREGUNTAS_PALANTIR_DEFAULT if tp == tipo]
    try:
        resultado = []
        cursor = None
        while True:
            kwargs: dict = {"page_size": 100}
            if cursor:
                kwargs["start_cursor"] = cursor
            resp = _query_bbdd(db_id, **kwargs)
            for fila in resp.get("results", []):
                props = fila.get("properties", {})
                tipo_fila = ((props.get("Tipo") or {}).get("select") or {}).get("name", "")
                if normalizar_nombre(tipo_fila) != normalizar_nombre(tipo):
                    continue
                clave = "".join(p.get("plain_text", "") for p in props.get("Clave", {}).get("title", [])).strip()
                texto = "".join(p.get("plain_text", "") for p in props.get("Texto", {}).get("rich_text", [])).strip()
                if clave and texto:
                    resultado.append({"clave": clave, "texto": texto})
            if not resp.get("has_more"):
                break
            cursor = resp.get("next_cursor")
        if not resultado:
            resultado = [{"clave": c, "texto": t} for tp, c, t in _PREGUNTAS_PALANTIR_DEFAULT if tp == tipo]
        resultado.sort(key=lambda x: x["clave"])
        with _lock_preguntas_palantir:
            _cache_preguntas_palantir_data[tipo] = resultado
            _cache_preguntas_palantir_ts[tipo] = time.time()
        return resultado
    except Exception:
        logging.exception("Error leyendo preguntas Palantir tipo '%s'", tipo)
        return sorted(
            [{"clave": c, "texto": t} for tp, c, t in _PREGUNTAS_PALANTIR_DEFAULT if tp == tipo],
            key=lambda x: x["clave"],
        )


def obtener_o_crear_bbdd_evaluado(nombre_evaluado):
    nombre_limpio = " ".join(nombre_evaluado.split()).strip() or "Sin nombre"
    titulo = _titulo_bbdd(nombre_limpio)
    with lock:
        cacheada = bbdd_por_evaluado.get(titulo)
    if cacheada:
        return cacheada

    parent = _parent_bbdd_referencia()
    parent_evaluaciones = _parent_bbdd_en_pagina(config.NOTION_INDIVIDUAL_EVALUATIONS_PAGE_NAME, crear=True)
    resultado = notion.search(query=titulo, filter={"value": _tipo_objeto_busqueda_bbdd(), "property": "object"}, page_size=100)
    for bbdd in resultado.get("results", []):
        if _extraer_titulo_bbdd(bbdd) == titulo and (
            _coincide_parent_bbdd(bbdd, parent) or _coincide_parent_bbdd(bbdd, parent_evaluaciones)
        ):
            database_id = _data_source_id(bbdd)
            asegurar_propiedades_bbdd(database_id)
            with lock:
                bbdd_por_evaluado[titulo] = database_id
            return database_id

    if _usa_data_sources():
        nueva = notion.databases.create(
            parent=parent_evaluaciones,
            title=[{"type": "text", "text": {"content": titulo}}],
            initial_data_source={"title": [{"type": "text", "text": {"content": titulo}}], "properties": _propiedades_bbdd_evaluaciones()},
        )
        nueva = notion.databases.retrieve(database_id=nueva["id"])
    else:
        nueva = notion.databases.create(parent=parent_evaluaciones, title=[{"type": "text", "text": {"content": titulo}}], properties=_propiedades_bbdd_evaluaciones())

    database_id = _data_source_id(nueva)
    asegurar_propiedades_bbdd(database_id)
    with lock:
        bbdd_por_evaluado[titulo] = database_id
    logging.info(f"Base de datos creada en Notion: {titulo}")
    return database_id


def guardar_en_notion(nombre, respuestas, relacion="igual", area="Negocio"):
    nombre_evaluado = respuestas.get("evaluado", "").strip()
    proyecto = respuestas.get("proyecto", "").strip()
    try:
        database_id = obtener_o_crear_bbdd_evaluado(nombre_evaluado)
        asegurar_propiedades_bbdd(database_id)
        fecha = datetime.now(timezone.utc)
        _skip = {"evaluado", "proyecto", "satisfaccion"}
        _extras = [v for k, v in respuestas.items() if k not in _skip and v]
        valoracion = _extras[0] if len(_extras) > 0 else ""
        justificacion = _extras[1] if len(_extras) > 1 else ""
        suf_col = {"superior": "de superiores", "inferior": "de inferiores"}.get(relacion, "de iguales")
        _crear_pagina_en_bbdd(
            database_id,
            {
                "Name": {"title": [{"text": {"content": f"Evaluacion {fecha.strftime('%Y-%m-%d %H:%M')}"}}]},
                "Evaluador": {"rich_text": [{"text": {"content": nombre}}]},
                "Proyecto": {"rich_text": [{"text": {"content": proyecto}}]},
                "Fecha": {"date": {"start": fecha.isoformat()}},
                "Area": {"select": {"name": area}},
                f"Valoración {suf_col}": {"rich_text": [{"text": {"content": valoracion}}]},
                f"Justificación {suf_col}": {"rich_text": [{"text": {"content": justificacion}}]},
            },
        )
        return True
    except Exception:
        logging.exception("Error guardando en Notion")
        return False


def _texto_rich_text(propiedades, nombre_propiedad):
    items = propiedades.get(nombre_propiedad, {}).get("rich_text", [])
    return items[0]["text"]["content"] if items else ""


def _texto_title(propiedades, nombre_propiedad):
    items = propiedades.get(nombre_propiedad, {}).get("title", [])
    return items[0]["text"]["content"] if items else ""


def _texto_propiedad(propiedades, nombre_propiedad):
    propiedad = propiedades.get(nombre_propiedad, {})
    tipo = propiedad.get("type")
    if tipo == "title":
        return " ".join(item.get("plain_text", "") for item in propiedad.get("title", [])).strip()
    if tipo == "rich_text":
        return " ".join(item.get("plain_text", "") for item in propiedad.get("rich_text", [])).strip()
    if tipo == "select":
        return (propiedad.get("select") or {}).get("name", "").strip()
    if tipo == "multi_select":
        return ", ".join(item.get("name", "") for item in propiedad.get("multi_select", [])).strip()
    if tipo == "people":
        nombres = []
        for persona in propiedad.get("people", []):
            nombre = persona.get("name", "") or (persona.get("person") or {}).get("email", "") or persona.get("id", "")
            if nombre:
                nombres.append(nombre)
        return ", ".join(nombres).strip()
    if tipo == "email":
        return (propiedad.get("email") or "").strip()
    if tipo == "formula":
        formula = propiedad.get("formula") or {}
        if formula.get("type") == "string":
            return (formula.get("string") or "").strip()
    return ""


def _texto_email_propiedad(propiedades, nombre_propiedad):
    propiedad = propiedades.get(nombre_propiedad, {})
    tipo = propiedad.get("type")
    if tipo == "email":
        return (propiedad.get("email") or "").strip()
    if tipo == "people":
        emails = []
        for persona in propiedad.get("people", []):
            email = (persona.get("person") or {}).get("email", "")
            if email:
                emails.append(email)
        return ", ".join(emails).strip()
    return _texto_propiedad(propiedades, nombre_propiedad)


def _url_foto_propiedad(props, nombre_propiedad):
    prop = props.get(nombre_propiedad, {})
    tipo = prop.get("type")
    if tipo == "files":
        for f in prop.get("files", []):
            url = f.get("file", {}).get("url") or f.get("external", {}).get("url") or ""
            if url:
                return url
    if tipo == "url":
        return prop.get("url") or ""
    return _texto_propiedad(props, nombre_propiedad)


_lock_empleados = threading.Lock()
_empleados_cache_data: list = []
_empleados_cache_ts: float = 0.0
_EMPLEADOS_CACHE_TTL = 300  # 5 minutos


def _obtener_registros_empleados() -> list[dict]:
    """Lee empleados con su nombre canonico y aliases utiles para busqueda. Cachea 5 min."""
    global _empleados_cache_data, _empleados_cache_ts
    import time as _time
    ahora = _time.time()
    with _lock_empleados:
        if _empleados_cache_data and (ahora - _empleados_cache_ts) < _EMPLEADOS_CACHE_TTL:
            return list(_empleados_cache_data)
    try:
        db_id, resultado = _retrieve_bbdd(config.NOTION_EMPLOYEES_DATABASE_ID)
        propiedades = resultado.get("properties", {})
        candidatos = ("Nombre", "Empleado", "Persona", "Miembro", "Persona evaluada", "Name", "Employee", "Employee Name")
        nombre_props = [candidato for candidato in candidatos if candidato in propiedades]
        if not nombre_props:
            logging.warning(
                "No se encontro una propiedad de nombre en la lista de empleados. Columnas disponibles: %s",
                ", ".join(propiedades.keys()),
            )
            return []

        registros = []
        cursor = None
        while True:
            kwargs = {"page_size": 100}
            if cursor:
                kwargs["start_cursor"] = cursor
            resp = _query_bbdd(db_id, **kwargs)
            for pagina in resp.get("results", []):
                props = pagina.get("properties", {})
                nombre = ""
                for nombre_prop in nombre_props:
                    valor = _texto_propiedad(props, nombre_prop)
                    if valor:
                        nombre = valor.strip()
                        break
                if not nombre:
                    continue

                aliases = []
                email = ""
                for email_prop in ("Email", "Mail", "Correo", "Correo electronico", "Correo electrónico", "E-mail"):
                    if email_prop in props:
                        email = _texto_email_propiedad(props, email_prop)
                        if email:
                            break

                for alias_prop in ("Nombre_Slack", "Slack", "Usuario Slack", "Nombre Slack", "Alias", "Email"):
                    if alias_prop in props:
                        valor_alias = _texto_propiedad(props, alias_prop)
                        if valor_alias:
                            aliases.append(valor_alias.strip())

                cargo = ""
                for cargo_prop in ("Cargo", "Puesto", "Rol", "Role"):
                    if cargo_prop in props:
                        cargo = _texto_propiedad(props, cargo_prop)
                        if cargo:
                            break

                id_usuario = ""
                for id_prop in ("ID_usuario", "ID usuario", "Slack ID"):
                    if id_prop in props:
                        id_usuario = _texto_propiedad(props, id_prop)
                        if id_usuario:
                            break

                foto = ""
                for foto_prop in ("Foto", "Photo", "Avatar"):
                    if foto_prop in props:
                        foto = _url_foto_propiedad(props, foto_prop)
                        if foto:
                            break

                baja = bool((props.get("Baja") or {}).get("checkbox", False))
                registros.append({"nombre": nombre, "email": email.strip(), "aliases": aliases, "cargo": cargo, "id_usuario": id_usuario, "foto": foto, "baja": baja})
            if not resp.get("has_more"):
                break
            cursor = resp.get("next_cursor")
        logging.info(
            "Lista de empleados leida desde Notion '%s' (%s): %s nombres. Columnas usadas: %s",
            _extraer_titulo_bbdd(resultado) or "sin titulo",
            db_id,
            len(registros),
            ", ".join(nombre_props),
        )
        with _lock_empleados:
            _empleados_cache_data = registros
            _empleados_cache_ts = _time.time()
        return registros
    except Exception:
        logging.exception("Error leyendo la lista de empleados desde Notion")
        with _lock_empleados:
            if _empleados_cache_data:
                logging.warning("Devolviendo cache de empleados por error de Notion")
                return list(_empleados_cache_data)
        return []


def obtener_lista_empleados() -> list[str]:
    """Lee los nombres canonicos de empleados desde Notion."""
    return [registro["nombre"] for registro in _obtener_registros_empleados()]


def obtener_registros_empleados() -> list[dict]:
    """Lee empleados con nombre, email y aliases desde Notion."""
    return _obtener_registros_empleados()


def obtener_perfil_empleado(nombre: str) -> dict:
    """Devuelve cargo y foto del empleado que coincide con el nombre dado."""
    nombre_norm = normalizar_nombre(nombre)
    for r in _obtener_registros_empleados():
        if normalizar_nombre(r["nombre"]) == nombre_norm:
            return {"cargo": r.get("cargo", ""), "foto": r.get("foto", "")}
    return {"cargo": "", "foto": ""}


# ---------------------------------------------------------------------------
# Criterios de evaluación por grupo (Negocio / Palantir / MiddleOffice)
# ---------------------------------------------------------------------------

_NOMBRE_PAGINA_CRITERIOS = "Criterios de evaluaciones"
_cache_criterios: dict[str, tuple[dict, float]] = {}
_lock_criterios = threading.Lock()
_CRITERIOS_CACHE_TTL = 300  # 5 minutos

# IDs de las BDs de criterios (se descubren dinámicamente la primera vez)
_criterios_db_ids: dict[str, str] = {}
_criterios_db_ids_ts: float = 0.0


def _obtener_db_criterios(grupo: str) -> str | None:
    """Devuelve el data_source_id de la BD de criterios para el grupo dado."""
    global _criterios_db_ids, _criterios_db_ids_ts
    ahora = time.time()
    with _lock_criterios:
        if _criterios_db_ids and (ahora - _criterios_db_ids_ts) < _CRITERIOS_CACHE_TTL:
            return _criterios_db_ids.get(grupo)

    try:
        criterios_page_id = _buscar_objeto_notion_por_nombre(_NOMBRE_PAGINA_CRITERIOS)
        if not criterios_page_id:
            return None
        ids: dict[str, str] = {}
        for bloque in _iter_blocks(criterios_page_id):
            if bloque.get("type") == "child_database":
                titulo = _titulo_child_database(bloque)
                try:
                    db = notion.databases.retrieve(database_id=bloque["id"])
                    db_id = _data_source_id(db)
                except Exception:
                    db_id = bloque["id"]
                ids.setdefault(titulo, db_id)  # usa la primera (original), ignora duplicados
        with _lock_criterios:
            _criterios_db_ids = ids
            _criterios_db_ids_ts = time.time()
        return ids.get(grupo)
    except Exception:
        logging.exception("Error buscando BD de criterios para grupo '%s'", grupo)
        return None


def obtener_criterios_evaluacion(grupo: str) -> dict:
    """
    Devuelve {dimension_label: {nivel: [textos]}} para el grupo indicado.
    Lee de 'Criterios de evaluaciones/{grupo}' en Notion. Cachea 5 min.
    """
    ahora = time.time()
    with _lock_criterios:
        cached = _cache_criterios.get(grupo)
        if cached and (ahora - cached[1]) < _CRITERIOS_CACHE_TTL:
            return cached[0]

    db_id = _obtener_db_criterios(grupo)
    if not db_id:
        return {}

    resultado: dict = {}
    try:
        cursor = None
        while True:
            kwargs: dict = {"page_size": 100}
            if cursor:
                kwargs["start_cursor"] = cursor
            resp = _query_bbdd(db_id, **kwargs)
            for row in resp.get("results", []):
                props = row.get("properties", {})
                texto = "".join(t.get("plain_text", "") for t in (props.get("Criterio") or {}).get("title", []))
                dimension = (props.get("Dimension") or {}).get("select", {}) or {}
                dimension = dimension.get("name", "")
                nivel = (props.get("Nivel") or {}).get("select", {}) or {}
                nivel = nivel.get("name", "")
                orden = (props.get("Orden") or {}).get("number") or 999
                if texto and dimension and nivel:
                    resultado.setdefault(dimension, {}).setdefault(nivel, [])
                    resultado[dimension][nivel].append((orden, texto))
            if not resp.get("has_more"):
                break
            cursor = resp.get("next_cursor")
        # Ordenar por 'orden' y dejar solo textos
        for dim in resultado:
            for niv in resultado[dim]:
                resultado[dim][niv] = [t for _, t in sorted(resultado[dim][niv])]
    except Exception:
        logging.exception("Error leyendo criterios para grupo '%s'", grupo)
        return {}

    with _lock_criterios:
        _cache_criterios[grupo] = (resultado, time.time())
    return resultado


def _tokens_nombre(nombre):
    return {token for token in _normalizar_para_match(nombre).split() if len(token) > 1}


def _normalizar_para_match(valor):
    texto = normalizar_nombre(valor)
    texto = "".join(
        char for char in unicodedata.normalize("NFD", texto)
        if unicodedata.category(char) != "Mn"
    )
    return re.sub(r"[^a-z0-9]+", " ", texto).strip()


def _compactar_match(valor):
    return _normalizar_para_match(valor).replace(" ", "")


def _lcs_len(a, b):
    if not a or not b:
        return 0
    prev = [0] * (len(b) + 1)
    for char_a in a:
        curr = [0]
        for idx_b, char_b in enumerate(b, 1):
            curr.append(prev[idx_b - 1] + 1 if char_a == char_b else max(prev[idx_b], curr[-1]))
        prev = curr
    return prev[-1]


def _score_orden_letras(buscado, candidato):
    buscado_compacto = _compactar_match(buscado)
    candidato_compacto = _compactar_match(candidato)
    if not buscado_compacto or not candidato_compacto:
        return 0
    lcs = _lcs_len(buscado_compacto, candidato_compacto)
    cobertura_buscado = lcs / len(buscado_compacto)
    cobertura_candidato = lcs / len(candidato_compacto)
    return (cobertura_buscado * 0.8) + (cobertura_candidato * 0.2)


def _score_nombre(buscado, candidato):
    buscado_norm = _normalizar_para_match(buscado)
    candidato_norm = _normalizar_para_match(candidato)
    if not buscado_norm or not candidato_norm:
        return 0

    ratio = SequenceMatcher(None, buscado_norm, candidato_norm).ratio()
    ratio_compacto = SequenceMatcher(None, _compactar_match(buscado), _compactar_match(candidato)).ratio()
    orden_score = _score_orden_letras(buscado, candidato)
    tokens_buscados = buscado_norm.split()
    tokens_candidato = candidato_norm.split()
    token_hits = 0
    for token in tokens_buscados:
        if any(t.startswith(token) or token.startswith(t) for t in tokens_candidato):
            token_hits += 1
    token_score = token_hits / max(len(tokens_buscados), 1)
    prefix_bonus = 0.12 if candidato_norm.startswith(buscado_norm) else 0
    return max(
        (ratio * 0.45) + (ratio_compacto * 0.2) + (token_score * 0.25) + (orden_score * 0.1) + prefix_bonus,
        (orden_score * 0.7) + (ratio_compacto * 0.3),
    )


def buscar_empleado_en_lista(nombre: str):
    """Devuelve el nombre de la lista que coincide con el texto recibido."""
    nombre_limpio = _normalizar_para_match(nombre)
    if not nombre_limpio:
        return None
    for registro in _obtener_registros_empleados():
        if nombre_limpio == _normalizar_para_match(registro["nombre"]):
            return registro["nombre"]
    logging.info("Empleado no encontrado en la lista de Notion: %s", nombre)
    return None


def buscar_empleado_y_cargo(nombre: str) -> tuple[str | None, str | None]:
    """Devuelve (nombre_canonico, cargo) del empleado que coincide, o (None, None) si no existe."""
    nombre_limpio = _normalizar_para_match(nombre)
    if not nombre_limpio:
        return None, None
    for registro in _obtener_registros_empleados():
        if nombre_limpio == _normalizar_para_match(registro["nombre"]):
            return registro["nombre"], registro.get("cargo") or None
    logging.info("Empleado no encontrado en la lista de Notion: %s", nombre)
    return None, None


def obtener_cargo_por_slack_id(user_id: str) -> str | None:
    """Devuelve el cargo del empleado cuyo ID_usuario coincide con user_id."""
    for registro in _obtener_registros_empleados():
        if registro.get("id_usuario") == user_id:
            return registro.get("cargo") or None
    return None


def obtener_slack_ids_empleados() -> list[str]:
    """Devuelve todos los ID_usuario (Slack IDs) no vacíos de la lista de empleados."""
    return [r["id_usuario"] for r in _obtener_registros_empleados() if r.get("id_usuario")]




def sugerir_empleados_parecidos(nombre: str, limite: int = 8) -> list[str]:
    candidatos = []
    for registro in _obtener_registros_empleados():
        valores = [registro["nombre"], *registro.get("aliases", [])]
        score = max((_score_nombre(nombre, valor) for valor in valores if valor), default=0)
        candidatos.append((score, registro["nombre"]))
    candidatos.sort(key=lambda item: (-item[0], _normalizar_para_match(item[1])))
    sugerencias = []
    vistos = set()
    for score, empleado in candidatos:
        if len(sugerencias) >= 3 and score < 0.24:
            continue
        clave = _normalizar_para_match(empleado)
        if clave in vistos:
            continue
        vistos.add(clave)
        sugerencias.append(empleado)
        if len(sugerencias) >= limite:
            break
    return sugerencias


_cache_nombre_por_id: dict = {}


def obtener_nombre_por_id_usuario(user_id: str) -> str | None:
    """Busca el nombre en la lista de empleados por la columna ID_usuario."""
    with lock:
        if user_id in _cache_nombre_por_id:
            return _cache_nombre_por_id[user_id]
    try:
        db_id, _ = _retrieve_bbdd(config.NOTION_EMPLOYEES_DATABASE_ID)
        cursor = None
        while True:
            kwargs: dict = {"page_size": 100}
            if cursor:
                kwargs["start_cursor"] = cursor
            resp = _query_bbdd(db_id, **kwargs)
            for fila in resp.get("results", []):
                props = fila.get("properties", {})
                prop_id = props.get("ID_usuario", {})
                id_usuario = "".join(
                    p.get("plain_text", "")
                    for p in (prop_id.get("rich_text") or prop_id.get("title") or [])
                ).strip()
                if id_usuario != user_id:
                    continue
                prop_nombre = props.get("Nombre", {})
                nombre = "".join(
                    p.get("plain_text", "")
                    for p in (prop_nombre.get("rich_text") or prop_nombre.get("title") or [])
                ).strip()
                if nombre:
                    with lock:
                        _cache_nombre_por_id[user_id] = nombre
                    return nombre
            if not resp.get("has_more"):
                break
            cursor = resp.get("next_cursor")
        return None
    except Exception:
        logging.exception(f"Error buscando nombre para '{user_id}' en lista de empleados")
        return None


def validar_empleado_en_lista(nombre: str) -> bool:
    """Comprueba si un nombre coincide con algun empleado de la lista de Notion."""
    return buscar_empleado_en_lista(nombre) is not None


def listar_bbdd_evaluados():
    parent = obtener_parent_bbdd_evaluados()
    if parent is None:
        return []
    resultado = notion.search(query=config.PREFIJO_BBDD_EVALUADO, filter={"value": _tipo_objeto_busqueda_bbdd(), "property": "object"}, page_size=100)
    bases = []
    for bbdd in resultado.get("results", []):
        titulo = _extraer_titulo_bbdd(bbdd)
        if titulo.startswith(config.PREFIJO_BBDD_EVALUADO) and _coincide_parent_bbdd(bbdd, parent):
            bases.append({"id": _data_source_id(bbdd), "evaluado": titulo.removeprefix(config.PREFIJO_BBDD_EVALUADO)})
    return bases


def obtener_evaluaciones_de_bbdd(database_id, evaluado):
    evaluaciones = []
    try:
        cursor = None
        while True:
            kwargs = {"page_size": 100}
            if cursor:
                kwargs["start_cursor"] = cursor
            resultado = _query_bbdd(database_id, **kwargs)
            for pagina in resultado.get("results", []):
                props = pagina.get("properties", {})
                titulo_items = props.get("Name", {}).get("title", [])
                nombre_tecnico = titulo_items[0]["text"]["content"] if titulo_items else ""
                evaluador = _texto_rich_text(props, "Evaluador") or _texto_rich_text(props, "Persona que evalua") or nombre_tecnico or "Desconocido"
                fecha = (props.get("Fecha", {}).get("date") or {}).get("start", "")
                val_sup  = _texto_rich_text(props, "Valoración de superiores") or _texto_rich_text(props, "Mejor aspecto de superiores")
                val_igu  = _texto_rich_text(props, "Valoración de iguales") or _texto_rich_text(props, "Mejor aspecto de iguales")
                val_inf  = _texto_rich_text(props, "Valoración de inferiores") or _texto_rich_text(props, "Mejor aspecto de inferiores")
                jus_sup  = _texto_rich_text(props, "Justificación de superiores") or _texto_rich_text(props, "Peor aspecto de superiores")
                jus_igu  = _texto_rich_text(props, "Justificación de iguales") or _texto_rich_text(props, "Peor aspecto de iguales")
                jus_inf  = _texto_rich_text(props, "Justificación de inferiores") or _texto_rich_text(props, "Peor aspecto de inferiores")
                if val_sup or jus_sup:
                    relacion = "superior"
                    q1_act, q2_act = val_sup, jus_sup
                elif val_inf or jus_inf:
                    relacion = "inferior"
                    q1_act, q2_act = val_inf, jus_inf
                elif val_igu or jus_igu:
                    relacion = "igual"
                    q1_act, q2_act = val_igu, jus_igu
                else:
                    relacion = ""
                    q1_act = _texto_rich_text(props, "Mejor aspecto")
                    q2_act = _texto_rich_text(props, "Peor aspecto")
                evaluaciones.append({
                    "nombre": evaluador,
                    "evaluado": evaluado,
                    "persona_evaluada": evaluado,
                    "persona_que_evalua": evaluador,
                    "proyecto": _texto_rich_text(props, "Proyecto"),
                    "q1": q1_act,
                    "q2": q2_act,
                    "relacion": relacion,
                    "fecha": fecha,
                })
            if resultado.get("has_more"):
                cursor = resultado.get("next_cursor")
            else:
                break
    except Exception:
        logging.exception("Error leyendo evaluaciones de Notion")
    return evaluaciones


def obtener_evaluaciones():
    evaluaciones = []
    bases = listar_bbdd_evaluados()
    if not bases:
        return obtener_evaluaciones_de_bbdd(config.NOTION_DATABASE_ID, "General")
    for bbdd in bases:
        evaluaciones.extend(obtener_evaluaciones_de_bbdd(bbdd["id"], bbdd["evaluado"]))
    return evaluaciones


def obtener_evaluaciones_por_evaluado(evaluado):
    if not evaluado:
        raise RuntimeError("Selecciona una persona evaluada.")
    for bbdd in listar_bbdd_evaluados():
        if bbdd["evaluado"] == evaluado:
            return obtener_evaluaciones_de_bbdd(bbdd["id"], bbdd["evaluado"])
    raise RuntimeError(f"No se encontró una tabla de evaluaciones para {evaluado}.")


def obtener_historial_mis_evaluaciones(evaluado: str, evaluador: str, proyecto_web: str) -> list:
    """Devuelve las evaluaciones que `evaluador` registró sobre `evaluado` en un proyecto similar a `proyecto_web`."""
    import re as _re
    from difflib import SequenceMatcher

    def _tokenize(s):
        s = s.lower().replace("_", " ").replace("-", " ")
        return [w for w in s.split() if not _re.match(r"^\d{4}$", w) and len(w) >= 3]

    def _proyecto_coincide(pw, pn):
        tokens = _tokenize(pw)
        notion_low = pn.lower()
        tokens_n = _tokenize(pn)
        if not tokens or not pn.strip():
            return False
        for tok in tokens:
            if tok in notion_low:
                return True
            for ntok in tokens_n:
                if SequenceMatcher(None, tok, ntok).ratio() >= 0.78:
                    return True
        return False

    evaluador_norm = normalizar_nombre(evaluador)
    try:
        todas = obtener_evaluaciones_por_evaluado(evaluado)
    except Exception:
        return []

    resultado = []
    for ev in todas:
        if normalizar_nombre(ev.get("nombre", "")) != evaluador_norm:
            continue
        if proyecto_web and not _proyecto_coincide(proyecto_web, ev.get("proyecto", "")):
            continue
        resultado.append(ev)

    resultado.sort(key=lambda x: x.get("fecha", ""))
    return resultado


# ---------------------------------------------------------------------------
# Advisees y opiniones CA (para la web)
# ---------------------------------------------------------------------------

def _extraer_url_foto(prop: dict) -> str:
    tipo = prop.get("type", "")
    if tipo == "url":
        return prop.get("url") or ""
    if tipo == "files":
        for archivo in (prop.get("files") or []):
            if archivo.get("type") == "external":
                return archivo.get("external", {}).get("url", "")
            if archivo.get("type") == "file":
                return archivo.get("file", {}).get("url", "")
    if tipo in ("rich_text", "title"):
        return "".join(p.get("plain_text", "") for p in prop.get(tipo, [])).strip()
    return ""


_cache_lista_ca: dict = {"db_id": None}
_cache_advisees_por_ca: dict = {}


def obtener_advisees(ca_nombre: str, ca_aliases=None) -> list[str]:
    """Retorna los advisees de un CA desde 'Lista CA' (columna CA y columnas A1, A2, ...)."""
    ca_norms = {normalizar_nombre(valor) for valor in [ca_nombre, *(ca_aliases or [])] if valor}
    ca_norm = sorted(ca_norms)[0] if ca_norms else ""
    logging.info(f"[advisees] Buscando advisees para CA: '{ca_nombre}' (aliases: {ca_norms})")
    with lock:
        if ca_norm in _cache_advisees_por_ca:
            return _cache_advisees_por_ca[ca_norm]
    try:
        with lock:
            db_id = _cache_lista_ca["db_id"]
        if not db_id:
            resultado = notion.search(
                query="Lista CA",
                filter={"value": _tipo_objeto_busqueda_bbdd(), "property": "object"},
                page_size=50,
            )
            titulos_encontrados = []
            candidatos = []
            for bbdd in resultado.get("results", []):
                titulo = _extraer_titulo_bbdd(bbdd)
                titulos_encontrados.append(titulo)
                titulo_norm = normalizar_nombre(titulo)
                if titulo_norm == "lista ca":
                    db_id = _data_source_id(bbdd)
                    logging.info(f"[advisees] Base de datos CA encontrada (exacto): '{titulo}' (id: {db_id})")
                    with lock:
                        _cache_lista_ca["db_id"] = db_id
                    break
                if titulo_norm.startswith("lista ca"):
                    candidatos.append((titulo, _data_source_id(bbdd)))
            if not db_id and candidatos:
                titulo, db_id = candidatos[0]
                logging.info(f"[advisees] Base de datos CA encontrada (startswith): '{titulo}' (id: {db_id})")
                with lock:
                    _cache_lista_ca["db_id"] = db_id
            if not db_id:
                logging.warning(f"[advisees] No se encontró 'Lista CA'. Resultados: {titulos_encontrados}")
        if not db_id:
            return []
        cursor = None
        while True:
            kwargs: dict = {"page_size": 100}
            if cursor:
                kwargs["start_cursor"] = cursor
            resp = _query_bbdd(db_id, **kwargs)
            for fila in resp.get("results", []):
                props = fila.get("properties", {})
                prop_ca = props.get("CA", {})
                nombre_ca = "".join(
                    p.get("plain_text", "")
                    for p in (prop_ca.get("rich_text") or prop_ca.get("title") or [])
                ).strip()
                logging.info(f"[advisees] Fila encontrada con CA='{nombre_ca}' (norm: '{normalizar_nombre(nombre_ca)}')")
                if normalizar_nombre(nombre_ca) not in ca_norms:
                    continue
                pares = []
                for col_name, prop_val in props.items():
                    if not re.match(r'^A\d+$', col_name):
                        continue
                    nombre_a = "".join(
                        p.get("plain_text", "")
                        for p in (prop_val.get("rich_text") or prop_val.get("title") or [])
                    ).strip()
                    if nombre_a:
                        pares.append((int(col_name[1:]), nombre_a))
                pares.sort(key=lambda x: x[0])
                advisees = [nombre for _, nombre in pares]
                with lock:
                    _cache_advisees_por_ca[ca_norm] = advisees
                return advisees
            if not resp.get("has_more"):
                break
            cursor = resp.get("next_cursor")
        with lock:
            _cache_advisees_por_ca[ca_norm] = []
        return []
    except Exception:
        logging.exception(f"Error obteniendo advisees de '{ca_nombre}'")
        return []


def obtener_datos_empleados_por_nombres(nombres: list[str]) -> list[dict]:
    """Retorna {nombre, foto, email} para cada nombre desde 'Lista de empleados'."""
    if not nombres:
        return []
    nombres_norm = {normalizar_nombre(n): n for n in nombres}
    try:
        db_id, _ = _retrieve_bbdd(config.NOTION_EMPLOYEES_DATABASE_ID)
        resultado = []
        cursor = None
        while True:
            kwargs: dict = {"page_size": 100}
            if cursor:
                kwargs["start_cursor"] = cursor
            resp = _query_bbdd(db_id, **kwargs)
            for fila in resp.get("results", []):
                props = fila.get("properties", {})
                prop_nombre = props.get("Nombre", {})
                nombre = "".join(
                    p.get("plain_text", "")
                    for p in (prop_nombre.get("rich_text") or prop_nombre.get("title") or [])
                ).strip()
                if normalizar_nombre(nombre) not in nombres_norm:
                    continue
                foto = _extraer_url_foto(props.get("Foto", {}))
                prop_correo = props.get("Correo", props.get("correo", props.get("email", props.get("Email", {}))))
                correo = prop_correo.get("email") or prop_correo.get("url") or "".join(
                    p.get("plain_text", "")
                    for p in (prop_correo.get("rich_text") or prop_correo.get("title") or [])
                ).strip()
                resultado.append({"nombre": nombre, "foto": foto or "", "email": correo or ""})
            if not resp.get("has_more"):
                break
            cursor = resp.get("next_cursor")
        return resultado
    except Exception:
        logging.exception("Error obteniendo datos de empleados por nombres")
        return [{"nombre": n, "foto": "", "email": ""} for n in nombres]


def obtener_opiniones_ca_por_advisee(ca_nombre: str, advisee: str, ca_aliases=None) -> list[dict]:
    """Retorna las opiniones guardadas por el CA sobre el advisee, ordenadas por fecha desc."""
    ca_norms = {normalizar_nombre(valor) for valor in [ca_nombre, *(ca_aliases or [])] if valor}
    advisee_norm = normalizar_nombre(advisee)

    def texto_alias(props, nombres):
        for nombre in nombres:
            if nombre in props:
                valor = _texto_propiedad(props, nombre)
                if valor:
                    return valor
        return ""

    def buscar_bbdd(titulo):
        db_id = _buscar_bbdd_en_pagina(config.NOTION_CA_TRACKING_PAGE_NAME, titulo)
        if db_id:
            return db_id
        resultado = notion.search(
            query=titulo,
            filter={"value": _tipo_objeto_busqueda_bbdd(), "property": "object"},
            page_size=25,
        )
        for bbdd in resultado.get("results", []):
            if normalizar_nombre(_extraer_titulo_bbdd(bbdd)) == normalizar_nombre(titulo):
                return _data_source_id(bbdd)
        return None

    def leer_opiniones_nuevo(db_id):
        opiniones = []
        cursor = None
        while True:
            kwargs: dict = {"page_size": 100}
            if cursor:
                kwargs["start_cursor"] = cursor
            resp = _query_bbdd(db_id, **kwargs)
            for fila in resp.get("results", []):
                props = fila.get("properties", {})
                ca_texto = texto_alias(props, ("CA", "CA que le evalua", "CA que le evalúa", "Career Advisor", "Evaluador"))
                if ca_texto and normalizar_nombre(ca_texto) not in ca_norms:
                    continue
                opinion = texto_alias(props, ("Opinion", "Opinión"))
                resumen = texto_alias(props, ("Resumen", "Resumen_advisee", "Resumen sobre lo que se opina", "Resumen evaluaciones"))
                fecha = (props.get("Fecha", {}).get("date") or {}).get("start", "")
                if not any((fecha, ca_texto, opinion, resumen)):
                    continue
                opiniones.append({"fecha": fecha, "ca": ca_texto, "opinion": opinion, "resumen_advisee": resumen})
            if not resp.get("has_more"):
                break
            cursor = resp.get("next_cursor")
        return opiniones

    def leer_opiniones_antiguo(db_id):
        opiniones = []
        cursor = None
        while True:
            kwargs: dict = {"page_size": 100}
            if cursor:
                kwargs["start_cursor"] = cursor
            resp = _query_bbdd(db_id, **kwargs)
            for fila in resp.get("results", []):
                props = fila.get("properties", {})
                advisee_texto = texto_alias(props, ("Advisee", "Persona evaluada", "Evaluado"))
                if normalizar_nombre(advisee_texto) != advisee_norm:
                    continue
                opinion = texto_alias(props, ("Opinion", "Opinión"))
                resumen = texto_alias(props, ("Resumen", "Resumen_advisee", "Resumen sobre lo que se opina", "Resumen evaluaciones"))
                fecha = (props.get("Fecha", {}).get("date") or {}).get("start", "")
                ca_texto = texto_alias(props, ("CA", "Evaluador", "CA que le evalua", "CA que le evalúa"))
                if not any((fecha, ca_texto, opinion, resumen)):
                    continue
                opiniones.append({"fecha": fecha, "ca": ca_texto or ca_nombre, "opinion": opinion, "resumen_advisee": resumen})
            if not resp.get("has_more"):
                break
            cursor = resp.get("next_cursor")
        return opiniones

    try:
        opiniones = []
        db_nuevo = buscar_bbdd(f"Opiniones - {advisee.strip()}")
        if db_nuevo:
            try:
                opiniones.extend(leer_opiniones_nuevo(db_nuevo))
            except Exception:
                logging.exception("No se pudieron leer opiniones nuevas para %s", advisee)

        db_antiguo = buscar_bbdd(f"Opiniones CA - {ca_nombre.strip()}")
        if db_antiguo:
            try:
                opiniones.extend(leer_opiniones_antiguo(db_antiguo))
            except Exception:
                logging.exception("No se pudieron leer opiniones antiguas para %s / %s", ca_nombre, advisee)

        return sorted(opiniones, key=lambda x: x.get("fecha", ""), reverse=True)
    except Exception:
        logging.exception(f"Error obteniendo opiniones de '{ca_nombre}' sobre '{advisee}'")
        return []


def listar_advisees_con_opiniones_ca(ca_nombre: str, ca_aliases=None) -> list[str]:
    """Lista evaluados con base 'Opiniones - ...' en Seguimiento CA para este CA."""
    ca_norms = {normalizar_nombre(valor) for valor in [ca_nombre, *(ca_aliases or [])] if valor}
    if not ca_norms:
        return []

    def texto_alias(props, nombres):
        for nombre in nombres:
            if nombre in props:
                valor = _texto_propiedad(props, nombre)
                if valor:
                    return valor
        return ""

    def db_desde_bloque(bloque):
        if bloque.get("type") == "child_database":
            titulo = _titulo_child_database(bloque)
            try:
                db = notion.databases.retrieve(database_id=bloque["id"])
                return titulo, _data_source_id(db)
            except Exception:
                logging.exception("No se pudo resolver la base de seguimiento %s", titulo)
                return titulo, bloque["id"]
        if bloque.get("type") == "link_to_page":
            target_id = _target_link_to_page(bloque)
            if not target_id:
                return "", None
            try:
                db = notion.databases.retrieve(database_id=target_id)
                return _extraer_titulo_bbdd(db), _data_source_id(db)
            except Exception:
                return "", None
        return "", None

    try:
        parent = _parent_bbdd_en_pagina(config.NOTION_CA_TRACKING_PAGE_NAME, crear=False)
        if parent.get("type") != "page_id":
            return []

        encontrados = []
        for bloque in _iter_blocks(parent["page_id"]):
            titulo, db_id = db_desde_bloque(bloque)
            if not db_id or not titulo.startswith("Opiniones - "):
                continue

            try:
                resp = _query_bbdd(db_id, page_size=100)
            except Exception:
                logging.exception("No se pudo leer la base de seguimiento %s", titulo)
                continue

            for fila in resp.get("results", []):
                props = fila.get("properties", {})
                ca_texto = texto_alias(props, ("CA", "CA que le evalua", "CA que le evalúa", "Career Advisor", "Evaluador"))
                if normalizar_nombre(ca_texto) in ca_norms:
                    encontrados.append(titulo.removeprefix("Opiniones - ").strip())
                    break

        return sorted(set(encontrados), key=normalizar_nombre)
    except Exception:
        logging.exception("Error listando advisees con opiniones CA")
        return []


_PROPS_OBJETIVOS = {
    "Name":      {"title": {}},
    "Fecha":     {"date": {}},
    "CA":        {"rich_text": {}},
    "Objetivos": {"rich_text": {}},
    "Nombre":    {"rich_text": {}},
}

_cache_objetivos_db: dict = {"db_id": None}


def _obtener_o_crear_bbdd_objetivos() -> str:
    with lock:
        db_id = _cache_objetivos_db["db_id"]
    if db_id:
        return db_id

    titulo = "Objetivos empleados"
    resultado = notion.search(
        query=titulo,
        filter={"value": _tipo_objeto_busqueda_bbdd(), "property": "object"},
        page_size=20,
    )
    for bbdd in resultado.get("results", []):
        if normalizar_nombre(_extraer_titulo_bbdd(bbdd)) == normalizar_nombre(titulo):
            db_id = _data_source_id(bbdd)
            with lock:
                _cache_objetivos_db["db_id"] = db_id
            return db_id

    parent = None
    try:
        res_pages = notion.search(
            query="Listas de datos",
            filter={"value": "page", "property": "object"},
            page_size=10,
        )
        for page in res_pages.get("results", []):
            if normalizar_nombre(_extraer_titulo_pagina(page)) == "listas de datos":
                parent = {"type": "page_id", "page_id": page["id"]}
                break
    except Exception:
        pass
    if parent is None:
        parent = _parent_bbdd_referencia()

    if _usa_data_sources():
        nueva = notion.databases.create(
            parent=parent,
            title=[{"type": "text", "text": {"content": titulo}}],
            initial_data_source={
                "title": [{"type": "text", "text": {"content": titulo}}],
                "properties": _PROPS_OBJETIVOS,
            },
        )
        nueva = notion.databases.retrieve(database_id=nueva["id"])
    else:
        nueva = notion.databases.create(
            parent=parent,
            title=[{"type": "text", "text": {"content": titulo}}],
            properties=_PROPS_OBJETIVOS,
        )

    db_id = _data_source_id(nueva)
    with lock:
        _cache_objetivos_db["db_id"] = db_id
    return db_id


def guardar_objetivos(ca_nombre: str, advisee_nombre: str, texto: str) -> None:
    db_id = _obtener_o_crear_bbdd_objetivos()
    fecha_iso = datetime.now(timezone.utc).isoformat()
    fecha_str = datetime.now(config.ZONA_HORARIA_MADRID).strftime("%Y-%m-%d")
    _crear_pagina_en_bbdd(
        db_id,
        {
            "Name":      {"title":     [{"text": {"content": f"Objetivos {advisee_nombre} {fecha_str}"}}]},
            "Fecha":     {"date":      {"start": fecha_iso}},
            "CA":        {"rich_text": [{"text": {"content": ca_nombre[:2000]}}]},
            "Objetivos": {"rich_text": [{"text": {"content": texto[:2000]}}]},
            "Nombre":    {"rich_text": [{"text": {"content": advisee_nombre[:2000]}}]},
        },
    )


def obtener_objetivos(advisee_nombre: str) -> list[dict]:
    try:
        db_id = _obtener_o_crear_bbdd_objetivos()
        nombre_norm = normalizar_nombre(advisee_nombre)
        resultados = []
        cursor = None
        while True:
            kwargs: dict = {"page_size": 100}
            if cursor:
                kwargs["start_cursor"] = cursor
            resp = _query_bbdd(db_id, **kwargs)
            for pagina in resp.get("results", []):
                props = pagina.get("properties", {})
                nombre_val = "".join(
                    p.get("plain_text", "") for p in (props.get("Nombre", {}).get("rich_text") or [])
                ).strip()
                if normalizar_nombre(nombre_val) != nombre_norm:
                    continue
                fecha_prop = props.get("Fecha", {}).get("date") or {}
                ca_val = "".join(p.get("plain_text", "") for p in (props.get("CA", {}).get("rich_text") or [])).strip()
                objetivos_val = "".join(p.get("plain_text", "") for p in (props.get("Objetivos", {}).get("rich_text") or [])).strip()
                resultados.append({"fecha": fecha_prop.get("start", ""), "ca": ca_val, "objetivos": objetivos_val})
            if not resp.get("has_more"):
                break
            cursor = resp.get("next_cursor")
        resultados.sort(key=lambda x: x["fecha"] or "", reverse=True)
        return resultados
    except Exception:
        logging.exception(f"Error obteniendo objetivos de '{advisee_nombre}'")
        return []


# ---------------------------------------------------------------------------
# Objetivos por persona (base de datos "Objetivos - {nombre}")
# ---------------------------------------------------------------------------

_PROPS_OBJETIVO_PERSONA = {
    "Name":        {"title": {}},
    "Fecha":       {"date": {}},
    "CA":          {"rich_text": {}},
    "KPIs":        {"rich_text": {}},
    "Descripcion": {"rich_text": {}},
    "Tipo":        {"rich_text": {}},
}

_cache_objetivos_persona: dict = {}  # cache_key -> db_id


def _obtener_o_crear_bbdd_objetivos_persona(nombre: str) -> str:
    nombre_strip = nombre.strip()
    titulo = f"Objetivos - {nombre_strip}"
    cache_key = normalizar_nombre(titulo)
    with lock:
        if cache_key in _cache_objetivos_persona:
            return _cache_objetivos_persona[cache_key]

    # Busca o crea la página contenedora "Objetivos empleados"
    parent = _parent_bbdd_en_pagina("Objetivos empleados", crear=True)

    resultado = notion.search(
        query=titulo,
        filter={"value": _tipo_objeto_busqueda_bbdd(), "property": "object"},
        page_size=20,
    )
    for bbdd in resultado.get("results", []):
        if normalizar_nombre(_extraer_titulo_bbdd(bbdd)) == cache_key:
            db_id = _data_source_id(bbdd)
            with lock:
                _cache_objetivos_persona[cache_key] = db_id
            return db_id

    if _usa_data_sources():
        nueva = notion.databases.create(
            parent=parent,
            title=[{"type": "text", "text": {"content": titulo}}],
            initial_data_source={
                "title": [{"type": "text", "text": {"content": titulo}}],
                "properties": _PROPS_OBJETIVO_PERSONA,
            },
        )
        nueva = notion.databases.retrieve(database_id=nueva["id"])
    else:
        nueva = notion.databases.create(
            parent=parent,
            title=[{"type": "text", "text": {"content": titulo}}],
            properties=_PROPS_OBJETIVO_PERSONA,
        )

    db_id = _data_source_id(nueva)
    with lock:
        _cache_objetivos_persona[cache_key] = db_id
    logging.info("Base de datos objetivos persona creada: %s", titulo)
    return db_id


def guardar_objetivo_persona(ca_nombre: str, advisee_nombre: str, titulo: str, kpis: str, descripcion: str, tipo: str) -> None:
    db_id = _obtener_o_crear_bbdd_objetivos_persona(advisee_nombre)
    fecha_iso = datetime.now(timezone.utc).isoformat()
    _crear_pagina_en_bbdd(
        db_id,
        {
            "Name":        {"title":     [{"text": {"content": titulo[:2000]}}]},
            "Fecha":       {"date":      {"start": fecha_iso}},
            "CA":          {"rich_text": [{"text": {"content": ca_nombre[:2000]}}]},
            "KPIs":        {"rich_text": [{"text": {"content": kpis[:2000]}}]},
            "Descripcion": {"rich_text": [{"text": {"content": descripcion[:2000]}}]},
            "Tipo":        {"rich_text": [{"text": {"content": tipo[:2000]}}]},
        },
    )


def obtener_objetivos_persona(advisee_nombre: str) -> list[dict]:
    try:
        db_id = _obtener_o_crear_bbdd_objetivos_persona(advisee_nombre)
        resultados = []
        cursor = None
        while True:
            kwargs: dict = {"page_size": 100}
            if cursor:
                kwargs["start_cursor"] = cursor
            resp = _query_bbdd(db_id, **kwargs)
            for pagina in resp.get("results", []):
                props = pagina.get("properties", {})
                titulo_val = "".join(
                    p.get("plain_text", "") for p in (props.get("Name", {}).get("title") or [])
                ).strip()
                ca_val = "".join(
                    p.get("plain_text", "") for p in (props.get("CA", {}).get("rich_text") or [])
                ).strip()
                kpis_val = "".join(
                    p.get("plain_text", "") for p in (props.get("KPIs", {}).get("rich_text") or [])
                ).strip()
                desc_val = "".join(
                    p.get("plain_text", "") for p in (props.get("Descripcion", {}).get("rich_text") or [])
                ).strip()
                tipo_val = "".join(
                    p.get("plain_text", "") for p in (props.get("Tipo", {}).get("rich_text") or [])
                ).strip()
                fecha_prop = props.get("Fecha", {}).get("date") or {}
                resultados.append({
                    "page_id": pagina["id"],
                    "titulo": titulo_val,
                    "ca": ca_val,
                    "kpis": kpis_val,
                    "descripcion": desc_val,
                    "tipo": tipo_val,
                    "fecha": fecha_prop.get("start", ""),
                })
            if not resp.get("has_more"):
                break
            cursor = resp.get("next_cursor")
        resultados.sort(key=lambda x: x["fecha"] or "", reverse=True)
        return resultados
    except Exception:
        logging.exception("Error obteniendo objetivos persona de '%s'", advisee_nombre)
        return []


def eliminar_objetivo_persona(page_id: str) -> bool:
    try:
        notion.pages.update(page_id=page_id, archived=True)
        return True
    except Exception:
        logging.exception("Error eliminando objetivo %s", page_id)
        return False


# ---------------------------------------------------------------------------
# Lista CA: helpers para acceso y búsqueda de CA por empleado
# ---------------------------------------------------------------------------

def _obtener_db_id_lista_ca() -> str | None:
    with lock:
        db_id = _cache_lista_ca["db_id"]
    if db_id:
        return db_id
    try:
        resultado = notion.search(
            query="Lista CA",
            filter={"value": _tipo_objeto_busqueda_bbdd(), "property": "object"},
            page_size=10,
        )
        for bbdd in resultado.get("results", []):
            titulo = normalizar_nombre(_extraer_titulo_bbdd(bbdd))
            if titulo == "lista ca" or titulo.startswith("lista ca"):
                db_id = _data_source_id(bbdd)
                with lock:
                    _cache_lista_ca["db_id"] = db_id
                return db_id
    except Exception:
        logging.exception("Error buscando 'Lista CA'")
    return None


def _asegurar_columna_acceso_lista_ca(db_id: str) -> None:
    try:
        bbdd = notion.databases.retrieve(database_id=db_id)
        if "Acceso habilitado" not in bbdd.get("properties", {}):
            notion.databases.update(database_id=db_id, properties={"Acceso habilitado": {"checkbox": {}}})
    except Exception:
        logging.exception("No se pudo asegurar columna 'Acceso habilitado' en Lista CA")


def _ca_fila_por_nombre(db_id: str, ca_norms) -> dict | None:
    if isinstance(ca_norms, str):
        ca_norms = {ca_norms}
    cursor = None
    while True:
        kwargs: dict = {"page_size": 100}
        if cursor:
            kwargs["start_cursor"] = cursor
        resp = _query_bbdd(db_id, **kwargs)
        for fila in resp.get("results", []):
            props = fila.get("properties", {})
            prop_ca = props.get("CA", {})
            nombre_ca = "".join(
                p.get("plain_text", "")
                for p in (prop_ca.get("rich_text") or prop_ca.get("title") or [])
            ).strip()
            if normalizar_nombre(nombre_ca) in ca_norms:
                return fila
        if not resp.get("has_more"):
            break
        cursor = resp.get("next_cursor")
    return None


def obtener_ca_de_empleado(empleado_nombre: str) -> str | None:
    """Busca quién es el CA de un empleado revisando las columnas A1, A2... de Lista CA."""
    empleado_norm = normalizar_nombre(empleado_nombre)
    db_id = _obtener_db_id_lista_ca()
    if not db_id:
        return None
    try:
        cursor = None
        while True:
            kwargs: dict = {"page_size": 100}
            if cursor:
                kwargs["start_cursor"] = cursor
            resp = _query_bbdd(db_id, **kwargs)
            for fila in resp.get("results", []):
                props = fila.get("properties", {})
                for col_name, prop_val in props.items():
                    if not re.match(r"^A\d+$", col_name):
                        continue
                    nombre_a = "".join(
                        p.get("plain_text", "")
                        for p in (prop_val.get("rich_text") or prop_val.get("title") or [])
                    ).strip()
                    if normalizar_nombre(nombre_a) == empleado_norm:
                        prop_ca = props.get("CA", {})
                        ca = "".join(
                            p.get("plain_text", "")
                            for p in (prop_ca.get("rich_text") or prop_ca.get("title") or [])
                        ).strip()
                        return ca or None
            if not resp.get("has_more"):
                break
            cursor = resp.get("next_cursor")
    except Exception:
        logging.exception("Error buscando CA de '%s'", empleado_nombre)
    return None


_cache_acceso_ca_db: dict = {"db_id": None}


def _norm_ca(nombre: str) -> str:
    sin_acentos = unicodedata.normalize("NFD", nombre).encode("ascii", "ignore").decode("ascii")
    return " ".join(sin_acentos.strip().lower().split())


def _obtener_o_crear_bbdd_acceso_ca() -> str:
    with lock:
        db_id = _cache_acceso_ca_db["db_id"]
    if db_id:
        return db_id
    titulo = "Acceso CA"
    resultado = notion.search(
        query=titulo,
        filter={"value": _tipo_objeto_busqueda_bbdd(), "property": "object"},
        page_size=10,
    )
    for bbdd in resultado.get("results", []):
        if normalizar_nombre(_extraer_titulo_bbdd(bbdd)) == "acceso ca":
            db_id = _data_source_id(bbdd)
            with lock:
                _cache_acceso_ca_db["db_id"] = db_id
            return db_id
    parent = _parent_bbdd_referencia()
    props = {"Name": {"title": {}}, "Activo": {"checkbox": {}}}
    if _usa_data_sources():
        nueva = notion.databases.create(
            parent=parent,
            title=[{"type": "text", "text": {"content": titulo}}],
            initial_data_source={"title": [{"type": "text", "text": {"content": titulo}}], "properties": props},
        )
        nueva = notion.databases.retrieve(database_id=nueva["id"])
    else:
        nueva = notion.databases.create(
            parent=parent,
            title=[{"type": "text", "text": {"content": titulo}}],
            properties=props,
        )
    db_id = _data_source_id(nueva)
    with lock:
        _cache_acceso_ca_db["db_id"] = db_id
    logging.info("Base de datos 'Acceso CA' creada en Notion")
    return db_id


def _acceso_ca_fila(db_id: str, ca_keys: set) -> dict | None:
    cursor = None
    while True:
        kwargs: dict = {"page_size": 100}
        if cursor:
            kwargs["start_cursor"] = cursor
        resp = _query_bbdd(db_id, **kwargs)
        for fila in resp.get("results", []):
            nombre = _extraer_titulo_pagina(fila)
            if _norm_ca(nombre) in ca_keys:
                return fila
        if not resp.get("has_more"):
            break
        cursor = resp.get("next_cursor")
    return None


def ca_tiene_acceso_activo(ca_nombre: str, ca_aliases=None) -> bool:
    ca_keys = {_norm_ca(n) for n in [ca_nombre, *(ca_aliases or [])] if n}
    try:
        db_id = _obtener_o_crear_bbdd_acceso_ca()
        fila = _acceso_ca_fila(db_id, ca_keys)
        if not fila:
            return False
        return bool(fila.get("properties", {}).get("Activo", {}).get("checkbox", False))
    except Exception:
        logging.exception("Error verificando acceso de CA '%s'", ca_nombre)
        return False


def toggle_acceso_advisees(ca_nombre: str, activo: bool, ca_aliases=None) -> bool:
    ca_keys = {_norm_ca(n) for n in [ca_nombre, *(ca_aliases or [])] if n}
    try:
        db_id = _obtener_o_crear_bbdd_acceso_ca()
        fila = _acceso_ca_fila(db_id, ca_keys)
        if fila:
            notion.pages.update(page_id=fila["id"], properties={"Activo": {"checkbox": activo}})
        else:
            _crear_pagina_en_bbdd(db_id, {
                "Name": {"title": [{"text": {"content": ca_nombre}}]},
                "Activo": {"checkbox": activo},
            })
        return True
    except Exception:
        logging.exception("Error actualizando acceso de CA '%s'", ca_nombre)
        return False


# ---------------------------------------------------------------------------
# Acceso individual por advisee
# ---------------------------------------------------------------------------

_cache_acceso_individual_db: dict = {"db_id": None}


def _obtener_o_crear_bbdd_acceso_individual() -> str:
    with lock:
        db_id = _cache_acceso_individual_db["db_id"]
    if db_id:
        return db_id
    titulo = "Acceso Individual Advisee"
    resultado = notion.search(
        query=titulo,
        filter={"value": _tipo_objeto_busqueda_bbdd(), "property": "object"},
        page_size=10,
    )
    for bbdd in resultado.get("results", []):
        if normalizar_nombre(_extraer_titulo_bbdd(bbdd)) == normalizar_nombre(titulo):
            db_id = _data_source_id(bbdd)
            with lock:
                _cache_acceso_individual_db["db_id"] = db_id
            return db_id
    parent = _parent_bbdd_referencia()
    props = {"Name": {"title": {}}, "CA": {"rich_text": {}}, "Activo": {"checkbox": {}}}
    if _usa_data_sources():
        nueva = notion.databases.create(
            parent=parent,
            title=[{"type": "text", "text": {"content": titulo}}],
            initial_data_source={"title": [{"type": "text", "text": {"content": titulo}}], "properties": props},
        )
        nueva = notion.databases.retrieve(database_id=nueva["id"])
    else:
        nueva = notion.databases.create(
            parent=parent,
            title=[{"type": "text", "text": {"content": titulo}}],
            properties=props,
        )
    db_id = _data_source_id(nueva)
    with lock:
        _cache_acceso_individual_db["db_id"] = db_id
    logging.info("Base de datos 'Acceso Individual Advisee' creada en Notion")
    return db_id


def _acceso_individual_fila(db_id: str, advisee_key: str, ca_key: str) -> dict | None:
    cursor = None
    while True:
        kwargs: dict = {"page_size": 100}
        if cursor:
            kwargs["start_cursor"] = cursor
        resp = _query_bbdd(db_id, **kwargs)
        for fila in resp.get("results", []):
            nombre = _extraer_titulo_pagina(fila)
            ca_val = ""
            ca_prop = fila.get("properties", {}).get("CA", {}).get("rich_text", [])
            if ca_prop:
                ca_val = ca_prop[0].get("plain_text", "")
            if _norm_ca(nombre) == advisee_key and _norm_ca(ca_val) == ca_key:
                return fila
        if not resp.get("has_more"):
            break
        cursor = resp.get("next_cursor")
    return None


def advisee_tiene_acceso_individual(advisee: str, ca_nombre: str) -> bool:
    try:
        db_id = _obtener_o_crear_bbdd_acceso_individual()
        fila = _acceso_individual_fila(db_id, _norm_ca(advisee), _norm_ca(ca_nombre))
        if not fila:
            return False
        return bool(fila.get("properties", {}).get("Activo", {}).get("checkbox", False))
    except Exception:
        logging.exception("Error verificando acceso individual de advisee '%s'", advisee)
        return False


def toggle_acceso_advisee_individual(ca_nombre: str, advisee: str, activo: bool) -> bool:
    try:
        db_id = _obtener_o_crear_bbdd_acceso_individual()
        fila = _acceso_individual_fila(db_id, _norm_ca(advisee), _norm_ca(ca_nombre))
        if fila:
            notion.pages.update(page_id=fila["id"], properties={"Activo": {"checkbox": activo}})
        else:
            _crear_pagina_en_bbdd(db_id, {
                "Name": {"title": [{"text": {"content": advisee}}]},
                "CA": {"rich_text": [{"text": {"content": ca_nombre}}]},
                "Activo": {"checkbox": activo},
            })
        return True
    except Exception:
        logging.exception("Error actualizando acceso individual de advisee '%s'", advisee)
        return False


# ---------------------------------------------------------------------------
# Informes Finales
# ---------------------------------------------------------------------------

_PROPS_INFORMES_FINALES = {
    "Name": {"title": {}},
    "CA": {"rich_text": {}},
    "Fecha": {"date": {}},
    "Archivo_docx": {"rich_text": {}},
    "Archivo_html": {"rich_text": {}},
    "URL": {"url": {}},
}

_cache_informes_finales_db: dict = {"db_id": None}


def _obtener_o_crear_bbdd_informes_finales() -> str:
    with lock:
        db_id = _cache_informes_finales_db["db_id"]
    if db_id:
        return db_id
    titulo = "Informes Finales"
    resultado = notion.search(
        query=titulo,
        filter={"value": _tipo_objeto_busqueda_bbdd(), "property": "object"},
        page_size=10,
    )
    for bbdd in resultado.get("results", []):
        if normalizar_nombre(_extraer_titulo_bbdd(bbdd)) == normalizar_nombre(titulo):
            db_id = _data_source_id(bbdd)
            with lock:
                _cache_informes_finales_db["db_id"] = db_id
            return db_id
    parent = _parent_bbdd_referencia()
    if _usa_data_sources():
        nueva = notion.databases.create(
            parent=parent,
            title=[{"type": "text", "text": {"content": titulo}}],
            initial_data_source={"title": [{"type": "text", "text": {"content": titulo}}], "properties": _PROPS_INFORMES_FINALES},
        )
        nueva = notion.databases.retrieve(database_id=nueva["id"])
    else:
        nueva = notion.databases.create(
            parent=parent,
            title=[{"type": "text", "text": {"content": titulo}}],
            properties=_PROPS_INFORMES_FINALES,
        )
    db_id = _data_source_id(nueva)
    with lock:
        _cache_informes_finales_db["db_id"] = db_id
    logging.info("Base de datos 'Informes Finales' creada en Notion")
    return db_id


def guardar_informe_final(ca_nombre: str, advisee: str, docx_filename: str, html_filename: str, url: str) -> None:
    db_id = _obtener_o_crear_bbdd_informes_finales()
    advisee_norm = normalizar_nombre(advisee)

    existentes = []
    cursor = None
    while True:
        kwargs: dict = {"page_size": 100}
        if cursor:
            kwargs["start_cursor"] = cursor
        resp = _query_bbdd(db_id, **kwargs)
        for fila in resp.get("results", []):
            props = fila.get("properties", {})
            nombre_val = " ".join(p.get("plain_text", "") for p in props.get("Name", {}).get("title", [])).strip()
            if normalizar_nombre(nombre_val) != advisee_norm:
                continue
            existentes.append({
                "page_id": fila["id"],
                "fecha": (props.get("Fecha", {}).get("date") or {}).get("start", ""),
                "docx": _texto_rich_text(props, "Archivo_docx"),
                "html": _texto_rich_text(props, "Archivo_html"),
            })
        if not resp.get("has_more"):
            break
        cursor = resp.get("next_cursor")

    existentes.sort(key=lambda x: x["fecha"])
    while len(existentes) >= 2:
        oldest = existentes.pop(0)
        try:
            notion.pages.update(page_id=oldest["page_id"], archived=True)
        except Exception:
            logging.exception("No se pudo archivar informe final antiguo %s", oldest["page_id"])
        for fname in (oldest.get("docx", ""), oldest.get("html", "")):
            if fname:
                try:
                    ruta = os.path.join(config.CARPETA_WEB, os.path.basename(fname))
                    if os.path.exists(ruta):
                        os.remove(ruta)
                except Exception:
                    logging.exception("No se pudo borrar archivo antiguo: %s", fname)

    fecha = datetime.now(timezone.utc)
    _crear_pagina_en_bbdd(db_id, {
        "Name": {"title": [{"text": {"content": advisee}}]},
        "CA": {"rich_text": [{"text": {"content": ca_nombre}}]},
        "Fecha": {"date": {"start": fecha.isoformat()}},
        "Archivo_docx": {"rich_text": [{"text": {"content": docx_filename}}]},
        "Archivo_html": {"rich_text": [{"text": {"content": html_filename}}]},
        "URL": {"url": url},
    })


def obtener_informe_final_reciente(advisee: str) -> dict | None:
    """Devuelve {pdf, html} del informe final más reciente del advisee, o None."""
    try:
        db_id = _obtener_o_crear_bbdd_informes_finales()
        advisee_norm = normalizar_nombre(advisee)
        registros = []
        cursor = None
        while True:
            kwargs: dict = {"page_size": 100}
            if cursor:
                kwargs["start_cursor"] = cursor
            resp = _query_bbdd(db_id, **kwargs)
            for fila in resp.get("results", []):
                props = fila.get("properties", {})
                nombre_val = " ".join(p.get("plain_text", "") for p in props.get("Name", {}).get("title", [])).strip()
                if normalizar_nombre(nombre_val) != advisee_norm:
                    continue
                registros.append({
                    "fecha": (props.get("Fecha", {}).get("date") or {}).get("start", ""),
                    "docx": _texto_rich_text(props, "Archivo_docx"),
                    "html": _texto_rich_text(props, "Archivo_html"),
                })
            if not resp.get("has_more"):
                break
            cursor = resp.get("next_cursor")
        if not registros:
            return None
        registros.sort(key=lambda x: x["fecha"], reverse=True)
        return registros[0]
    except Exception:
        logging.exception("Error obteniendo informe final de '%s'", advisee)
        return None


# ---------------------------------------------------------------------------
# Evaluaciones Personales
# ---------------------------------------------------------------------------

_cache_personales_page_id: dict = {"page_id": None}
_cache_personal_eval_db: dict = {"db_id": None}
_cache_personal_preguntas_db: dict = {"db_id": None}
_cache_personal_preguntas: dict = {}
_PERSONAL_PREGUNTAS_TTL = 300

_PROPS_EVALUACIONES_PERSONALES = {
    "Nombre": {"title": {}},
    "Fecha": {"date": {}},
    "Comentario": {"rich_text": {}},
}

_PROPS_PERSONAL_PREGUNTAS = {
    "Clave": {"title": {}},
    "Texto": {"rich_text": {}},
}

PREGUNTAS_PERSONALES_DEFAULT = {
    "mensaje_inicial": (
        "📝 *Tienes opción de seguimiento personal pendiente*\n\n"
        "_Esta evaluación es totalmente privada, solo podrá verla tu CA._\n"
        "_Si en algún momento quieres cancelar, escribe SOS en el hilo._\n\n"
        "Inmediatamente se te mandarán tus objetivos actuales para que puedas reflexionar "
        "si te estás dirigiendo hacia conseguirlos. 🏆"
    ),
}

_mensaje_inicial_migrado: set = set()


def _migrar_mensaje_inicial(db_id: str) -> None:
    if db_id in _mensaje_inicial_migrado:
        return
    _mensaje_inicial_migrado.add(db_id)
    try:
        resp = _query_bbdd(db_id, filter={"property": "Clave", "title": {"equals": "mensaje_inicial"}})
        for fila in resp.get("results", []):
            props = fila.get("properties", {})
            texto_actual = " ".join(p.get("plain_text", "") for p in props.get("Texto", {}).get("rich_text", [])).strip()
            if "SOS en cualquier momento" in texto_actual or "Pulsa cualquier tecla" in texto_actual:
                notion.pages.update(
                    page_id=fila["id"],
                    properties={"Texto": {"rich_text": [{"type": "text", "text": {"content": PREGUNTAS_PERSONALES_DEFAULT["mensaje_inicial"]}}]}},
                )
                with lock:
                    _cache_personal_preguntas.clear()
    except Exception:
        logging.exception("Error migrando mensaje_inicial personal")


def _obtener_o_crear_pagina_personales() -> str | None:
    with lock:
        page_id = _cache_personales_page_id["page_id"]
    if page_id:
        return page_id

    ref = _parent_bbdd_en_pagina("Evaluaciones Personales", crear=True)
    if ref.get("type") != "page_id":
        logging.warning("No se pudo localizar/crear la página 'Evaluaciones Personales'")
        return None

    with lock:
        _cache_personales_page_id["page_id"] = ref["page_id"]
    return ref["page_id"]


def _buscar_o_crear_bbdd_en_personales(titulo_db: str, props: dict, cache: dict, poblar=None) -> str | None:
    with lock:
        db_id = cache["db_id"]
    if db_id:
        return db_id

    personales_id = _obtener_o_crear_pagina_personales()
    if not personales_id:
        return None

    objetivo = normalizar_nombre(titulo_db)
    for bloque in _iter_blocks(personales_id):
        if bloque.get("type") == "child_database" and normalizar_nombre(_titulo_child_database(bloque)) == objetivo:
            try:
                db_id = _data_source_id(notion.databases.retrieve(database_id=bloque["id"]))
            except Exception:
                db_id = bloque["id"]
            with lock:
                cache["db_id"] = db_id
            return db_id

    try:
        parent_personales = {"type": "page_id", "page_id": personales_id}
        if _usa_data_sources():
            nueva = notion.databases.create(
                parent=parent_personales,
                title=[{"type": "text", "text": {"content": titulo_db}}],
                initial_data_source={"title": [{"type": "text", "text": {"content": titulo_db}}], "properties": props},
            )
            nueva = notion.databases.retrieve(database_id=nueva["id"])
        else:
            nueva = notion.databases.create(
                parent=parent_personales,
                title=[{"type": "text", "text": {"content": titulo_db}}],
                properties=props,
            )
        db_id = _data_source_id(nueva)
        with lock:
            cache["db_id"] = db_id
        logging.info("BD '%s' creada bajo 'Evaluaciones Personales'", titulo_db)
        if poblar:
            poblar(db_id)
        return db_id
    except Exception:
        logging.exception("Error creando BD '%s' para evaluaciones personales", titulo_db)
        return None


def _poblar_bbdd_preguntas_personal(db_id: str) -> None:
    for clave, texto in PREGUNTAS_PERSONALES_DEFAULT.items():
        try:
            _crear_pagina_en_bbdd(db_id, {
                "Clave": {"title": [{"type": "text", "text": {"content": clave}}]},
                "Texto": {"rich_text": [{"type": "text", "text": {"content": texto}}]},
            })
        except Exception:
            logging.exception("Error poblando pregunta personal '%s'", clave)


def obtener_preguntas_personales() -> dict:
    import time as _time
    ahora = _time.time()
    with lock:
        cached = _cache_personal_preguntas.get("data")
        ts = _cache_personal_preguntas.get("ts", 0.0)
    if cached and (ahora - ts) < _PERSONAL_PREGUNTAS_TTL:
        return cached

    db_id = _buscar_o_crear_bbdd_en_personales(
        "Preguntas", _PROPS_PERSONAL_PREGUNTAS, _cache_personal_preguntas_db,
        poblar=_poblar_bbdd_preguntas_personal,
    )
    if not db_id:
        return dict(PREGUNTAS_PERSONALES_DEFAULT)

    _migrar_mensaje_inicial(db_id)

    try:
        resultado = {}
        cursor = None
        while True:
            kwargs: dict = {"page_size": 100}
            if cursor:
                kwargs["start_cursor"] = cursor
            resp = _query_bbdd(db_id, **kwargs)
            for fila in resp.get("results", []):
                props = fila.get("properties", {})
                clave = " ".join(p.get("plain_text", "") for p in props.get("Clave", {}).get("title", [])).strip()
                texto = " ".join(p.get("plain_text", "") for p in props.get("Texto", {}).get("rich_text", [])).strip()
                if clave and texto:
                    resultado[clave] = texto
            if not resp.get("has_more"):
                break
            cursor = resp.get("next_cursor")
        for k, v in PREGUNTAS_PERSONALES_DEFAULT.items():
            resultado.setdefault(k, v)
        with lock:
            _cache_personal_preguntas["data"] = resultado
            _cache_personal_preguntas["ts"] = _time.time()
        return resultado
    except Exception:
        logging.exception("Error leyendo preguntas personales desde Notion")
        return dict(PREGUNTAS_PERSONALES_DEFAULT)


def guardar_evaluacion_personal(nombre: str, respuestas: dict) -> bool:
    db_id = _buscar_o_crear_bbdd_en_personales(
        "Respuestas", _PROPS_EVALUACIONES_PERSONALES, _cache_personal_eval_db,
    )
    if not db_id:
        return False
    try:
        from datetime import datetime, timezone
        fecha_iso = datetime.now(timezone.utc).isoformat()
        props = {
            "Nombre": {"title": [{"type": "text", "text": {"content": nombre or ""}}]},
            "Fecha": {"date": {"start": fecha_iso}},
            "Comentario": {"rich_text": [{"type": "text", "text": {"content": respuestas.get("comentario", "") or ""}}]},
        }
        _crear_pagina_en_bbdd(db_id, props)
        logging.info("Evaluación personal guardada para '%s'", nombre)
        return True
    except Exception:
        logging.exception("Error guardando evaluación personal de '%s'", nombre)
        return False


_cache_calendario_db: dict = {"db_id": None}

_PROPS_CALENDARIO = {
    "Nombre": {"title": {}},
    "Fecha inicio": {"date": {}},
}


def _obtener_o_crear_bbdd_calendario() -> str | None:
    with lock:
        db_id = _cache_calendario_db["db_id"]
    if db_id:
        return db_id

    parent = None
    try:
        res = notion.search(
            query="Listas de datos",
            filter={"value": "page", "property": "object"},
            page_size=10,
        )
        for page in res.get("results", []):
            if normalizar_nombre(_extraer_titulo_pagina(page)) == "listas de datos":
                parent = {"type": "page_id", "page_id": page["id"]}
                break
    except Exception:
        pass
    if parent is None:
        parent = _parent_bbdd_referencia()

    # Buscar si ya existe dentro de la página
    try:
        if parent and parent.get("type") == "page_id":
            for bloque in _iter_blocks(parent["page_id"]):
                if bloque.get("type") == "child_database":
                    if normalizar_nombre(_titulo_child_database(bloque)) == "calendario evaluaciones":
                        db_id = bloque["id"]
                        with lock:
                            _cache_calendario_db["db_id"] = db_id
                        return db_id
    except Exception:
        pass

    try:
        if _usa_data_sources():
            nueva = notion.databases.create(
                parent=parent,
                title=[{"type": "text", "text": {"content": "Calendario evaluaciones"}}],
                initial_data_source={
                    "title": [{"type": "text", "text": {"content": "Calendario evaluaciones"}}],
                    "properties": _PROPS_CALENDARIO,
                },
            )
            nueva = notion.databases.retrieve(database_id=nueva["id"])
        else:
            nueva = notion.databases.create(
                parent=parent,
                title=[{"type": "text", "text": {"content": "Calendario evaluaciones"}}],
                properties=_PROPS_CALENDARIO,
            )
        db_id = _data_source_id(nueva)
        with lock:
            _cache_calendario_db["db_id"] = db_id
        return db_id
    except Exception:
        logging.exception("Error creando 'Calendario evaluaciones' en Notion")
        return None


def obtener_config_calendario() -> dict:
    """Devuelve {'personal': 'YYYY-MM-DD'|None, 'proyecto_ca': 'YYYY-MM-DD'|None}."""
    db_id = _obtener_o_crear_bbdd_calendario()
    resultado = {"personal": None, "proyecto_ca": None}
    if not db_id:
        return resultado
    try:
        resp = _query_bbdd(db_id, page_size=50)
        for fila in resp.get("results", []):
            props = fila.get("properties", {})
            nombre = "".join(p.get("plain_text", "") for p in props.get("Nombre", {}).get("title", [])).strip().lower()
            fecha_prop = props.get("Fecha inicio", {}).get("date") or {}
            fecha = (fecha_prop.get("start") or "")[:10]
            if not fecha:
                continue
            if "inicio" in nombre:
                resultado["personal"] = fecha
                resultado["proyecto_ca"] = fecha
            elif "personal" in nombre:
                resultado["personal"] = fecha
            elif "proyecto" in nombre or " ca" in nombre or nombre.startswith("ca"):
                resultado["proyecto_ca"] = fecha
    except Exception:
        logging.exception("Error leyendo configuración de calendario desde Notion")
    return resultado


def siguiente_envio_calendario(fecha_inicio_str: str, semanas: int) -> "datetime":
    """Dado un inicio y un intervalo en semanas, devuelve el próximo momento de envío tras ahora."""
    inicio = datetime.fromisoformat(fecha_inicio_str)
    if inicio.tzinfo is None:
        inicio = inicio.replace(tzinfo=timezone.utc)
    ahora = datetime.now(timezone.utc)
    if ahora < inicio:
        return inicio
    intervalo = timedelta(weeks=semanas)
    n = int((ahora - inicio) / intervalo) + 1
    return inicio + intervalo * n


def obtener_comentarios_personales(nombre: str) -> list[dict]:
    """Devuelve los comentarios de evaluaciones personales escritos por 'nombre'."""
    db_id = _buscar_o_crear_bbdd_en_personales(
        "Respuestas", _PROPS_EVALUACIONES_PERSONALES, _cache_personal_eval_db,
    )
    if not db_id:
        return []
    try:
        nombre_norm = normalizar_nombre(nombre)
        resultados = []
        cursor = None
        while True:
            kwargs = {"page_size": 100}
            if cursor:
                kwargs["start_cursor"] = cursor
            resp = _query_bbdd(db_id, **kwargs)
            for fila in resp.get("results", []):
                props = fila.get("properties", {})
                autor = "".join(p.get("plain_text", "") for p in props.get("Nombre", {}).get("title", [])).strip()
                comentario = "".join(p.get("plain_text", "") for p in props.get("Comentario", {}).get("rich_text", [])).strip()
                fecha_prop = props.get("Fecha", {}).get("date") or {}
                fecha = (fecha_prop.get("start") or "")[:10]
                if normalizar_nombre(autor) == nombre_norm and comentario:
                    resultados.append({"autor": autor, "fecha": fecha, "comentario": comentario})
            if not resp.get("has_more"):
                break
            cursor = resp.get("next_cursor")
        return resultados
    except Exception:
        logging.exception("Error leyendo comentarios personales de '%s'", nombre)
        return []


def evaluacion_proyecto_guardada_desde(evaluador_nombre: str, desde_ts: float) -> bool:
    """True si el evaluador guardó al menos una evaluación de proyecto desde el timestamp dado."""
    desde_fecha = datetime.fromtimestamp(desde_ts, tz=timezone.utc).strftime("%Y-%m-%d")
    try:
        for bbdd in listar_bbdd_evaluados():
            resultado = _query_bbdd(bbdd["id"], page_size=100)
            for pagina in resultado.get("results", []):
                props = pagina.get("properties", {})
                evaluador = _texto_rich_text(props, "Evaluador") or _texto_rich_text(props, "Persona que evalua")
                if normalizar_nombre(evaluador) != normalizar_nombre(evaluador_nombre):
                    continue
                fecha = (props.get("Fecha", {}).get("date") or {}).get("start", "")[:10]
                if fecha >= desde_fecha:
                    return True
        return False
    except Exception:
        logging.exception("Error comprobando evaluaciones de proyecto de '%s'", evaluador_nombre)
        return False


def evaluacion_personal_guardada_desde(nombre: str, desde_ts: float) -> bool:
    """True si el usuario guardó al menos un comentario personal desde el timestamp dado."""
    desde_fecha = datetime.fromtimestamp(desde_ts, tz=timezone.utc).strftime("%Y-%m-%d")
    try:
        comentarios = obtener_comentarios_personales(nombre)
        return any(c.get("fecha", "") >= desde_fecha for c in comentarios)
    except Exception:
        logging.exception("Error comprobando evaluación personal de '%s'", nombre)
        return False


# ---------------------------------------------------------------------------
# MiddleOffice: Cargos y Relaciones
# ---------------------------------------------------------------------------

_PROPS_CARGOS_MO = {
    "Nombre": {"title": {}},
    "Cargo":  {"rich_text": {}},
}

_PROPS_RELACIONES_MO = {
    "Evaluador": {"title": {}},
    "Evaluado":  {"rich_text": {}},
}

_CARGOS_MO_DEFAULT = [
    ("Arancha Gomez-Arnau", "Head of Admin"),
    ("Iñigo Narvaiza",      "Head of Finance"),
    ("Alicia Sardina",      "Head of People Mexico"),
    ("Natalia Vega",        "Head of Communication"),
    ("Reyes Palomar",       "Head of Design"),
    ("Ana Hernanz",         "Head of People Madrid"),
    ("javireneclaude",     "Head of Test"),
]

_RELACIONES_MO_DEFAULT = [
    ("Iñigo Narvaiza",       "Arancha Gomez-Arnau"),
    ("Alicia Sardina",       "Arancha Gomez-Arnau"),
    ("Natalia Vega",         "Arancha Gomez-Arnau"),
    ("Ana Hernanz",          "Iñigo Narvaiza"),
    ("Natalia Vega",         "Alicia Sardina"),
    ("Arancha Gomez-Arnau",  "Alicia Sardina"),
    ("Arancha Gomez-Arnau",  "Natalia Vega"),
    ("Natalia Vega",         "Reyes Palomar"),
    ("Alicia Sardina",       "Reyes Palomar"),
    # Head of Test (usuario de prueba): puede evaluar a todos los Heads
    ("javireneclaude",      "Arancha Gomez-Arnau"),
    ("javireneclaude",      "Iñigo Narvaiza"),
    ("javireneclaude",      "Alicia Sardina"),
    ("javireneclaude",      "Natalia Vega"),
    ("javireneclaude",      "Reyes Palomar"),
    ("javireneclaude",      "Ana Hernanz"),
]

_cache_gestion_mo_page: dict = {"page_id": None}
_cache_cargos_mo: dict = {"db_id": None}
_cache_relaciones_mo: dict = {"db_id": None}

_TITULO_GESTION_MO = "Gestión de MiddleOffice"
_TITULO_CARGOS_MO = "Cargos de MiddleOffice"
_TITULO_RELACIONES_MO = "Relaciones de evaluaciones MiddleOffice"


def _obtener_o_crear_pagina_gestion_mo() -> dict:
    """Encuentra o crea la página 'Gestión de MiddleOffice' dentro de 'Listas de datos'."""
    with lock:
        if _cache_gestion_mo_page["page_id"]:
            return {"type": "page_id", "page_id": _cache_gestion_mo_page["page_id"]}
    listas_parent = _parent_bbdd_en_pagina(config.NOTION_DATA_LISTS_PAGE_NAME, crear=True)
    listas_id = listas_parent.get("page_id")
    if listas_id:
        page_id = _page_or_database_link_by_name(listas_id, _TITULO_GESTION_MO)
        if page_id:
            with lock:
                _cache_gestion_mo_page["page_id"] = page_id
            return {"type": "page_id", "page_id": page_id}
    try:
        nueva = notion.pages.create(
            parent=listas_parent,
            properties={"title": {"title": [{"type": "text", "text": {"content": _TITULO_GESTION_MO}}]}},
        )
        page_id = nueva["id"]
        with lock:
            _cache_gestion_mo_page["page_id"] = page_id
        logging.info("Página '%s' creada bajo '%s'", _TITULO_GESTION_MO, config.NOTION_DATA_LISTS_PAGE_NAME)
        return {"type": "page_id", "page_id": page_id}
    except Exception:
        logging.exception("Error creando página '%s'", _TITULO_GESTION_MO)
        return listas_parent


def _obtener_o_crear_bbdd_mo(titulo: str, props: dict, cache: dict, filas_default: list) -> str | None:
    with lock:
        if cache["db_id"]:
            return cache["db_id"]
    parent = _obtener_o_crear_pagina_gestion_mo()
    try:
        res = notion.search(
            query=titulo,
            filter={"value": _tipo_objeto_busqueda_bbdd(), "property": "object"},
            page_size=50,
        )
        for bbdd in res.get("results", []):
            if _extraer_titulo_bbdd(bbdd) == titulo and _coincide_parent_bbdd(bbdd, parent):
                db_id = _data_source_id(bbdd)
                with lock:
                    cache["db_id"] = db_id
                return db_id
        if _usa_data_sources():
            nueva = notion.databases.create(
                parent=parent,
                title=[{"type": "text", "text": {"content": titulo}}],
                initial_data_source={
                    "title": [{"type": "text", "text": {"content": titulo}}],
                    "properties": props,
                },
            )
            nueva = notion.databases.retrieve(database_id=nueva["id"])
        else:
            nueva = notion.databases.create(
                parent=parent,
                title=[{"type": "text", "text": {"content": titulo}}],
                properties=props,
            )
        db_id = _data_source_id(nueva)
        col_titulo = next(k for k, v in props.items() if "title" in v)
        col_texto = next(k for k, v in props.items() if "rich_text" in v)
        for val_titulo, val_texto in filas_default:
            _crear_pagina_en_bbdd(db_id, {
                col_titulo: {"title":     [{"type": "text", "text": {"content": val_titulo}}]},
                col_texto:  {"rich_text": [{"type": "text", "text": {"content": val_texto}}]},
            })
        with lock:
            cache["db_id"] = db_id
        logging.info("BD '%s' creada bajo '%s'", titulo, _TITULO_GESTION_MO)
        return db_id
    except Exception:
        logging.exception("Error creando '%s'", titulo)
        return None


def inicializar_bbdd_middleoffice() -> None:
    """Crea en Notion las BDs de MiddleOffice al arrancar si aún no existen."""
    _obtener_o_crear_bbdd_mo(_TITULO_CARGOS_MO, _PROPS_CARGOS_MO, _cache_cargos_mo, _CARGOS_MO_DEFAULT)
    _obtener_o_crear_bbdd_mo(_TITULO_RELACIONES_MO, _PROPS_RELACIONES_MO, _cache_relaciones_mo, _RELACIONES_MO_DEFAULT)


def obtener_evaluados_middleoffice(evaluador_nombre: str, evaluador_aliases: list[str] | None = None) -> list[str]:
    """Retorna la lista de personas que este evaluador puede evaluar en MiddleOffice."""
    db_id = _obtener_o_crear_bbdd_mo(
        _TITULO_RELACIONES_MO, _PROPS_RELACIONES_MO, _cache_relaciones_mo, _RELACIONES_MO_DEFAULT
    )
    if not db_id:
        return []
    nombres_ev = {normalizar_nombre(evaluador_nombre)}
    for alias in (evaluador_aliases or []):
        if alias:
            nombres_ev.add(normalizar_nombre(alias))
    nombres_ev_sin_espacios = {n.replace(" ", "") for n in nombres_ev}
    try:
        filas = _query_bbdd(db_id, page_size=100).get("results", [])
        evaluados = []
        for fila in filas:
            props = fila.get("properties", {})
            ev = "".join(p.get("plain_text", "") for p in props.get("Evaluador", {}).get("title", [])).strip()
            evaluado = "".join(p.get("plain_text", "") for p in props.get("Evaluado", {}).get("rich_text", [])).strip()
            ev_norm = normalizar_nombre(ev)
            if (ev_norm in nombres_ev or ev_norm.replace(" ", "") in nombres_ev_sin_espacios) and evaluado:
                evaluados.append(evaluado)
        return evaluados
    except Exception:
        logging.exception("Error obteniendo evaluados MiddleOffice para '%s'", evaluador_nombre)
        return []
