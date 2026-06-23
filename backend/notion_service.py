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
        "Satisfaccion de superiores": {"rich_text": {}},
        "Satisfaccion de iguales": {"rich_text": {}},
        "Satisfaccion de inferiores": {"rich_text": {}},
        "Mejor aspecto de superiores": {"rich_text": {}},
        "Mejor aspecto de iguales": {"rich_text": {}},
        "Mejor aspecto de inferiores": {"rich_text": {}},
        "Peor aspecto de superiores": {"rich_text": {}},
        "Peor aspecto de iguales": {"rich_text": {}},
        "Peor aspecto de inferiores": {"rich_text": {}},
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
            {"name": "satisfaccion"},
            {"name": "mejor_aspecto"},
            {"name": "peor_aspecto"},
        ]}},
    }


def _obtener_o_crear_bbdd_preguntas():
    bbdd_id = _buscar_bbdd_en_pagina(config.NOTION_DATA_LISTS_PAGE_NAME, NOTION_QUESTIONS_DATABASE_NAME)
    if bbdd_id:
        return bbdd_id
    parent = _parent_bbdd_en_pagina(config.NOTION_DATA_LISTS_PAGE_NAME, crear=True)
    if parent.get("type") != "page_id":
        logging.warning("No se pudo localizar la página '%s'", config.NOTION_DATA_LISTS_PAGE_NAME)
        return None
    titulo = NOTION_QUESTIONS_DATABASE_NAME
    props = _propiedades_bbdd_preguntas()
    try:
        if _usa_data_sources():
            nueva = notion.databases.create(
                parent={"type": "page_id", "page_id": parent["page_id"]},
                title=[{"type": "text", "text": {"content": titulo}}],
                initial_data_source={"title": [{"type": "text", "text": {"content": titulo}}], "properties": props},
            )
            nueva = notion.databases.retrieve(database_id=nueva["id"])
        else:
            nueva = notion.databases.create(
                parent={"type": "page_id", "page_id": parent["page_id"]},
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


_PREGUNTAS_INICIALES = [
    ("Top-Bottom", "satisfaccion", "¿Cómo de satisfecho estás con el desempeño de esta persona? (responde un número del 1 al 5)"),
    ("Top-Bottom", "mejor_aspecto", "¿Cuál es el mejor aspecto de esta persona en su rol?"),
    ("Top-Bottom", "peor_aspecto", "¿Cuál es el principal aspecto a mejorar de esta persona?"),
    ("Bottom-Top", "satisfaccion", "¿Cómo de satisfecho estás con esta persona como líder? (responde un número del 1 al 5)"),
    ("Bottom-Top", "mejor_aspecto", "¿Cuál es el mejor aspecto de esta persona como líder?"),
    ("Bottom-Top", "peor_aspecto", "¿Cuál es el principal aspecto a mejorar de esta persona como líder?"),
    ("Same Level", "satisfaccion", "¿Cómo de satisfecho estás trabajando con esta persona? (responde un número del 1 al 5)"),
    ("Same Level", "mejor_aspecto", "¿Cuál es el mejor aspecto de esta persona como compañero?"),
    ("Same Level", "peor_aspecto", "¿Cuál es el principal aspecto a mejorar de esta persona como compañero?"),
]


def _poblar_bbdd_preguntas(bbdd_id):
    for tipo, clave, texto in _PREGUNTAS_INICIALES:
        try:
            _crear_pagina_en_bbdd(bbdd_id, {
                "Texto": {"title": [{"text": {"content": texto}}]},
                "Tipo": {"select": {"name": tipo}},
                "Clave": {"select": {"name": clave}},
            })
        except Exception:
            logging.exception("Error creando fila '%s'/'%s' en BD Preguntas", tipo, clave)


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


def guardar_en_notion(nombre, respuestas, relacion="igual"):
    nombre_evaluado = respuestas.get("evaluado", "").strip()
    proyecto = respuestas.get("proyecto", "").strip()
    try:
        database_id = obtener_o_crear_bbdd_evaluado(nombre_evaluado)
        asegurar_propiedades_bbdd(database_id)
        fecha = datetime.now(timezone.utc)
        satisfaccion = respuestas.get("satisfaccion", "")
        mejor = respuestas.get("mejor_aspecto", "")
        peor = respuestas.get("peor_aspecto", "")
        suf_col = {"superior": "de superiores", "inferior": "de inferiores"}.get(relacion, "de iguales")
        _crear_pagina_en_bbdd(
            database_id,
            {
                "Name": {"title": [{"text": {"content": f"Evaluacion {fecha.strftime('%Y-%m-%d %H:%M')}"}}]},
                "Evaluador": {"rich_text": [{"text": {"content": nombre}}]},
                "Proyecto": {"rich_text": [{"text": {"content": proyecto}}]},
                "Fecha": {"date": {"start": fecha.isoformat()}},
                f"Satisfaccion {suf_col}": {"rich_text": [{"text": {"content": satisfaccion}}]},
                f"Mejor aspecto {suf_col}": {"rich_text": [{"text": {"content": mejor}}]},
                f"Peor aspecto {suf_col}": {"rich_text": [{"text": {"content": peor}}]},
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

                registros.append({"nombre": nombre, "email": email.strip(), "aliases": aliases, "cargo": cargo, "id_usuario": id_usuario, "foto": foto})
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


_cache_preguntas: dict = {}


def _normalizar_clave_pregunta(clave: str) -> str | None:
    norm = normalizar_nombre(clave)
    if "satisfac" in norm:
        return "satisfaccion"
    if "mejor" in norm:
        return "mejor_aspecto"
    if "peor" in norm:
        return "peor_aspecto"
    return None


def obtener_preguntas_desde_notion(tipo: str) -> dict:
    """Devuelve las preguntas del tipo dado ('Same Level', 'Top-Bottom', 'Bottom-Top').
    Resultado: {'satisfaccion': '...', 'mejor_aspecto': '...', 'peor_aspecto': '...'}
    """
    with lock:
        if tipo in _cache_preguntas:
            return _cache_preguntas[tipo]
    try:
        resultado = notion.search(
            query=config.NOTION_QUESTIONS_DATABASE_NAME,
            filter={"value": _tipo_objeto_busqueda_bbdd(), "property": "object"},
            page_size=10,
        )
        db_id = None
        for bbdd in resultado.get("results", []):
            if normalizar_nombre(_extraer_titulo_bbdd(bbdd)) == normalizar_nombre(config.NOTION_QUESTIONS_DATABASE_NAME):
                db_id = _data_source_id(bbdd)
                break
        if not db_id:
            logging.warning("No se encontró la base de preguntas '%s'", config.NOTION_QUESTIONS_DATABASE_NAME)
            return {}

        preguntas = {}
        tipo_norm = normalizar_nombre(tipo)
        cursor = None
        while True:
            kwargs: dict = {"page_size": 100}
            if cursor:
                kwargs["start_cursor"] = cursor
            resp = _query_bbdd(db_id, **kwargs)
            for fila in resp.get("results", []):
                props = fila.get("properties", {})
                if normalizar_nombre(_texto_propiedad(props, "Tipo")) != tipo_norm:
                    continue
                texto = _texto_propiedad(props, "Pregunta")
                clave = _normalizar_clave_pregunta(_texto_propiedad(props, "Clave"))
                if texto and clave:
                    preguntas[clave] = texto
            if not resp.get("has_more"):
                break
            cursor = resp.get("next_cursor")

        with lock:
            _cache_preguntas[tipo] = preguntas
        return preguntas
    except Exception:
        logging.exception("Error leyendo preguntas desde Notion para tipo '%s'", tipo)
        return {}


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
                sat_sup  = _texto_rich_text(props, "Satisfaccion de superiores")
                sat_igu  = _texto_rich_text(props, "Satisfaccion de iguales")
                sat_inf  = _texto_rich_text(props, "Satisfaccion de inferiores")
                mej_sup  = _texto_rich_text(props, "Mejor aspecto de superiores")
                mej_igu  = _texto_rich_text(props, "Mejor aspecto de iguales")
                mej_inf  = _texto_rich_text(props, "Mejor aspecto de inferiores")
                peor_sup = _texto_rich_text(props, "Peor aspecto de superiores")
                peor_igu = _texto_rich_text(props, "Peor aspecto de iguales")
                peor_inf = _texto_rich_text(props, "Peor aspecto de inferiores")
                if sat_sup or mej_sup or peor_sup:
                    relacion = "superior"
                    sat_act, mej_act, peor_act = sat_sup, mej_sup, peor_sup
                elif sat_inf or mej_inf or peor_inf:
                    relacion = "inferior"
                    sat_act, mej_act, peor_act = sat_inf, mej_inf, peor_inf
                elif sat_igu or mej_igu or peor_igu:
                    relacion = "igual"
                    sat_act, mej_act, peor_act = sat_igu, mej_igu, peor_igu
                else:
                    relacion = ""
                    sat_act  = _texto_rich_text(props, "Satisfaccion")
                    mej_act  = _texto_rich_text(props, "Mejor aspecto")
                    peor_act = _texto_rich_text(props, "Peor aspecto")
                evaluaciones.append({
                    "nombre": evaluador,
                    "evaluado": evaluado,
                    "persona_evaluada": evaluado,
                    "persona_que_evalua": evaluador,
                    "proyecto": _texto_rich_text(props, "Proyecto"),
                    "satisfaccion": sat_act,
                    "mejor_aspecto": mej_act,
                    "peor_aspecto": peor_act,
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
    """Devuelve {docx, html} del informe final más reciente del advisee, o None."""
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
    "Personas implicadas": {"rich_text": {}},
    "Proyecto": {"rich_text": {}},
    "Comentario": {"rich_text": {}},
}

_PROPS_PERSONAL_PREGUNTAS = {
    "Clave": {"title": {}},
    "Texto": {"rich_text": {}},
}

PREGUNTAS_PERSONALES_DEFAULT = {
    "mensaje_inicial": (
        "📝 *Evaluación personal*\n"
        "Es tu oportunidad para hacer cualquier comentario sobre cualquier avance o impedimento "
        "con tus objetivos marcados con tu CA, o cualquier comentario sobre el proyecto. "
        "Este mensaje es totalmente privado, por favor deja tu comentario respondiendo en el hilo.\n\n"
        "_Si quieres cancelar la encuesta, escribe SOS en cualquier momento._"
    ),
    "proyecto": "1️⃣ ¿Sobre qué proyecto quieres comentar? Si no está asociado a ningún proyecto, escribe *ninguno*.",
    "personas": "2️⃣ ¿Hay alguna persona implicada que quieras mencionar? Escribe su nombre o *ninguna* si no hay nadie.",
    "comentario": "3️⃣ Por favor, escribe tu comentario o lo que quieras compartir.",
}


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
            "Personas implicadas": {"rich_text": [{"type": "text", "text": {"content": respuestas.get("personas", "") or ""}}]},
            "Proyecto": {"rich_text": [{"type": "text", "text": {"content": respuestas.get("proyecto", "") or ""}}]},
            "Comentario": {"rich_text": [{"type": "text", "text": {"content": respuestas.get("comentario", "") or ""}}]},
        }
        _crear_pagina_en_bbdd(db_id, props)
        logging.info("Evaluación personal guardada para '%s'", nombre)
        return True
    except Exception:
        logging.exception("Error guardando evaluación personal de '%s'", nombre)
        return False
