import logging
import os
import re
import threading
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher

from . import config
from .clients import notion
from .i18n import IDIOMAS_SOPORTADOS
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


def _safe_link_by_name(page_id, nombre):
    """Como _page_or_database_link_by_name pero un 404/sin-compartir devuelve None
    en vez de propagar, para poder caer al buscador global por nombre."""
    try:
        return _page_or_database_link_by_name(page_id, nombre)
    except Exception:
        logging.warning(
            "No se pudo leer la pagina de Notion '%s' (¿movida o sin compartir con la integracion?). "
            "Se intentara localizar '%s' por busqueda global.",
            page_id, nombre,
        )
        return None


def _tabla_directa_o_none(id_):
    """Si `id_` ya es una tabla de Notion (data_source o database), devuelve su
    data_source_id para usarla directamente. Si es una pagina o no es accesible,
    devuelve None para que el llamador navegue/busque. Silencia el warning de
    notion_client durante el sondeo para no ensuciar el log en el caso pagina."""
    logger_nc = logging.getLogger("notion_client")
    nivel_previo = logger_nc.level
    logger_nc.setLevel(logging.ERROR)
    try:
        if _usa_data_sources():
            try:
                notion.data_sources.retrieve(data_source_id=id_)
                return id_
            except Exception:
                try:
                    db = notion.databases.retrieve(database_id=id_)
                    return _data_source_id(db)
                except Exception:
                    return None
        try:
            notion.databases.retrieve(database_id=id_)
            return id_
        except Exception:
            return None
    finally:
        logger_nc.setLevel(nivel_previo)


def _resolver_ruta_lista_empleados(origen_id):
    origen_id = _normalizar_notion_id(origen_id)

    # Paso 0: si el ID ya apunta directamente a la tabla, usarla sin navegar como
    # pagina. Asi NOTION_EMPLOYEES_DATABASE_ID puede ser el ID exacto de la
    # "Lista de empleados"; la navegacion / busqueda global queda solo de fallback.
    tabla_directa = _tabla_directa_o_none(origen_id)
    if tabla_directa:
        logging.info("Lista de empleados usada por ID directo (sin navegar): %s", tabla_directa)
        return tabla_directa

    pagina_listas_id = _safe_link_by_name(origen_id, config.NOTION_DATA_LISTS_PAGE_NAME)
    if pagina_listas_id:
        logging.info("Pagina de listas de datos encontrada: %s", config.NOTION_DATA_LISTS_PAGE_NAME)
        _decorar_pagina_notion(pagina_listas_id, config.NOTION_DATA_LISTS_PAGE_NAME)
        lista_empleados_id = _safe_link_by_name(pagina_listas_id, config.NOTION_EMPLOYEES_DATABASE_NAME)
        if lista_empleados_id:
            logging.info("Link a lista de empleados encontrado: %s", config.NOTION_EMPLOYEES_DATABASE_NAME)
            return lista_empleados_id
        return pagina_listas_id

    lista_empleados_id = _safe_link_by_name(origen_id, config.NOTION_EMPLOYEES_DATABASE_NAME)
    if lista_empleados_id:
        logging.info("Link a lista de empleados encontrado: %s", config.NOTION_EMPLOYEES_DATABASE_NAME)
        return lista_empleados_id

    lista_empleados_id = _buscar_objeto_notion_por_nombre(config.NOTION_EMPLOYEES_DATABASE_NAME)
    if lista_empleados_id:
        logging.info("Lista de empleados localizada por busqueda global: %s", config.NOTION_EMPLOYEES_DATABASE_NAME)
        return lista_empleados_id

    raise RuntimeError(
        f"No se encontro '{config.NOTION_EMPLOYEES_DATABASE_NAME}' desde el origen configurado. "
        "Comparte la pagina/base con la integracion o configura NOTION_EMPLOYEES_DATABASE_ID "
        "con la URL o ID exacto de la lista de empleados."
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
    if not config.NOTION_DATABASE_ID:
        raise RuntimeError("Configura NOTION_PARENT_PAGE_ID con la página donde crear las bases nuevas.")
    bbdd_referencia = notion.databases.retrieve(database_id=config.NOTION_DATABASE_ID)
    parent = bbdd_referencia.get("parent", {})
    if parent.get("type") != "page_id":
        raise RuntimeError("Configura NOTION_PARENT_PAGE_ID con la página donde crear las bases nuevas.")
    return {"type": "page_id", "page_id": parent["page_id"]}


_NOTION_PAGE_STYLE = {
    config.NOTION_DATA_LISTS_PAGE_NAME: {
        "emoji": "🗂️",
        "color": "green",
        "title": "Datos a Monitorizar",
        "body": "Listas maestras que se mantienen a mano: empleados, usuarios, CA y datos de soporte.",
    },
    config.NOTION_DATA_MODIFICABLES_PAGE_NAME: {
        "emoji": "⚙️",
        "color": "gray",
        "title": "Datos opcionalmente modificables",
        "body": "Configuración personalizable del chatbot: preguntas, criterios de evaluación y ejemplos de guía.",
    },
    config.NOTION_PREGUNTAS_CHATBOT_PAGE_NAME: {
        "emoji": "💬",
        "color": "blue",
        "title": "Preguntas del chatbot",
        "body": "Banco de preguntas que el bot envía en cada evaluación mensual, organizado por área.",
    },
    config.NOTION_RESULTADOS_EVAL_PAGE_NAME: {
        "emoji": "📈",
        "color": "orange",
        "title": "Resultados de evaluaciones",
        "body": "Espacio generado automáticamente. Aquí se acumulan todos los resultados y logs del chatbot.",
    },
    config.NOTION_ACTIVACIONES_PERMISOS_PAGE_NAME: {
        "emoji": "🔐",
        "color": "red",
        "title": "Control de permisos",
        "body": "Activa o desactiva funcionalidades del bot por empleado o equipo.",
    },
    config.NOTION_INDIVIDUAL_EVALUATIONS_PAGE_NAME: {
        "emoji": "📊",
        "color": "blue",
        "title": "Evaluaciones mensuales individuales",
        "body": "Espacio generado por el bot. Cada tabla recoge el feedback recibido sobre una persona evaluada.",
    },
    config.NOTION_CA_TRACKING_PAGE_NAME: {
        "emoji": "🗣️",
        "color": "purple",
        "title": "Seguimiento Career Advisor",
        "body": "Opiniones y revisiones de CA generadas desde Slack. Pensado para consulta, no para edición manual.",
    },
}

_NOTION_DATABASE_STYLE: dict[str, dict] = {
    "Lista de empleados": {
        "emoji": "👥",
        "description": "Directorio principal. Define quién puede ser evaluado, su cargo y sus alias de Slack.",
    },
    "Usuarios Web": {
        "emoji": "🌐",
        "description": "Cuentas con acceso al portal web del asistente de evaluación.",
    },
    "Evaluaciones anuales": {
        "emoji": "🏆",
        "description": "Informes anuales generados en la sesión asistida entre el CA y el bot.",
    },
    "Preguntas Negocio": {
        "emoji": "❓",
        "description": "Preguntas enviadas en evaluaciones mensuales para equipos de Negocio.",
    },
    "Preguntas MiddleOffice": {
        "emoji": "❓",
        "description": "Preguntas enviadas en evaluaciones mensuales para MiddleOffice.",
    },
    "Preguntas Palantir": {
        "emoji": "❓",
        "description": "Preguntas enviadas en evaluaciones mensuales para Palantir.",
    },
    "Log evaluacion anual asistida": {
        "emoji": "📋",
        "description": "Auditoría: decisiones del CA frente a la propuesta del bot en cada evaluación anual.",
    },
    "Resultados Barbecho": {
        "emoji": "🌱",
        "description": "Registros de empleados en periodo de barbecho: área y labores que realizan.",
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
    except Exception as exc:
        if getattr(exc, "status", 0) == 404:
            return  # El ID pertenece a una BD, no a una página; se decora por separado
        logging.exception("No se pudo actualizar el icono de la pagina %s", nombre_pagina)
        return

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


def _decorar_bbdd_notion(db_id: str, nombre_bbdd: str) -> None:
    estilo = _NOTION_DATABASE_STYLE.get(nombre_bbdd)
    if not estilo:
        return
    try:
        payload: dict = {
            "icon": {"type": "emoji", "emoji": estilo["emoji"]},
            "description": [{"type": "text", "text": {"content": estilo["description"]}}],
        }
        if _usa_data_sources():
            try:
                notion.data_sources.update(data_source_id=db_id, **payload)
            except Exception:
                notion.databases.update(database_id=db_id, **payload)
        else:
            notion.databases.update(database_id=db_id, **payload)
    except Exception:
        logging.exception("No se pudo decorar la BD de Notion '%s'", nombre_bbdd)


def aplicar_estetica_notion():
    parent_raiz = _parent_bbdd_referencia()
    root_id = parent_raiz["page_id"]

    _PAGINAS_SOLO_SI_EXISTEN = {
        config.NOTION_DATA_LISTS_PAGE_NAME,
        config.NOTION_DATA_MODIFICABLES_PAGE_NAME,
        config.NOTION_PREGUNTAS_CHATBOT_PAGE_NAME,
        config.NOTION_RESULTADOS_EVAL_PAGE_NAME,
        config.NOTION_ACTIVACIONES_PERMISOS_PAGE_NAME,
    }

    for nombre_pagina in (
        config.NOTION_DATA_LISTS_PAGE_NAME,
        config.NOTION_DATA_MODIFICABLES_PAGE_NAME,
        config.NOTION_PREGUNTAS_CHATBOT_PAGE_NAME,
        config.NOTION_RESULTADOS_EVAL_PAGE_NAME,
        config.NOTION_ACTIVACIONES_PERMISOS_PAGE_NAME,
        config.NOTION_INDIVIDUAL_EVALUATIONS_PAGE_NAME,
        config.NOTION_CA_TRACKING_PAGE_NAME,
    ):
        page_id = _buscar_pagina_en_jerarquia(nombre_pagina, root_id)
        if page_id:
            _decorar_pagina_notion(page_id, nombre_pagina)
        elif nombre_pagina not in _PAGINAS_SOLO_SI_EXISTEN:
            _parent_bbdd_en_pagina(nombre_pagina, crear=True)

    for nombre_bbdd in _NOTION_DATABASE_STYLE:
        try:
            resultado = notion.search(
                query=nombre_bbdd,
                filter={"value": _tipo_objeto_busqueda_bbdd(), "property": "object"},
                page_size=10,
            )
            for bbdd in resultado.get("results", []):
                if normalizar_nombre(_extraer_titulo_bbdd(bbdd)) == normalizar_nombre(nombre_bbdd):
                    _decorar_bbdd_notion(_data_source_id(bbdd), nombre_bbdd)
                    break
        except Exception:
            logging.exception("No se pudo decorar la BD '%s'", nombre_bbdd)


def _buscar_pagina_en_jerarquia(nombre_pagina: str, root_id: str) -> str | None:
    """Busca una página por nombre en root y hasta 2 niveles bajo TO-DO/TO-SEE."""
    # Nivel 0: directamente bajo root
    page_id = _page_or_database_link_by_name(root_id, nombre_pagina)
    if page_id:
        return page_id
    # Niveles 1-2: dentro de TO-DO y TO-SEE y sus hijos directos
    for l1_nombre in (config.NOTION_TODO_PAGE_NAME, config.NOTION_TOSEE_PAGE_NAME):
        l1_id = _page_or_database_link_by_name(root_id, l1_nombre)
        if not l1_id:
            continue
        page_id = _page_or_database_link_by_name(l1_id, nombre_pagina)
        if page_id:
            return page_id
        for bloque in _iter_blocks(l1_id):
            if bloque.get("type") not in ("child_page",):
                continue
            l2_id = bloque["id"]
            page_id = _page_or_database_link_by_name(l2_id, nombre_pagina)
            if page_id:
                return page_id
    return None


def _parent_bbdd_en_pagina(nombre_pagina, crear=False):
    parent_raiz = _parent_bbdd_referencia()
    page_id = _buscar_pagina_en_jerarquia(nombre_pagina, parent_raiz["page_id"])
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
_NOMBRE_SUBPAGINA_PREGUNTAS_NUEVO = "Preguntas evaluación mensual"
_cache_pagina_preguntas: dict = {"page_id": None}
_lock_pagina_preguntas = threading.Lock()


def _obtener_o_crear_pagina_preguntas_id() -> str | None:
    """Devuelve el page_id de la sub-página de preguntas de evaluación mensual.

    Busca en nueva estructura (Preguntas Chatbot) y en antigua (Listas de datos).
    """
    with _lock_pagina_preguntas:
        cached = _cache_pagina_preguntas["page_id"]
    if cached:
        return cached

    # Nueva estructura: buscar dentro de "Preguntas Chatbot"
    chatbot_parent = _parent_bbdd_en_pagina(config.NOTION_PREGUNTAS_CHATBOT_PAGE_NAME, crear=False)
    if chatbot_parent.get("type") == "page_id":
        chatbot_id = chatbot_parent["page_id"]
        page_id = _page_or_database_link_by_name(chatbot_id, _NOMBRE_SUBPAGINA_PREGUNTAS_NUEVO)
        if not page_id:
            page_id = _page_or_database_link_by_name(chatbot_id, _NOMBRE_SUBPAGINA_PREGUNTAS)
        if page_id:
            with _lock_pagina_preguntas:
                _cache_pagina_preguntas["page_id"] = page_id
            return page_id

    # Fallback: estructura antigua — buscar "Preguntas" dentro de la página de listas
    for nombre_listas in (config.NOTION_DATA_LISTS_PAGE_NAME, config.NOTION_DATA_MODIFICABLES_PAGE_NAME):
        listas_parent = _parent_bbdd_en_pagina(nombre_listas, crear=False)
        if listas_parent.get("type") != "page_id":
            continue
        listas_page_id = listas_parent["page_id"]
        page_id = _page_or_database_link_by_name(listas_page_id, _NOMBRE_SUBPAGINA_PREGUNTAS)
        if page_id:
            with _lock_pagina_preguntas:
                _cache_pagina_preguntas["page_id"] = page_id
            return page_id

    # Crear bajo "Preguntas Chatbot" si existe, si no bajo la página de listas
    parent_crear = _parent_bbdd_en_pagina(config.NOTION_PREGUNTAS_CHATBOT_PAGE_NAME, crear=False)
    if parent_crear.get("type") != "page_id":
        parent_crear = _parent_bbdd_en_pagina(config.NOTION_DATA_MODIFICABLES_PAGE_NAME, crear=True)
    if parent_crear.get("type") != "page_id":
        parent_crear = _parent_bbdd_en_pagina(config.NOTION_DATA_LISTS_PAGE_NAME, crear=True)
    if parent_crear.get("type") != "page_id":
        return None
    try:
        nombre_crear = _NOMBRE_SUBPAGINA_PREGUNTAS_NUEVO
        nueva = notion.pages.create(
            parent={"type": "page_id", "page_id": parent_crear["page_id"]},
            properties={"title": {"title": [{"type": "text", "text": {"content": nombre_crear}}]}},
        )
        page_id = nueva["id"]
        logging.info("Sub-página '%s' creada", nombre_crear)
    except Exception:
        logging.exception("Error creando sub-página de preguntas")
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
        _decorar_bbdd_notion(bbdd_id, titulo)
        _poblar_bbdd_preguntas(bbdd_id)
        return bbdd_id
    except Exception:
        logging.exception("Error creando BD de Preguntas en Notion")
        return None


_Q4_BOTTOM_TOP = (
    "¿Cómo valorarías del 1 al 4 la contribución del Project Leader al buen avance del proyecto? "
    "(Esta respuesta es totalmente privada y anónima: no la puede ver nadie de la empresa, "
    "ni tu CA ni la persona evaluada, salvo la Head of People, Ana.)"
)
_Q4_TOP_BOTTOM = "¿Cómo valorarías del 1 al 4 la contribución de {nombre} al buen avance del proyecto?"
_Q4_SAME_LEVEL = "¿Cómo valorarías del 1 al 4 la contribución de {nombre} al buen avance del proyecto?"
_Q5_TEXTO = "Indica un ejemplo concreto que justifique tu valoración"
_Q5_BOTTOM_TOP = (
    "Indica un ejemplo concreto que justifique tu valoración. "
    "Recuerda: esta respuesta es totalmente privada y anónima, solo la ve la Head of People (Ana)."
)

_PREGUNTAS_INICIALES = [
    ("Top-Bottom", "q1", _Q4_TOP_BOTTOM),
    ("Top-Bottom", "q2", _Q5_TEXTO),
    ("Bottom-Top", "q1", _Q4_BOTTOM_TOP),
    ("Bottom-Top", "q2", _Q5_BOTTOM_TOP),
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


def obtener_preguntas_desde_notion(tipo: str, idioma: str = "es") -> dict:
    """Devuelve {clave: texto} para el tipo dado (Top-Bottom / Bottom-Top / Same Level).

    Filtra por la columna 'Idioma' (ES/EN) de la base de preguntas. Si se pide 'en'
    y una clave no tiene fila en ingles, cae a la version espanola. Si la columna
    'Idioma' aun no existe, todas las filas se tratan como ES (compatibilidad). Cache 5 min.
    """
    idioma = idioma if idioma in IDIOMAS_SOPORTADOS else "es"
    cache_key = f"{tipo}|{idioma}"
    ahora = time.time()
    with _lock_preguntas:
        if cache_key in _preguntas_cache and (ahora - _preguntas_cache_time.get(cache_key, 0)) < _PREGUNTAS_CACHE_TTL:
            return _preguntas_cache[cache_key]
    try:
        bbdd_id = _obtener_o_crear_bbdd_preguntas()
        if not bbdd_id:
            return {}
        resp = _query_bbdd(bbdd_id, filter={"property": "Tipo", "select": {"equals": tipo}})
        mapas: dict = {}  # idioma -> {clave: titulo}
        for pagina in resp.get("results", []):
            props = pagina.get("properties", {})
            clave = ((props.get("Clave") or {}).get("select") or {}).get("name", "")
            titulo = "".join(t.get("plain_text", "") for t in (props.get("Texto") or {}).get("title", []))
            if not (clave and titulo):
                continue
            _lang = _normalizar_idioma(_texto_propiedad(props, "Idioma"))
            mapas.setdefault(_lang, {})[clave] = titulo
        es_map = mapas.get("es", {})
        base = mapas.get(idioma, {})
        preguntas = {c: (base.get(c) or es_map.get(c)) for c in (set(es_map) | set(base))}
        with _lock_preguntas:
            _preguntas_cache[cache_key] = preguntas
            _preguntas_cache_time[cache_key] = ahora
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
            _decorar_bbdd_notion(db_id, _NOMBRE_BBDD_PREGUNTAS_MO)
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


def obtener_preguntas_mo(idioma: str = "es") -> list[dict]:
    """Devuelve [{clave, texto}] para MiddleOffice en el idioma dado (cacheado 5 min).

    Filtra por la columna 'Idioma' (ES/EN). Si se pide 'en' y una clave no tiene fila EN,
    cae a la versión ES. Sin columna Idioma, todo se trata como ES."""
    idioma = idioma if idioma in IDIOMAS_SOPORTADOS else "es"
    ahora = time.time()
    with _lock_preguntas_mo:
        entry = _cache_preguntas_mo_data.get(idioma)
    if entry and (ahora - entry["ts"]) < _PREGUNTAS_MO_TTL:
        return entry["data"]
    db_id = _obtener_o_crear_bbdd_preguntas_mo()
    if not db_id:
        return [{"clave": c, "texto": t} for c, t in _PREGUNTAS_MO_DEFAULT]
    try:
        mapas: dict = {}  # idioma -> {clave: texto}
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
                if not (clave and texto):
                    continue
                _lang = _normalizar_idioma(_texto_propiedad(props, "Idioma"))
                mapas.setdefault(_lang, {})[clave] = texto
            if not resp.get("has_more"):
                break
            cursor = resp.get("next_cursor")
        es_map = mapas.get("es", {})
        base = mapas.get(idioma, {})
        resultado = []
        for c in _CLAVES_MO_ORDEN:
            texto = base.get(c) or es_map.get(c)  # fallback -> ES
            if texto:
                resultado.append({"clave": c, "texto": texto})
        if not resultado:
            resultado = [{"clave": c, "texto": t} for c, t in _PREGUNTAS_MO_DEFAULT]
        with _lock_preguntas_mo:
            _cache_preguntas_mo_data[idioma] = {"data": resultado, "ts": time.time()}
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
    ("Bottom-Top", "q2", _Q5_BOTTOM_TOP),
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
            _decorar_bbdd_notion(db_id, _NOMBRE_BBDD_PREGUNTAS_PALANTIR)
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


def obtener_preguntas_palantir(tipo: str, idioma: str = "es") -> list[dict]:
    """Devuelve [{clave, texto}] para el tipo de jerarquía dado en Palantir (cacheado 5 min).

    Filtra por la columna 'Idioma' (ES/EN) con fallback EN->ES por clave."""
    idioma = idioma if idioma in IDIOMAS_SOPORTADOS else "es"
    ahora = time.time()
    cache_key = f"{tipo}|{idioma}"
    with _lock_preguntas_palantir:
        cached = _cache_preguntas_palantir_data.get(cache_key)
        ts = _cache_preguntas_palantir_ts.get(cache_key, 0.0)
    if cached is not None and (ahora - ts) < _PREGUNTAS_PALANTIR_TTL:
        return cached
    db_id = _obtener_o_crear_bbdd_preguntas_palantir()
    if not db_id:
        return [{"clave": c, "texto": t} for tp, c, t in _PREGUNTAS_PALANTIR_DEFAULT if tp == tipo]
    try:
        mapas: dict = {}  # idioma -> {clave: texto}
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
                if not (clave and texto):
                    continue
                _lang = _normalizar_idioma(_texto_propiedad(props, "Idioma"))
                mapas.setdefault(_lang, {})[clave] = texto
            if not resp.get("has_more"):
                break
            cursor = resp.get("next_cursor")
        es_map = mapas.get("es", {})
        base = mapas.get(idioma, {})
        claves = sorted(set(es_map) | set(base))
        resultado = [{"clave": c, "texto": base.get(c) or es_map.get(c)} for c in claves if (base.get(c) or es_map.get(c))]
        if not resultado:
            resultado = sorted(
                [{"clave": c, "texto": t} for tp, c, t in _PREGUNTAS_PALANTIR_DEFAULT if tp == tipo],
                key=lambda x: x["clave"],
            )
        with _lock_preguntas_palantir:
            _cache_preguntas_palantir_data[cache_key] = resultado
            _cache_preguntas_palantir_ts[cache_key] = time.time()
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
        pagina = _crear_pagina_en_bbdd(
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
        return pagina["id"]
    except Exception:
        logging.exception("Error guardando en Notion")
        return None


def actualizar_en_notion(page_id: str, nombre: str, respuestas: dict, relacion: str = "igual", area: str = "Negocio") -> bool:
    try:
        _skip = {"evaluado", "proyecto", "satisfaccion"}
        _extras = [v for k, v in respuestas.items() if k not in _skip and v]
        valoracion = _extras[0] if len(_extras) > 0 else ""
        justificacion = _extras[1] if len(_extras) > 1 else ""
        suf_col = {"superior": "de superiores", "inferior": "de inferiores"}.get(relacion, "de iguales")
        notion.pages.update(
            page_id=page_id,
            properties={
                f"Valoración {suf_col}": {"rich_text": [{"text": {"content": valoracion}}]},
                f"Justificación {suf_col}": {"rich_text": [{"text": {"content": justificacion}}]},
            },
        )
        return True
    except Exception:
        logging.exception("Error actualizando en Notion")
        return False


_cache_bbdd_continuas: dict = {"db_id": None}


_TITULO_BBDD_BARBECHO_NUEVO = "Resultados Barbecho"
_TITULO_BBDD_BARBECHO_ANTIGUO = "Registros barbecho"


def _obtener_o_crear_bbdd_continuas() -> str:
    with lock:
        db_id = _cache_bbdd_continuas["db_id"]
    if db_id:
        return db_id
    # Buscar por nombre nuevo o antiguo, sin restricción de parent (puede estar en nueva ubicación)
    for titulo_buscar in (_TITULO_BBDD_BARBECHO_NUEVO, _TITULO_BBDD_BARBECHO_ANTIGUO):
        resultado = notion.search(query=titulo_buscar, filter={"value": _tipo_objeto_busqueda_bbdd(), "property": "object"}, page_size=50)
        for bbdd in resultado.get("results", []):
            titulo_bbdd = _extraer_titulo_bbdd(bbdd)
            if titulo_bbdd in (_TITULO_BBDD_BARBECHO_NUEVO, _TITULO_BBDD_BARBECHO_ANTIGUO):
                found_id = _data_source_id(bbdd)
                with lock:
                    _cache_bbdd_continuas["db_id"] = found_id
                return found_id
    # Crear bajo "Resultados Evaluaciones" si existe, si no bajo root
    titulo = _TITULO_BBDD_BARBECHO_NUEVO
    parent_resultados = _parent_bbdd_en_pagina(config.NOTION_RESULTADOS_EVAL_PAGE_NAME, crear=False)
    parent = parent_resultados if parent_resultados.get("type") == "page_id" else _parent_bbdd_referencia()
    props = {
        "Name": {"title": {}},
        "Empleado": {"rich_text": {}},
        "Area": {"select": {}},
        "Labores": {"rich_text": {}},
        "Fecha": {"date": {}},
    }
    if _usa_data_sources():
        nueva = notion.databases.create(
            parent=parent,
            title=[{"type": "text", "text": {"content": titulo}}],
            initial_data_source={"title": [{"type": "text", "text": {"content": titulo}}], "properties": props},
        )
        nueva = notion.databases.retrieve(database_id=nueva["id"])
    else:
        nueva = notion.databases.create(parent=parent, title=[{"type": "text", "text": {"content": titulo}}], properties=props)
    new_id = _data_source_id(nueva)
    with lock:
        _cache_bbdd_continuas["db_id"] = new_id
    logging.info("Base de datos '%s' creada", titulo)
    _decorar_bbdd_notion(new_id, titulo)
    return new_id


def guardar_barbecho_en_notion(nombre: str, area: str, labores: str) -> bool:
    try:
        db_id = _obtener_o_crear_bbdd_continuas()
        fecha = datetime.now(timezone.utc)
        _crear_pagina_en_bbdd(
            db_id,
            {
                "Name": {"title": [{"text": {"content": f"Barbecho {nombre} {fecha.strftime('%Y-%m-%d %H:%M')}"}}]},
                "Empleado": {"rich_text": [{"text": {"content": nombre}}]},
                "Area": {"select": {"name": area}},
                "Labores": {"rich_text": [{"text": {"content": labores}}]},
                "Fecha": {"date": {"start": fecha.isoformat()}},
            },
        )
        return True
    except Exception:
        logging.exception("Error guardando barbecho en Notion para '%s'", nombre)
        return False


def obtener_barbecho_por_empleado(nombre: str) -> list[dict]:
    """Registros de barbecho (labores en periodo sin proyecto) de un empleado.

    Cada elemento: {area, labores, fecha (YYYY-MM-DD), page_id, url}, ordenado por fecha.
    """
    if not nombre:
        return []
    objetivo = normalizar_nombre(nombre)
    registros: list[dict] = []
    try:
        db_id = _obtener_o_crear_bbdd_continuas()
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
                empleado = _texto_propiedad(props, "Empleado")
                if normalizar_nombre(empleado) != objetivo:
                    continue
                labores = _texto_propiedad(props, "Labores")
                area = _texto_propiedad(props, "Area")
                fecha = ((props.get("Fecha") or {}).get("date") or {}).get("start", "")
                if not labores:
                    continue
                registros.append({
                    "area": area,
                    "labores": labores,
                    "fecha": (fecha or "")[:10],
                    "page_id": fila.get("id", ""),
                    "url": fila.get("url", ""),
                })
            if not resp.get("has_more"):
                break
            cursor = resp.get("next_cursor")
    except Exception:
        logging.exception("Error leyendo barbecho de '%s'", nombre)
    registros.sort(key=lambda x: x.get("fecha", ""))
    return registros


_cache_bbdd_sesiones_anual: dict = {"db_id": None}


def _obtener_o_crear_bbdd_sesiones_anual() -> str | None:
    with lock:
        db_id = _cache_bbdd_sesiones_anual["db_id"]
    if db_id:
        return db_id
    parent = _parent_bbdd_referencia()
    titulo = "Log evaluacion anual asistida"
    try:
        resultado = notion.search(query=titulo, filter={"value": _tipo_objeto_busqueda_bbdd(), "property": "object"}, page_size=50)
        for bbdd in resultado.get("results", []):
            if _extraer_titulo_bbdd(bbdd) == titulo and _coincide_parent_bbdd(bbdd, parent):
                found_id = _data_source_id(bbdd)
                with lock:
                    _cache_bbdd_sesiones_anual["db_id"] = found_id
                return found_id
        props = {
            "Name": {"title": {}},
            "Advisee": {"rich_text": {}},
            "CA": {"rich_text": {}},
            "Anio": {"number": {}},
            "Dimension": {"rich_text": {}},
            "ValoracionCA": {"rich_text": {}},
            "ValoracionIA": {"rich_text": {}},
            "Eleccion": {"select": {}},
            "Divergencia": {"checkbox": {}},
            "TextoFinal": {"rich_text": {}},
            "Fecha": {"date": {}},
        }
        if _usa_data_sources():
            nueva = notion.databases.create(
                parent=parent,
                title=[{"type": "text", "text": {"content": titulo}}],
                initial_data_source={"title": [{"type": "text", "text": {"content": titulo}}], "properties": props},
            )
            nueva = notion.databases.retrieve(database_id=nueva["id"])
        else:
            nueva = notion.databases.create(parent=parent, title=[{"type": "text", "text": {"content": titulo}}], properties=props)
        new_id = _data_source_id(nueva)
        with lock:
            _cache_bbdd_sesiones_anual["db_id"] = new_id
        logging.info("Base de datos '%s' creada", titulo)
        _decorar_bbdd_notion(new_id, titulo)
        return new_id
    except Exception:
        logging.exception("No se pudo obtener/crear la BD del log de evaluación anual")
        return None


def guardar_log_evaluacion_anual(advisee: str, ca: str, anio, entradas: list[dict]) -> bool:
    """Escribe en Notion el log de auditoría (decisiones CA vs IA). Best-effort, no rompe el flujo."""
    try:
        db_id = _obtener_o_crear_bbdd_sesiones_anual()
        if not db_id:
            return False
        fecha = datetime.now(timezone.utc).isoformat()

        def _rt(texto):
            return [{"type": "text", "text": {"content": (texto or "")[:2000]}}]

        for e in entradas:
            _crear_pagina_en_bbdd(db_id, {
                "Name": {"title": [{"type": "text", "text": {"content": f"{advisee} · {e.get('etiqueta', '')}"[:200]}}]},
                "Advisee": {"rich_text": _rt(advisee)},
                "CA": {"rich_text": _rt(ca)},
                "Anio": {"number": int(anio) if str(anio).isdigit() else None},
                "Dimension": {"rich_text": _rt(e.get("etiqueta", ""))},
                "ValoracionCA": {"rich_text": _rt(e.get("caTexto", ""))},
                "ValoracionIA": {"rich_text": _rt(e.get("claudeTexto", ""))},
                "Eleccion": {"select": {"name": e.get("eleccion", "") or "—"}},
                "Divergencia": {"checkbox": bool(e.get("divergencia"))},
                "TextoFinal": {"rich_text": _rt(e.get("textoFinal", ""))},
                "Fecha": {"date": {"start": fecha}},
            })
        return True
    except Exception:
        logging.exception("No se pudo guardar el log de evaluación anual de '%s'", advisee)
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
    if tipo == "status":
        return (propiedad.get("status") or {}).get("name", "").strip()
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


def _codigo_idioma(valor: str) -> str:
    """Extrae un codigo de idioma de 2 letras del texto de la columna Idioma.

    Acepta: 'ES', 'EN', 'Espanol (ES)', 'Ingles (EN)', 'English', 'Portugues (PT)'...
    Devuelve el codigo en minusculas (p.ej. 'es', 'en', 'pt') o '' si no lo reconoce.
    """
    v = (valor or "").strip().lower()
    if not v:
        return ""
    # 1) Codigo entre parentesis: "Espanol (ES)" -> es
    m = re.search(r"\(([a-z]{2})\)", v)
    if m:
        return m.group(1)
    # 2) El valor ya es un codigo de 2 letras: "es", "en", "pt"...
    if len(v) == 2 and v.isalpha():
        return v
    # 3) Nombres de idioma conocidos (por si se escribe el nombre completo)
    for fragmento, codigo in (("ingl", "en"), ("engl", "en"), ("espa", "es"),
                              ("castel", "es"), ("portug", "pt"), ("fran", "fr")):
        if fragmento in v:
            return codigo
    return ""


def _normalizar_area(valor: str) -> str:
    """Mapea la columna Área/Area de la Lista de empleados a 'negocio' | 'palantir' | 'middleoffice'.

    Acepta variantes como 'Negocio', 'Palantir', 'MiddleOffice', 'Middle Office', 'MO'...
    Devuelve '' si no reconoce el valor (para que el bot pregunte como fallback).
    """
    v = (valor or "").strip().lower().replace(" ", "")
    if not v:
        return ""
    if "negocio" in v or v == "business":
        return "negocio"
    if "palantir" in v:
        return "palantir"
    if "middle" in v or v == "mo":
        return "middleoffice"
    return ""


def _normalizar_idioma(valor: str) -> str:
    """Mapea la columna Idioma de Notion a un idioma soportado. Por defecto 'es'.

    Un codigo no soportado todavia (sin traducciones) cae a 'es'. Para anadir un
    idioma nuevo basta con incluir su codigo en IDIOMAS_SOPORTADOS (i18n.py) y sus
    traducciones; esta funcion ya lo detectara sola.
    """
    codigo = _codigo_idioma(valor)
    return codigo if codigo in IDIOMAS_SOPORTADOS else "es"


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

                area = ""
                for area_prop in ("Área", "Area", "Departamento", "Department"):
                    if area_prop in props:
                        area = _normalizar_area(_texto_propiedad(props, area_prop))
                        if area:
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

                idioma = "es"
                idioma_prop = ""
                idioma_prop_tipo = ""
                for ip in ("Idioma", "Language", "Lang"):
                    if ip in props:
                        idioma_prop = ip
                        idioma_prop_tipo = (props.get(ip) or {}).get("type", "")
                        valor_idioma = _texto_propiedad(props, ip)
                        if valor_idioma:
                            idioma = _normalizar_idioma(valor_idioma)
                        break

                pais = ""
                pais_prop = ""
                pais_prop_tipo = ""
                for pp in ("Pais", "País", "Country"):
                    if pp in props:
                        pais_prop = pp
                        pais_prop_tipo = (props.get(pp) or {}).get("type", "")
                        pais = _texto_propiedad(props, pp)
                        break

                baja = bool((props.get("Baja") or {}).get("checkbox", False))
                registros.append({"nombre": nombre, "email": email.strip(), "aliases": aliases, "cargo": cargo, "area": area, "id_usuario": id_usuario, "foto": foto, "idioma": idioma, "pais": pais, "baja": baja, "page_id": pagina.get("id", ""), "idioma_prop": idioma_prop, "idioma_prop_tipo": idioma_prop_tipo, "pais_prop": pais_prop, "pais_prop_tipo": pais_prop_tipo})
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


def invalidar_cache_empleados() -> None:
    """Fuerza que la próxima lectura de empleados vuelva a Notion (para reflejar cambios recientes,
    p. ej. la columna Idioma). Útil antes de enviar los DM iniciales."""
    global _empleados_cache_ts
    with _lock_empleados:
        _empleados_cache_ts = 0.0


def obtener_lista_empleados() -> list[str]:
    """Lee los nombres canonicos de empleados desde Notion."""
    return [registro["nombre"] for registro in _obtener_registros_empleados()]


def obtener_registros_empleados() -> list[dict]:
    """Lee empleados con nombre, email y aliases desde Notion."""
    return _obtener_registros_empleados()


def obtener_perfil_empleado(nombre: str) -> dict:
    """Devuelve cargo, foto, idioma y pais del empleado que coincide con el nombre dado."""
    nombre_norm = normalizar_nombre(nombre)
    for r in _obtener_registros_empleados():
        if normalizar_nombre(r["nombre"]) == nombre_norm:
            return {"cargo": r.get("cargo", ""), "foto": r.get("foto", ""), "idioma": r.get("idioma", "es"), "pais": r.get("pais", "")}
    return {"cargo": "", "foto": "", "idioma": "es", "pais": ""}


def idioma_de_persona(nombre: str) -> str:
    """Devuelve el idioma ('es' | 'en') del empleado. Por defecto 'es' si no se encuentra o no tiene idioma."""
    nombre_norm = normalizar_nombre(nombre)
    for r in _obtener_registros_empleados():
        if normalizar_nombre(r["nombre"]) == nombre_norm:
            return r.get("idioma", "es") or "es"
    return "es"


def idioma_por_sesion(sesion: dict) -> str:
    """Idioma del usuario web probando persona/username/email contra nombre, aliases,
    email e ID de Slack de la Lista de empleados. Por defecto 'es'. Registra el match en log."""
    candidatos = {
        normalizar_nombre(v)
        for v in (sesion.get("persona"), sesion.get("username"), sesion.get("email"))
        if v
    }
    if not candidatos:
        return "es"
    for r in _obtener_registros_empleados():
        ids = {normalizar_nombre(r.get("nombre", ""))}
        ids |= {normalizar_nombre(a) for a in r.get("aliases", []) if a}
        if r.get("email"):
            ids.add(normalizar_nombre(r["email"]))
        if r.get("id_usuario"):
            ids.add(normalizar_nombre(r["id_usuario"]))
        if candidatos & ids:
            idi = r.get("idioma", "es") or "es"
            logging.info("[i18n] sesion %s -> empleado '%s' (idioma=%s)", candidatos, r.get("nombre"), idi)
            return idi
    logging.info("[i18n] sesion %s sin match en Lista de empleados -> es", candidatos)
    return "es"


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
        # Buscar "Criterios de evaluaciones": nueva ubicación (Datos opcionalmente modificables)
        # y ubicación antigua (Datos a Monitorizar / Listas de datos) como fallback
        criterios_page_id = None
        for nombre_contenedor in (config.NOTION_DATA_MODIFICABLES_PAGE_NAME, config.NOTION_DATA_LISTS_PAGE_NAME):
            parent = _parent_bbdd_en_pagina(nombre_contenedor, crear=False)
            if parent.get("type") == "page_id":
                criterios_page_id = _page_or_database_link_by_name(parent["page_id"], _NOMBRE_PAGINA_CRITERIOS)
                if criterios_page_id:
                    break
        # Fallback: búsqueda global por nombre
        if not criterios_page_id:
            criterios_page_id = _buscar_objeto_notion_por_nombre(_NOMBRE_PAGINA_CRITERIOS)
        if not criterios_page_id:
            logging.warning("[criterios] No se encontró la página '%s'", _NOMBRE_PAGINA_CRITERIOS)
            return None
        ids: dict[str, str] = {}
        for bloque in _iter_blocks(criterios_page_id):
            tipo = bloque.get("type", "")
            if tipo == "child_database":
                titulo = _titulo_child_database(bloque)
                try:
                    db = notion.databases.retrieve(database_id=bloque["id"])
                    db_id = _data_source_id(db)
                except Exception:
                    db_id = bloque["id"]
                ids.setdefault(titulo, db_id)
            elif tipo == "child_page":
                # Las BDs de criterios son páginas completas — buscar child_database dentro
                titulo_pagina = (bloque.get("child_page") or {}).get("title", "")
                page_id = bloque.get("id", "")
                if not titulo_pagina or not page_id:
                    continue
                for sub in _iter_blocks(page_id):
                    if sub.get("type") == "child_database":
                        try:
                            db = notion.databases.retrieve(database_id=sub["id"])
                            db_id = _data_source_id(db)
                        except Exception:
                            db_id = sub["id"]
                        ids.setdefault(titulo_pagina, db_id)
                        break  # solo necesitamos la primera BD de cada página
        logging.info("[criterios] BDs encontradas: %s", list(ids.keys()))
        with _lock_criterios:
            _criterios_db_ids = ids
            _criterios_db_ids_ts = time.time()
        return ids.get(grupo)
    except Exception:
        logging.exception("Error buscando BD de criterios para grupo '%s'", grupo)
        return None


def obtener_criterios_evaluacion(grupo: str, idioma: str = "es") -> dict:
    """
    Devuelve {dimension_label: {nivel: [textos]}} para el grupo indicado, en el idioma dado.
    Lee de 'Criterios de evaluaciones/{grupo}' en Notion. Cachea 5 min.
    Filtra por la columna 'Idioma' (ES/EN); el nombre del Criterio es la clave estable
    (no se traduce) y se cae a ES cuando un criterio no tiene fila EN.
    """
    idioma = idioma if idioma in IDIOMAS_SOPORTADOS else "es"
    ahora = time.time()
    cache_key = f"{grupo}|{idioma}"
    with _lock_criterios:
        cached = _cache_criterios.get(cache_key)
        if cached and (ahora - cached[1]) < _CRITERIOS_CACHE_TTL:
            return cached[0]

    db_id = _obtener_db_criterios(grupo)
    if not db_id:
        return {}

    _NIVELES = ["Trainee", "Analista", "Asociado", "Asociado Sr", "Manager"]

    def _rt(prop):
        return "".join(t.get("plain_text", "") for t in (prop or {}).get("rich_text", [])).strip()

    mapas: dict = {}  # idioma -> {criterio: niveles}
    try:
        cursor = None
        while True:
            kwargs: dict = {"page_size": 100}
            if cursor:
                kwargs["start_cursor"] = cursor
            resp = _query_bbdd(db_id, **kwargs)
            rows = resp.get("results", [])
            for row in rows:
                props = row.get("properties", {})
                criterio = "".join(t.get("plain_text", "") for t in (props.get("Criterio") or {}).get("title", [])).strip()
                if not criterio:
                    continue
                niveles: dict = {}
                for nivel in _NIVELES:
                    texto = _rt(props.get(nivel))
                    if texto:
                        lineas = [
                            re.sub(r"^[-•*]\s*", "", linea).strip()
                            for linea in texto.split("\n")
                        ]
                        lineas = [linea for linea in lineas if linea]
                        if lineas:
                            niveles[nivel] = lineas
                if not niveles:
                    continue
                _lang = _normalizar_idioma(_texto_propiedad(props, "Idioma"))
                mapas.setdefault(_lang, {})[criterio] = niveles
            if not resp.get("has_more"):
                break
            cursor = resp.get("next_cursor")
    except Exception:
        logging.exception("Error leyendo criterios para grupo '%s'", grupo)
        return {}

    es_res = mapas.get("es", {})
    base = mapas.get(idioma, {})
    resultado = {c: (base.get(c) or es_res.get(c)) for c in (list(es_res) + [k for k in base if k not in es_res])}

    with _lock_criterios:
        _cache_criterios[cache_key] = (resultado, time.time())
    return resultado


# ---------------------------------------------------------------------------
# Ejemplos de guía (Ejemplos de guia en Listas de datos)
# ---------------------------------------------------------------------------

_NOMBRE_PAGINA_EJEMPLOS = "Ejemplos de Guia para bot"
_NOMBRES_PAGINA_EJEMPLOS_FALLBACK = ("Ejemplos de guia", "Ejemplos de guía")
_cache_ejemplos: dict = {}  # idioma -> (data, ts)
_cache_ejemplos_ts: float = 0.0
_ejemplos_db_id: str | None = None
_ejemplos_db_id_ts: float = 0.0
_lock_ejemplos = threading.Lock()
_EJEMPLOS_CACHE_TTL = 300  # 5 minutos


def _obtener_db_ejemplos() -> str | None:
    global _ejemplos_db_id, _ejemplos_db_id_ts
    ahora = time.time()
    with _lock_ejemplos:
        if _ejemplos_db_id and (ahora - _ejemplos_db_id_ts) < _EJEMPLOS_CACHE_TTL:
            return _ejemplos_db_id
    try:
        # Buscar en nueva ubicación (Datos opcionalmente modificables) primero, luego en antigua
        encontrado_id = None
        nombres_buscar = (_NOMBRE_PAGINA_EJEMPLOS,) + _NOMBRES_PAGINA_EJEMPLOS_FALLBACK
        for nombre_contenedor in (config.NOTION_DATA_MODIFICABLES_PAGE_NAME, config.NOTION_DATA_LISTS_PAGE_NAME):
            parent = _parent_bbdd_en_pagina(nombre_contenedor, crear=False)
            if parent.get("type") != "page_id":
                continue
            for nombre_ej in nombres_buscar:
                encontrado_id = _page_or_database_link_by_name(parent["page_id"], nombre_ej)
                if encontrado_id:
                    break
            if encontrado_id:
                break
        if not encontrado_id:
            for nombre_ej in nombres_buscar:
                encontrado_id = _buscar_objeto_notion_por_nombre(nombre_ej)
                if encontrado_id:
                    break
        if not encontrado_id:
            logging.warning("[ejemplos] No se encontró '%s'", _NOMBRE_PAGINA_EJEMPLOS)
            return None

        # Caso 1: el ID encontrado ya ES la base de datos directamente
        try:
            db = notion.databases.retrieve(database_id=encontrado_id)
            db_id = _data_source_id(db)
            logging.info("[ejemplos] BD de ejemplos encontrada directamente: %s", db_id)
            with _lock_ejemplos:
                _ejemplos_db_id = db_id
                _ejemplos_db_id_ts = time.time()
            return db_id
        except Exception:
            pass

        # Caso 2: el ID es una página que contiene una child_database
        for bloque in _iter_blocks(encontrado_id):
            if bloque.get("type") == "child_database":
                try:
                    db = notion.databases.retrieve(database_id=bloque["id"])
                    db_id = _data_source_id(db)
                except Exception:
                    db_id = bloque["id"]
                logging.info("[ejemplos] BD de ejemplos encontrada como child_database: %s", db_id)
                with _lock_ejemplos:
                    _ejemplos_db_id = db_id
                    _ejemplos_db_id_ts = time.time()
                return db_id
    except Exception:
        logging.exception("[ejemplos] Error buscando BD de ejemplos de guía")
    return None


def obtener_ejemplos_guia(idioma: str = "es") -> dict:
    """
    Devuelve {tipo: texto_ejemplo} leyendo la BD 'Ejemplos de Guia para bot' en el idioma dado.
    La BD tiene una columna título ('Tipo', la clave) y una columna rich_text con el ejemplo.
    Filtra por la columna 'Idioma' (ES/EN) con fallback EN->ES por tipo. Cachea 5 min.
    """
    global _cache_ejemplos
    idioma = idioma if idioma in IDIOMAS_SOPORTADOS else "es"
    ahora = time.time()
    with _lock_ejemplos:
        cached = _cache_ejemplos.get(idioma) if isinstance(_cache_ejemplos, dict) else None
        if cached and (ahora - cached[1]) < _EJEMPLOS_CACHE_TTL:
            return cached[0]

    db_id = _obtener_db_ejemplos()
    if not db_id:
        return {}

    def _rt(prop):
        return "".join(t.get("plain_text", "") for t in (prop or {}).get("rich_text", [])).strip()

    mapas: dict = {}  # idioma -> {tipo: ejemplo}
    try:
        cursor = None
        while True:
            kwargs: dict = {"page_size": 100}
            if cursor:
                kwargs["start_cursor"] = cursor
            resp = _query_bbdd(db_id, **kwargs)
            for row in resp.get("results", []):
                props = row.get("properties", {})
                # Detectar la columna título genéricamente (sea cual sea su nombre)
                tipo = ""
                tipo_key = None
                for key, val in props.items():
                    if (val or {}).get("type") == "title":
                        tipo = "".join(t.get("plain_text", "") for t in (val or {}).get("title", [])).strip()
                        tipo_key = key
                        break
                if not tipo:
                    continue
                # Primera columna rich_text (que no sea el título) como texto del ejemplo
                ejemplo = ""
                for key, val in props.items():
                    if key != tipo_key and (val or {}).get("type") == "rich_text":
                        texto = _rt(val)
                        if texto:
                            ejemplo = texto
                            break
                _lang = _normalizar_idioma(_texto_propiedad(props, "Idioma"))
                mapas.setdefault(_lang, {})[tipo] = ejemplo
            if not resp.get("has_more"):
                break
            cursor = resp.get("next_cursor")
    except Exception:
        logging.exception("[ejemplos] Error leyendo BD de ejemplos de guía")
        return {}

    es_map = mapas.get("es", {})
    base = mapas.get(idioma, {})
    resultado = {tp: (base.get(tp) or es_map.get(tp)) for tp in (set(es_map) | set(base))}

    with _lock_ejemplos:
        if not isinstance(_cache_ejemplos, dict):
            _cache_ejemplos = {}
        _cache_ejemplos[idioma] = (resultado, time.time())
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


_GRUPO_DISPLAY = {"negocio": "Negocio", "palantir": "Palantir", "middleoffice": "MiddleOffice"}


def obtener_grupo_empleado(nombre: str) -> str:
    """Grupo del empleado (Negocio/Palantir/MiddleOffice) desde la columna Área de 'Lista de empleados'.

    Devuelve '' si no consta (para que el que llama caiga a inferirlo del cargo).
    """
    objetivo = _normalizar_para_match(nombre)
    if not objetivo:
        return ""
    for registro in _obtener_registros_empleados():
        if objetivo == _normalizar_para_match(registro["nombre"]):
            return _GRUPO_DISPLAY.get(registro.get("area", ""), "")
    return ""


def obtener_cargo_por_slack_id(user_id: str) -> str | None:
    """Devuelve el cargo del empleado cuyo ID_usuario coincide con user_id."""
    for registro in _obtener_registros_empleados():
        if registro.get("id_usuario") == user_id:
            return registro.get("cargo") or None
    return None


def obtener_area_por_slack_id(user_id: str) -> str | None:
    """Devuelve 'negocio' | 'palantir' | 'middleoffice' segun la columna Área de la Lista de
    empleados para ese Slack ID, o None si no hay empleado o no tiene área asignada en Notion."""
    for registro in _obtener_registros_empleados():
        if registro.get("id_usuario") == user_id:
            return registro.get("area") or None
    return None


def idioma_por_slack_id(user_id: str) -> str:
    """Devuelve el idioma ('es' | 'en') del empleado cuyo Slack ID coincide. Por defecto 'es'."""
    for registro in _obtener_registros_empleados():
        if registro.get("id_usuario") == user_id:
            return registro.get("idioma", "es") or "es"
    return "es"


def _valor_idioma_notion(idioma: str, tipo: str) -> dict:
    """Payload de la propiedad 'Idioma' de Notion segun el tipo de columna (select/status/title/text)."""
    idioma = idioma if idioma in IDIOMAS_SOPORTADOS else "es"
    texto = idioma.upper()  # es->ES, en->EN, pt->PT
    if tipo == "select":
        return {"select": {"name": texto}}
    if tipo == "status":
        return {"status": {"name": texto}}
    if tipo == "title":
        return {"title": [{"type": "text", "text": {"content": texto}}]}
    return {"rich_text": [{"type": "text", "text": {"content": texto}}]}


def _guardar_idioma_en_registro(registro: dict, idioma: str) -> str:
    """Escribe el idioma en la columna Idioma de Notion para ese empleado y actualiza la cache. Devuelve el idioma."""
    idioma = idioma if idioma in IDIOMAS_SOPORTADOS else "es"
    page_id = registro.get("page_id")
    prop = registro.get("idioma_prop")
    tipo = registro.get("idioma_prop_tipo") or "select"
    if page_id and prop:
        try:
            notion.pages.update(page_id=page_id, properties={prop: _valor_idioma_notion(idioma, tipo)})
        except Exception:
            logging.exception("Error guardando idioma en Notion (page %s, prop %s)", page_id, prop)
            return registro.get("idioma", "es") or "es"
    else:
        logging.warning("No se pudo guardar idioma: no hay columna 'Idioma' en la Lista de empleados.")
        return registro.get("idioma", "es") or "es"
    # Actualiza el registro cacheado para que las lecturas devuelvan el nuevo valor al instante.
    with _lock_empleados:
        registro["idioma"] = idioma
    return idioma


def guardar_idioma_por_slack_id(user_id: str, idioma: str) -> str:
    """Escribe en Notion el idioma del empleado con ese Slack ID. Devuelve el idioma efectivo."""
    for registro in _obtener_registros_empleados():
        if registro.get("id_usuario") == user_id:
            return _guardar_idioma_en_registro(registro, idioma)
    return "es"


def guardar_idioma_por_sesion(sesion: dict, idioma: str) -> str:
    """Escribe en Notion el idioma del usuario web (match por persona/username/email). Devuelve el idioma efectivo."""
    candidatos = {
        normalizar_nombre(v)
        for v in (sesion.get("persona"), sesion.get("username"), sesion.get("email"))
        if v
    }
    if not candidatos:
        return "es"
    for registro in _obtener_registros_empleados():
        ids = {normalizar_nombre(registro.get("nombre", ""))}
        ids |= {normalizar_nombre(a) for a in registro.get("aliases", []) if a}
        if registro.get("email"):
            ids.add(normalizar_nombre(registro["email"]))
        if registro.get("id_usuario"):
            ids.add(normalizar_nombre(registro["id_usuario"]))
        if candidatos & ids:
            return _guardar_idioma_en_registro(registro, idioma)
    return "es"


def _valor_pais_notion(pais: str, tipo: str) -> dict:
    """Payload de la propiedad 'Pais' de Notion segun el tipo de columna (select/status/title/text)."""
    pais = (pais or "").strip()
    if tipo == "select":
        return {"select": {"name": pais} if pais else None}
    if tipo == "status":
        return {"status": {"name": pais} if pais else None}
    if tipo == "title":
        return {"title": [{"type": "text", "text": {"content": pais}}]}
    return {"rich_text": [{"type": "text", "text": {"content": pais}}]}


def _guardar_pais_en_registro(registro: dict, pais: str) -> str:
    """Escribe el pais en la columna Pais de Notion para ese empleado y actualiza la cache. Devuelve el pais."""
    pais = (pais or "").strip()
    page_id = registro.get("page_id")
    prop = registro.get("pais_prop")
    tipo = registro.get("pais_prop_tipo") or "rich_text"
    if not (page_id and prop):
        logging.warning("No se pudo guardar pais: no hay columna 'Pais' en la Lista de empleados.")
        return registro.get("pais", "") or ""
    try:
        notion.pages.update(page_id=page_id, properties={prop: _valor_pais_notion(pais, tipo)})
    except Exception:
        logging.exception("Error guardando pais en Notion (page %s, prop %s)", page_id, prop)
        return registro.get("pais", "") or ""
    # Actualiza el registro cacheado para que las lecturas devuelvan el nuevo valor al instante.
    with _lock_empleados:
        registro["pais"] = pais
    return pais


def guardar_pais_por_sesion(sesion: dict, pais: str) -> str:
    """Escribe en Notion el pais del usuario web (match por persona/username/email). Devuelve el pais efectivo."""
    candidatos = {
        normalizar_nombre(v)
        for v in (sesion.get("persona"), sesion.get("username"), sesion.get("email"))
        if v
    }
    if not candidatos:
        return ""
    for registro in _obtener_registros_empleados():
        ids = {normalizar_nombre(registro.get("nombre", ""))}
        ids |= {normalizar_nombre(a) for a in registro.get("aliases", []) if a}
        if registro.get("email"):
            ids.add(normalizar_nombre(registro["email"]))
        if registro.get("id_usuario"):
            ids.add(normalizar_nombre(registro["id_usuario"]))
        if candidatos & ids:
            return _guardar_pais_en_registro(registro, pais)
    return ""


def obtener_paises_disponibles() -> list[str]:
    """Devuelve los paises distintos ya presentes en la Lista de empleados, ordenados."""
    vistos = {}
    for registro in _obtener_registros_empleados():
        p = (registro.get("pais") or "").strip()
        if p:
            vistos.setdefault(p.casefold(), p)
    return sorted(vistos.values(), key=lambda s: s.casefold())


def obtener_slack_ids_empleados() -> list[str]:
    """Devuelve todos los ID_usuario (Slack IDs) no vacíos de la lista de empleados."""
    return [r["id_usuario"] for r in _obtener_registros_empleados() if r.get("id_usuario")]


def obtener_slack_id_por_nombre(nombre: str) -> str | None:
    """Devuelve el Slack ID del empleado cuyo nombre coincide (normalizado)."""
    nombre_norm = normalizar_nombre(nombre)
    for registro in _obtener_registros_empleados():
        if normalizar_nombre(registro.get("nombre", "")) == nombre_norm:
            return registro.get("id_usuario") or None
    return None




def sugerir_empleados_parecidos(nombre: str, limite: int = 8, excluir: str | None = None) -> list[str]:
    excluir_norm = _normalizar_para_match(excluir) if excluir else None
    candidatos = []
    for registro in _obtener_registros_empleados():
        if excluir_norm and _normalizar_para_match(registro["nombre"]) == excluir_norm:
            continue  # no sugerir el propio nombre del evaluador
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
                    "page_id": pagina.get("id", ""),
                    "url": pagina.get("url", ""),
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
        if not config.NOTION_DATABASE_ID:
            return evaluaciones
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


def excluir_feedback_confidencial(evaluaciones: list[dict]) -> list[dict]:
    """Quita el feedback bottom-to-top (subordinado evaluando a un superior):
    es confidencial, solo accesible para administradores desde el panel dedicado."""
    return [e for e in evaluaciones if e.get("relacion") != "inferior"]


def obtener_feedback_confidencial_por_evaluado(evaluado: str) -> list[dict]:
    """Feedback bottom-to-top (subordinado -> superior) de un evaluado.
    Confidencial: solo para el panel de administración. Nunca incluye
    quién lo escribió."""
    evaluaciones = obtener_evaluaciones_por_evaluado(evaluado)
    return [
        {"proyecto": e.get("proyecto", ""), "q1": e.get("q1", ""),
         "q2": e.get("q2", ""), "fecha": e.get("fecha", "")}
        for e in evaluaciones if e.get("relacion") == "inferior"
    ]


def obtener_todo_el_feedback_confidencial() -> list[dict]:
    """Todo el feedback bottom-to-top (subordinado -> superior) de todas las personas,
    para la vista agregada del panel de administración. Nunca incluye quién lo escribió.
    Ordenado por fecha descendente (más reciente primero)."""
    resultado = [
        {"evaluado": e.get("evaluado", ""), "proyecto": e.get("proyecto", ""),
         "q1": e.get("q1", ""), "q2": e.get("q2", ""), "fecha": e.get("fecha", "")}
        for e in obtener_evaluaciones() if e.get("relacion") == "inferior"
    ]
    resultado.sort(key=lambda e: e.get("fecha", ""), reverse=True)
    return resultado


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
_cache_advisees_por_ca: dict = {}  # ca_norm -> (advisees, ts)
_ADVISEES_CACHE_TTL = 300  # 5 minutos: refleja cambios en 'Lista CA' sin reiniciar


def obtener_advisees(ca_nombre: str, ca_aliases=None) -> list[str]:
    """Retorna los advisees de un CA desde 'Lista CA' (columna CA y columnas A1, A2, ...)."""
    ca_norms = {normalizar_nombre(valor) for valor in [ca_nombre, *(ca_aliases or [])] if valor}
    ca_norm = sorted(ca_norms)[0] if ca_norms else ""
    logging.info(f"[advisees] Buscando advisees para CA: '{ca_nombre}' (aliases: {ca_norms})")
    with lock:
        entry = _cache_advisees_por_ca.get(ca_norm)
        if entry and (time.time() - entry[1]) < _ADVISEES_CACHE_TTL:
            return entry[0]
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
                if titulo_norm in ("lista ca", "lista de cas"):
                    db_id = _data_source_id(bbdd)
                    logging.info(f"[advisees] Base de datos CA encontrada (exacto): '{titulo}' (id: {db_id})")
                    with lock:
                        _cache_lista_ca["db_id"] = db_id
                    break
                if titulo_norm.startswith("lista ca") or titulo_norm.startswith("lista de ca"):
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
                    _cache_advisees_por_ca[ca_norm] = (advisees, time.time())
                return advisees
            if not resp.get("has_more"):
                break
            cursor = resp.get("next_cursor")
        with lock:
            _cache_advisees_por_ca[ca_norm] = ([], time.time())
        return []
    except Exception:
        logging.exception(f"Error obteniendo advisees de '{ca_nombre}'")
        return []


def obtener_todos_los_advisees() -> list[str]:
    """Devuelve todos los advisees únicos de TODOS los CAs desde la Lista CA."""
    with lock:
        db_id = _cache_lista_ca.get("db_id", "")
    if not db_id:
        resultado = notion.search(
            query="Lista CA",
            filter={"value": _tipo_objeto_busqueda_bbdd(), "property": "object"},
            page_size=50,
        )
        for bbdd in resultado.get("results", []):
            titulo_norm = normalizar_nombre(_extraer_titulo_bbdd(bbdd))
            if titulo_norm in ("lista ca", "lista de cas") or titulo_norm.startswith("lista ca") or titulo_norm.startswith("lista de ca"):
                db_id = _data_source_id(bbdd)
                with lock:
                    _cache_lista_ca["db_id"] = db_id
                break
    if not db_id:
        return []
    try:
        nombres: set[str] = set()
        cursor = None
        while True:
            kwargs: dict = {"page_size": 100}
            if cursor:
                kwargs["start_cursor"] = cursor
            resp = _query_bbdd(db_id, **kwargs)
            for fila in resp.get("results", []):
                props = fila.get("properties", {})
                for col_name, prop_val in props.items():
                    if not re.match(r'^A\d+$', col_name):
                        continue
                    nombre_a = "".join(
                        p.get("plain_text", "")
                        for p in (prop_val.get("rich_text") or prop_val.get("title") or [])
                    ).strip()
                    if nombre_a:
                        nombres.add(nombre_a)
            if not resp.get("has_more"):
                break
            cursor = resp.get("next_cursor")
        return list(nombres)
    except Exception:
        logging.exception("Error obteniendo todos los advisees")
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
                opiniones.append({"fecha": fecha, "ca": ca_texto, "opinion": opinion, "resumen_advisee": resumen,
                                  "page_id": fila.get("id", ""), "url": fila.get("url", "")})
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
                opiniones.append({"fecha": fecha, "ca": ca_texto or ca_nombre, "opinion": opinion, "resumen_advisee": resumen,
                                  "page_id": fila.get("id", ""), "url": fila.get("url", "")})
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

    parent = _parent_bbdd_en_pagina(config.NOTION_TOSEE_PAGE_NAME, crear=False)
    if parent.get("type") != "page_id":
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

_cache_objetivos_persona: dict = {}  # cache_key -> db_id  (base destino para GUARDAR)
_cache_objetivos_ids: dict = {}      # cache_key -> (ts, list[db_id])  (todas las bases, para LEER)
# Las bases son estables → si encontramos alguna, cacheamos largo (el CONTENIDO se lee en
# vivo igualmente). Si NO hay ninguna, cacheamos corto para ver enseguida bases nuevas o
# restauradas de la papelera sin tener que reiniciar el backend.
_TTL_OBJ_POS = 600
_TTL_OBJ_NEG = 20


def _clave_objetivos(texto: str) -> str:
    """Clave de comparación para las bases 'Objetivos - X': insensible a mayúsculas,
    espacios y TILDES. Evita que 'Belén Hernández' y 'Belen Hernandez' se traten como
    personas distintas (y acaben leyendo/creando bases separadas)."""
    base = normalizar_nombre(texto)
    return "".join(c for c in unicodedata.normalize("NFD", base) if unicodedata.category(c) != "Mn")


def _buscar_todas_bbdd_objetivos_persona(nombre: str) -> list[str]:
    """Todos los db_id de bases 'Objetivos - {nombre}' bajo 'Objetivos empleados',
    comparando sin tildes ni mayúsculas. Devuelve varias si hay duplicados, para poder
    agregar sus objetivos en la lectura.

    Cachea la lista de db_id por persona (recorrer las bases es caro: ~1 llamada de
    listado + 1 retrieve por base). El CONTENIDO se sigue leyendo en vivo en
    obtener_objetivos_persona, así que un objetivo nuevo en una base ya conocida se ve
    al instante; solo si se crea una base nueva se actualiza este cache (en _obtener_o_crear)."""
    objetivo = _clave_objetivos(f"Objetivos - {nombre.strip()}")
    ahora = time.time()
    with lock:
        entry = _cache_objetivos_ids.get(objetivo)
    if entry is not None:
        ts, ids_cache = entry
        ttl = _TTL_OBJ_POS if ids_cache else _TTL_OBJ_NEG
        if ahora - ts < ttl:
            return ids_cache

    parent = _parent_bbdd_en_pagina("Objetivos empleados", crear=False)
    ids: list[str] = []
    if parent.get("type") == "page_id":
        for bloque in _iter_blocks(parent["page_id"]):
            if bloque.get("type") == "child_database" and _clave_objetivos(_titulo_child_database(bloque)) == objetivo:
                try:
                    ids.append(_data_source_id(notion.databases.retrieve(database_id=bloque["id"])))
                except Exception:
                    ids.append(bloque["id"])
    with lock:
        _cache_objetivos_ids[objetivo] = (time.time(), ids)
    return ids


def _obtener_o_crear_bbdd_objetivos_persona(nombre: str) -> str:
    nombre_strip = nombre.strip()
    titulo = f"Objetivos - {nombre_strip}"
    cache_key = _clave_objetivos(titulo)
    with lock:
        if cache_key in _cache_objetivos_persona:
            return _cache_objetivos_persona[cache_key]

    # Busca cualquier base existente (insensible a tildes/mayúsculas). Si hay
    # duplicados, usa la primera; la lectura las agrega todas de todos modos.
    existentes = _buscar_todas_bbdd_objetivos_persona(nombre_strip)
    if existentes:
        db_id = existentes[0]
        with lock:
            _cache_objetivos_persona[cache_key] = db_id
        return db_id

    # No existe todavía: crea la página contenedora "Objetivos empleados" (si hace falta) y la base
    parent = _parent_bbdd_en_pagina("Objetivos empleados", crear=True)

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
        # La lectura debe incluir la base recién creada sin re-listar todo Notion.
        _cache_objetivos_ids[cache_key] = (time.time(), [db_id])
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
        # Lee de TODAS las bases 'Objetivos - {nombre}' que casen (insensible a tildes),
        # agregando duplicados. NO crea ninguna base (leer no debe generar duplicados vacíos).
        db_ids = _buscar_todas_bbdd_objetivos_persona(advisee_nombre)
        resultados = []
        vistos: set = set()
        alguna_fallo = False
        for db_id in db_ids:
          try:
            cursor = None
            while True:
                kwargs: dict = {"page_size": 100}
                if cursor:
                    kwargs["start_cursor"] = cursor
                resp = _query_bbdd(db_id, **kwargs)
                for pagina in resp.get("results", []):
                    if pagina["id"] in vistos:
                        continue
                    vistos.add(pagina["id"])
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
          except Exception:
            alguna_fallo = True
            logging.exception("Error leyendo base de objetivos %s (¿borrada?)", db_id)
        if alguna_fallo:
            # Alguna base cacheada ya no existe (borrada/restaurada): invalida para re-listar.
            with lock:
                _cache_objetivos_ids.pop(_clave_objetivos(f"Objetivos - {advisee_nombre.strip()}"), None)
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
            if titulo in ("lista ca", "lista de cas") or titulo.startswith("lista ca") or titulo.startswith("lista de ca"):
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
    parent = _parent_bbdd_en_pagina(config.NOTION_ACTIVACIONES_PERMISOS_PAGE_NAME, crear=True)
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
    "Tipo": {"rich_text": {}},
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
    "item_1": 'Explicar cómo estás ayudando en _"Contribution to the firm"_',
    "item_2": "Cómo te estás acercando a tus objetivos",
    "item_3": "Recordar los criterios de evaluación",
    "item_4": "Solicitar apoyo en alguna área o informar sobre alguna dificultad.",
    "pregunta_tipo": "¿Sobre qué vas a querer hablar hoy?",
    "topic_cttf": "CTTF",
    "topic_objetivos": "Objetivos",
    "topic_dificultades": "Dificultades",
    "topic_trayectoria": "Trayectoria",
    "topic_otro": "Otro",
}

# Claves añadidas después del despliegue inicial: se siembran en BDs "Preguntas" ya existentes
# para que el admin pueda editarlas en Notion (una vez por proceso, respetando borrados manuales).
_CLAVES_PERSONAL_NUEVAS = (
    "pregunta_tipo", "topic_cttf", "topic_objetivos", "topic_dificultades", "topic_trayectoria",
    "topic_otro",
)

_mensaje_inicial_migrado: set = set()
_claves_personal_migradas: set = set()


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


def _asegurar_claves_personal(db_id: str) -> None:
    """Inserta en la BD de preguntas las claves nuevas (pregunta_tipo, topic_*) que falten,
    para que aparezcan en Notion y el admin pueda editarlas.

    Se hace una vez por proceso y solo inserta las que no existan; si el admin borra una a
    propósito, no se vuelve a crear en la misma ejecución."""
    if db_id in _claves_personal_migradas:
        return
    _claves_personal_migradas.add(db_id)
    try:
        existentes: set = set()
        cursor = None
        while True:
            kwargs: dict = {"page_size": 100}
            if cursor:
                kwargs["start_cursor"] = cursor
            resp = _query_bbdd(db_id, **kwargs)
            for fila in resp.get("results", []):
                clave = " ".join(
                    p.get("plain_text", "") for p in fila.get("properties", {}).get("Clave", {}).get("title", [])
                ).strip()
                if clave:
                    existentes.add(clave)
            if not resp.get("has_more"):
                break
            cursor = resp.get("next_cursor")

        creada = False
        for clave in _CLAVES_PERSONAL_NUEVAS:
            if clave in existentes:
                continue
            _crear_pagina_en_bbdd(db_id, {
                "Clave": {"title": [{"type": "text", "text": {"content": clave}}]},
                "Texto": {"rich_text": [{"type": "text", "text": {"content": PREGUNTAS_PERSONALES_DEFAULT[clave]}}]},
            })
            creada = True
        if creada:
            with lock:
                _cache_personal_preguntas.clear()
    except Exception:
        logging.exception("Error asegurando claves nuevas personales")


_NOMBRE_PREGUNTAS_EVAL_PERSONAL = "Preguntas evaluación personal"
_NOMBRE_RESPUESTAS_SEGUIMIENTO = "Resultados Seguimiento personal"

# Mapa: nombre DB antiguo → (nombre nuevo, función que devuelve el parent dict de la nueva ubicación)
_PERSONAL_DB_NUEVA_UBICACION: dict = {
    "Preguntas": (
        _NOMBRE_PREGUNTAS_EVAL_PERSONAL,
        lambda: _parent_bbdd_en_pagina(config.NOTION_PREGUNTAS_CHATBOT_PAGE_NAME, crear=True),
    ),
    "Respuestas": (
        _NOMBRE_RESPUESTAS_SEGUIMIENTO,
        lambda: _parent_bbdd_en_pagina(config.NOTION_RESULTADOS_EVAL_PAGE_NAME, crear=True),
    ),
}


def _obtener_o_crear_pagina_personales() -> str | None:
    with lock:
        page_id = _cache_personales_page_id["page_id"]
    if page_id:
        return page_id

    # Intentar encontrar la página antigua "Evaluaciones Personales" (aún puede existir)
    ref = _parent_bbdd_en_pagina("Evaluaciones Personales", crear=False)
    if ref.get("type") == "page_id":
        with lock:
            _cache_personales_page_id["page_id"] = ref["page_id"]
        return ref["page_id"]
    return None


def _buscar_bbdd_personal_en_nueva_ubicacion(titulo_db: str) -> str | None:
    """Busca la BD de evaluaciones personales en su nueva ubicación post-migración."""
    nueva_info = _PERSONAL_DB_NUEVA_UBICACION.get(titulo_db)
    if not nueva_info:
        return None
    nombre_nuevo, obtener_parent = nueva_info
    parent = obtener_parent()
    if parent.get("type") != "page_id":
        return None
    # Buscar la BD con nombre nuevo
    for nombre_buscar in (nombre_nuevo, titulo_db):
        objetivo = normalizar_nombre(nombre_buscar)
        for bloque in _iter_blocks(parent["page_id"]):
            if bloque.get("type") == "child_database" and normalizar_nombre(_titulo_child_database(bloque)) == objetivo:
                try:
                    return _data_source_id(notion.databases.retrieve(database_id=bloque["id"]))
                except Exception:
                    return bloque["id"]
    return None


def _buscar_o_crear_bbdd_en_personales(titulo_db: str, props: dict, cache: dict, poblar=None) -> str | None:
    with lock:
        db_id = cache["db_id"]
    if db_id:
        return db_id

    # 1. Buscar en nueva ubicación post-migración
    db_id = _buscar_bbdd_personal_en_nueva_ubicacion(titulo_db)
    if db_id:
        with lock:
            cache["db_id"] = db_id
        return db_id

    # 2. Buscar en ubicación antigua "Evaluaciones Personales"
    personales_id = _obtener_o_crear_pagina_personales()
    if personales_id:
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

    # 3. Crear en nueva ubicación si está disponible, si no en root
    nueva_info = _PERSONAL_DB_NUEVA_UBICACION.get(titulo_db)
    if nueva_info:
        titulo_crear, obtener_parent = nueva_info
        parent_crear = obtener_parent()
    else:
        titulo_crear = titulo_db
        parent_crear = _parent_bbdd_referencia()

    try:
        if _usa_data_sources():
            nueva = notion.databases.create(
                parent=parent_crear,
                title=[{"type": "text", "text": {"content": titulo_crear}}],
                initial_data_source={"title": [{"type": "text", "text": {"content": titulo_crear}}], "properties": props},
            )
            nueva = notion.databases.retrieve(database_id=nueva["id"])
        else:
            nueva = notion.databases.create(
                parent=parent_crear,
                title=[{"type": "text", "text": {"content": titulo_crear}}],
                properties=props,
            )
        db_id = _data_source_id(nueva)
        with lock:
            cache["db_id"] = db_id
        logging.info("BD '%s' creada", titulo_crear)
        if poblar:
            poblar(db_id)
        return db_id
    except Exception:
        logging.exception("Error creando BD '%s'", titulo_crear)
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


def obtener_preguntas_personales(idioma: str = "es", con_fallback_es: bool = True) -> dict:
    """Devuelve {clave: texto} de la evaluación personal en el idioma dado (cacheado 5 min).

    Filtra por la columna 'Idioma' (ES/EN) con fallback EN->ES por clave.
    Si con_fallback_es=False, devuelve SOLO las claves que existen en ese idioma en
    Notion (sin caer a ES); sirve para saber si una clave está realmente traducida."""
    import time as _time
    idioma = idioma if idioma in IDIOMAS_SOPORTADOS else "es"
    ahora = _time.time()
    cache_key = idioma if con_fallback_es else f"{idioma}|nofb"
    with lock:
        entry = _cache_personal_preguntas.get(cache_key)
    if entry and (ahora - entry["ts"]) < _PERSONAL_PREGUNTAS_TTL:
        return entry["data"]

    db_id = _buscar_o_crear_bbdd_en_personales(
        "Preguntas", _PROPS_PERSONAL_PREGUNTAS, _cache_personal_preguntas_db,
        poblar=_poblar_bbdd_preguntas_personal,
    )
    if not db_id:
        return dict(PREGUNTAS_PERSONALES_DEFAULT)

    _migrar_mensaje_inicial(db_id)
    _asegurar_claves_personal(db_id)

    try:
        mapas: dict = {}  # idioma -> {clave: texto}
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
                if not (clave and texto):
                    continue
                _lang = _normalizar_idioma(_texto_propiedad(props, "Idioma"))
                mapas.setdefault(_lang, {})[clave] = texto
            if not resp.get("has_more"):
                break
            cursor = resp.get("next_cursor")
        # Lo que hay en Notion manda: NO se re-siembran las claves por defecto (añadir/quitar surte efecto).
        es_map = mapas.get("es", {})
        base = mapas.get(idioma, {})
        if con_fallback_es:
            resultado = {c: (base.get(c) or es_map.get(c)) for c in (set(es_map) | set(base))}
        else:
            resultado = dict(base)
        with lock:
            _cache_personal_preguntas[cache_key] = {"data": resultado, "ts": _time.time()}
        return resultado
    except Exception:
        logging.exception("Error leyendo preguntas personales desde Notion")
        return dict(PREGUNTAS_PERSONALES_DEFAULT)


PREGUNTAS_CA_DEFAULT = {
    "opinion": "Añade a continuación tus opiniones/puntos a añadir sobre esta información.",
    "opinion_sin_claude": "¿Te gustaría opinar o comentar algo extra sobre la información disponible para hacer seguimiento de tu advisee?",
    "opinion_con_claude": "Añade a continuación tus opiniones/puntos a añadir sobre esta información.",
}

_NOMBRE_PREGUNTAS_CA = "Preguntas seguimiento CA"
_cache_ca_preguntas_db: dict = {"db_id": None}
_cache_ca_preguntas: dict = {}
_CA_PREGUNTAS_TTL = 300


def _poblar_bbdd_preguntas_ca(db_id: str) -> None:
    for clave, texto in PREGUNTAS_CA_DEFAULT.items():
        try:
            _crear_pagina_en_bbdd(db_id, {
                "Clave": {"title": [{"type": "text", "text": {"content": clave}}]},
                "Texto": {"rich_text": [{"type": "text", "text": {"content": texto}}]},
            })
        except Exception:
            logging.exception("Error poblando pregunta CA '%s'", clave)


def _obtener_o_crear_bbdd_preguntas_ca() -> str | None:
    with lock:
        db_id = _cache_ca_preguntas_db["db_id"]
    if db_id:
        return db_id

    parent = _parent_bbdd_en_pagina(config.NOTION_PREGUNTAS_CHATBOT_PAGE_NAME, crear=True)
    if parent.get("type") == "page_id":
        objetivo = normalizar_nombre(_NOMBRE_PREGUNTAS_CA)
        for bloque in _iter_blocks(parent["page_id"]):
            if bloque.get("type") == "child_database" and normalizar_nombre(_titulo_child_database(bloque)) == objetivo:
                try:
                    db_id = _data_source_id(notion.databases.retrieve(database_id=bloque["id"]))
                except Exception:
                    db_id = bloque["id"]
                with lock:
                    _cache_ca_preguntas_db["db_id"] = db_id
                return db_id

    props = {"Clave": {"title": {}}, "Texto": {"rich_text": {}}}
    try:
        if _usa_data_sources():
            nueva = notion.databases.create(
                parent=parent,
                title=[{"type": "text", "text": {"content": _NOMBRE_PREGUNTAS_CA}}],
                initial_data_source={"title": [{"type": "text", "text": {"content": _NOMBRE_PREGUNTAS_CA}}], "properties": props},
            )
            nueva = notion.databases.retrieve(database_id=nueva["id"])
        else:
            nueva = notion.databases.create(
                parent=parent,
                title=[{"type": "text", "text": {"content": _NOMBRE_PREGUNTAS_CA}}],
                properties=props,
            )
        db_id = _data_source_id(nueva)
        with lock:
            _cache_ca_preguntas_db["db_id"] = db_id
        logging.info("BD '%s' creada", _NOMBRE_PREGUNTAS_CA)
        _poblar_bbdd_preguntas_ca(db_id)
        return db_id
    except Exception:
        logging.exception("Error creando BD '%s'", _NOMBRE_PREGUNTAS_CA)
        return None


def obtener_preguntas_seguimiento_ca(idioma: str = "es") -> dict:
    """Devuelve {clave: texto} de seguimiento CA en el idioma dado (cacheado 5 min).

    Filtra por la columna 'Idioma' (ES/EN) con fallback EN->ES por clave."""
    import time as _time
    idioma = idioma if idioma in IDIOMAS_SOPORTADOS else "es"
    ahora = _time.time()
    with lock:
        entry = _cache_ca_preguntas.get(idioma)
    if entry and (ahora - entry["ts"]) < _CA_PREGUNTAS_TTL:
        return entry["data"]

    db_id = _obtener_o_crear_bbdd_preguntas_ca()
    if not db_id:
        return dict(PREGUNTAS_CA_DEFAULT)

    try:
        mapas: dict = {}  # idioma -> {clave: texto}
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
                if not (clave and texto):
                    continue
                _lang = _normalizar_idioma(_texto_propiedad(props, "Idioma"))
                mapas.setdefault(_lang, {})[clave] = texto
            if not resp.get("has_more"):
                break
            cursor = resp.get("next_cursor")
        es_map = mapas.get("es", {})
        base = mapas.get(idioma, {})
        resultado = {c: (base.get(c) or es_map.get(c)) for c in (set(es_map) | set(base))}
        # Garantiza que las 3 claves de flujo existan siempre (por seguridad).
        for k, v in PREGUNTAS_CA_DEFAULT.items():
            resultado.setdefault(k, v)
        with lock:
            _cache_ca_preguntas[idioma] = {"data": resultado, "ts": _time.time()}
        return resultado
    except Exception:
        logging.exception("Error leyendo preguntas CA desde Notion")
        return dict(PREGUNTAS_CA_DEFAULT)


# --- Seguimiento personal: una BD por persona ("Seg Personal - {Nombre}") ----
# Estructura (como las evaluaciones mensuales): página contenedora
# "Resultados Seguimiento personal" (bajo "Resultados Evaluaciones") con una BD por empleado.
_PREFIJO_SEG_PERSONAL = "Seg Personal - "
_NOMBRE_PAGINA_SEG_PERSONAL = "Resultados Seguimiento personal"
_cache_seg_personal_por_nombre: dict = {}   # titulo -> db_id
_cache_pagina_seg_personal: dict = {"page_id": None}


def _obtener_o_crear_pagina_seg_personal() -> str | None:
    """Devuelve el page_id de la página contenedora 'Resultados Seguimiento personal'
    (bajo 'Resultados Evaluaciones'); la crea si no existe."""
    with lock:
        if _cache_pagina_seg_personal["page_id"]:
            return _cache_pagina_seg_personal["page_id"]
    parent_res = _parent_bbdd_en_pagina(config.NOTION_RESULTADOS_EVAL_PAGE_NAME, crear=True)
    if parent_res.get("type") != "page_id":
        return None
    res_id = parent_res["page_id"]
    objetivo = normalizar_nombre(_NOMBRE_PAGINA_SEG_PERSONAL)
    page_id = None
    for bloque in _iter_blocks(res_id):
        if bloque.get("type") == "child_page" and normalizar_nombre((bloque.get("child_page") or {}).get("title", "")) == objetivo:
            page_id = bloque["id"]
            break
    if not page_id:
        pagina = notion.pages.create(
            parent={"type": "page_id", "page_id": res_id},
            properties={"title": {"title": [{"type": "text", "text": {"content": _NOMBRE_PAGINA_SEG_PERSONAL}}]}},
        )
        page_id = pagina["id"]
        logging.info("Página '%s' creada", _NOMBRE_PAGINA_SEG_PERSONAL)
    with lock:
        _cache_pagina_seg_personal["page_id"] = page_id
    return page_id


def obtener_o_crear_bbdd_seg_personal(nombre: str) -> str | None:
    """Busca/crea la BD 'Seg Personal - {nombre}' dentro de la página contenedora."""
    titulo = f"{_PREFIJO_SEG_PERSONAL}{' '.join((nombre or '').split()).strip() or 'Sin nombre'}"
    with lock:
        if titulo in _cache_seg_personal_por_nombre:
            return _cache_seg_personal_por_nombre[titulo]
    pagina_id = _obtener_o_crear_pagina_seg_personal()
    if not pagina_id:
        return None
    db_id = _buscar_bbdd_en_pagina_id(pagina_id, titulo)
    if not db_id:
        parent = {"type": "page_id", "page_id": pagina_id}
        try:
            if _usa_data_sources():
                nueva = notion.databases.create(
                    parent=parent,
                    title=[{"type": "text", "text": {"content": titulo}}],
                    initial_data_source={"title": [{"type": "text", "text": {"content": titulo}}], "properties": _PROPS_EVALUACIONES_PERSONALES},
                )
                nueva = notion.databases.retrieve(database_id=nueva["id"])
            else:
                nueva = notion.databases.create(
                    parent=parent,
                    title=[{"type": "text", "text": {"content": titulo}}],
                    properties=_PROPS_EVALUACIONES_PERSONALES,
                )
            db_id = _data_source_id(nueva)
            logging.info("BD '%s' creada", titulo)
        except Exception:
            logging.exception("Error creando BD de seguimiento personal '%s'", titulo)
            return None
    with lock:
        _cache_seg_personal_por_nombre[titulo] = db_id
    return db_id


def _asegurar_propiedades_seg_personal(db_id: str) -> None:
    """Añade a la BD por persona las columnas que falten (p.ej. 'Tipo' en BDs antiguas)."""
    try:
        if _usa_data_sources():
            bbdd = notion.data_sources.retrieve(data_source_id=db_id)
            faltantes = {k: v for k, v in _PROPS_EVALUACIONES_PERSONALES.items() if k not in bbdd.get("properties", {})}
            if faltantes:
                notion.data_sources.update(data_source_id=db_id, properties=faltantes)
        else:
            bbdd = notion.databases.retrieve(database_id=db_id)
            faltantes = {k: v for k, v in _PROPS_EVALUACIONES_PERSONALES.items() if k not in bbdd.get("properties", {})}
            if faltantes:
                notion.databases.update(database_id=db_id, properties=faltantes)
    except Exception:
        logging.exception("Error asegurando propiedades de BD seg personal %s", db_id)


def guardar_evaluacion_personal(nombre: str, respuestas: dict) -> bool:
    try:
        db_id = obtener_o_crear_bbdd_seg_personal(nombre)
    except Exception:
        logging.exception("Error localizando BD de evaluaciones personales para '%s'", nombre)
        return False
    if not db_id:
        return False
    try:
        from datetime import datetime, timezone
        _asegurar_propiedades_seg_personal(db_id)
        fecha_iso = datetime.now(timezone.utc).isoformat()
        props = {
            "Nombre": {"title": [{"type": "text", "text": {"content": nombre or ""}}]},
            "Fecha": {"date": {"start": fecha_iso}},
            "Tipo": {"rich_text": [{"type": "text", "text": {"content": respuestas.get("tipo", "") or ""}}]},
            "Comentario": {"rich_text": [{"type": "text", "text": {"content": respuestas.get("comentario", "") or ""}}]},
        }
        _crear_pagina_en_bbdd(db_id, props)
        logging.info("Evaluación personal guardada para '%s'", nombre)
        return True
    except Exception:
        logging.exception("Error guardando evaluación personal de '%s'", nombre)
        return False


_cache_calendario_db: dict = {"db_id": None}
# Lock dedicado: se mantiene durante TODA la búsqueda/creación para que dos hilos
# concurrentes (ca/personal/... al arrancar) no creen 'Calendario evaluaciones' duplicados.
_lock_calendario = threading.Lock()

_PROPS_CALENDARIO = {
    "Nombre": {"title": {}},
    "Fecha inicio": {"date": {}},
}


def _obtener_o_crear_bbdd_calendario() -> str | None:
    with _lock_calendario:
        if _cache_calendario_db["db_id"]:
            return _cache_calendario_db["db_id"]

        # Parent previsto: "Datos a Monitorizar" si existe, si no la página raíz.
        parent = _parent_bbdd_en_pagina(config.NOTION_DATA_LISTS_PAGE_NAME, crear=False)
        if parent.get("type") != "page_id":
            parent = _parent_bbdd_referencia()
        parent_id = parent.get("page_id")

        # 1) Buscar como hija del parent: children.list es consistente al instante, así que
        #    evita duplicar por el lag de indexación de notion.search (incluso tras reiniciar).
        db_id = None
        if parent_id:
            db_id = _buscar_bbdd_en_pagina_id(parent_id, "Calendario evaluaciones")

        # 2) Fallback: búsqueda global por si la BD está en otra ubicación.
        if not db_id:
            try:
                res = notion.search(
                    query="Calendario evaluaciones",
                    filter={"value": _tipo_objeto_busqueda_bbdd(), "property": "object"},
                    page_size=10,
                )
                for bbdd in res.get("results", []):
                    if normalizar_nombre(_extraer_titulo_bbdd(bbdd)) == "calendario evaluaciones":
                        db_id = _data_source_id(bbdd)
                        break
            except Exception:
                pass

        # 3) Crear solo si de verdad no existe.
        if not db_id:
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
            except Exception:
                logging.exception("Error creando 'Calendario evaluaciones' en Notion")
                return None

        _cache_calendario_db["db_id"] = db_id
        return db_id


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
    """Devuelve los comentarios de seguimiento personal de 'nombre' (su propia BD 'Seg Personal - ...')."""
    db_id = obtener_o_crear_bbdd_seg_personal(nombre)
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
                    resultados.append({"autor": autor, "fecha": fecha, "comentario": comentario,
                                       "page_id": fila.get("id", ""), "url": fila.get("url", "")})
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
