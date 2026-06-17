import logging
import os
import re
from datetime import datetime, timezone

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


def _propiedades_bbdd_evaluaciones():
    return {
        "Name": {"title": {}},
        "Persona evaluada": {"rich_text": {}},
        "Persona que evalua": {"rich_text": {}},
        "Proyecto": {"rich_text": {}},
        "Satisfaccion": {"rich_text": {}},
        "Mejor aspecto": {"rich_text": {}},
        "Peor aspecto": {"rich_text": {}},
        "Fecha": {"date": {}},
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


def obtener_parent_bbdd_evaluados():
    try:
        return _parent_bbdd_referencia()
    except RuntimeError as error:
        logging.warning(error)
        return None


def obtener_o_crear_bbdd_evaluado(nombre_evaluado):
    nombre_limpio = " ".join(nombre_evaluado.split()).strip() or "Sin nombre"
    titulo = _titulo_bbdd(nombre_limpio)
    with lock:
        cacheada = bbdd_por_evaluado.get(titulo)
    if cacheada:
        return cacheada

    parent = _parent_bbdd_referencia()
    resultado = notion.search(query=titulo, filter={"value": _tipo_objeto_busqueda_bbdd(), "property": "object"}, page_size=100)
    for bbdd in resultado.get("results", []):
        if _extraer_titulo_bbdd(bbdd) == titulo and _coincide_parent_bbdd(bbdd, parent):
            database_id = _data_source_id(bbdd)
            asegurar_propiedades_bbdd(database_id)
            with lock:
                bbdd_por_evaluado[titulo] = database_id
            return database_id

    if _usa_data_sources():
        nueva = notion.databases.create(
            parent=parent,
            title=[{"type": "text", "text": {"content": titulo}}],
            initial_data_source={"title": [{"type": "text", "text": {"content": titulo}}], "properties": _propiedades_bbdd_evaluaciones()},
        )
        nueva = notion.databases.retrieve(database_id=nueva["id"])
    else:
        nueva = notion.databases.create(parent=parent, title=[{"type": "text", "text": {"content": titulo}}], properties=_propiedades_bbdd_evaluaciones())

    database_id = _data_source_id(nueva)
    asegurar_propiedades_bbdd(database_id)
    with lock:
        bbdd_por_evaluado[titulo] = database_id
    logging.info(f"Base de datos creada en Notion: {titulo}")
    return database_id


def guardar_en_notion(nombre, respuestas):
    nombre_evaluado = respuestas.get("evaluado", "").strip()
    proyecto = respuestas.get("proyecto", "").strip()
    try:
        database_id = obtener_o_crear_bbdd_evaluado(nombre_evaluado)
        asegurar_propiedades_bbdd(database_id)
        _crear_pagina_en_bbdd(
            database_id,
            {
                "Name": {"title": [{"text": {"content": nombre}}]},
                "Persona evaluada": {"rich_text": [{"text": {"content": nombre_evaluado}}]},
                "Persona que evalua": {"rich_text": [{"text": {"content": nombre}}]},
                "Proyecto": {"rich_text": [{"text": {"content": proyecto}}]},
                "Satisfaccion": {"rich_text": [{"text": {"content": respuestas.get("satisfaccion", "")}}]},
                "Mejor aspecto": {"rich_text": [{"text": {"content": respuestas.get("mejor_aspecto", "")}}]},
                "Peor aspecto": {"rich_text": [{"text": {"content": respuestas.get("peor_aspecto", "")}}]},
                "Fecha": {"date": {"start": datetime.now(timezone.utc).isoformat()}},
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


def obtener_lista_empleados() -> list[str]:
    """Lee la lista de empleados desde la base de datos configurada en Notion.

    Se intenta leer la propiedad 'Name' (título) y, si no existe, se usa la
    propiedad 'Empleado' o 'Nombre'.
    """
    try:
        db_id = config.NOTION_DATABASE_ID
        resultado = notion.databases.retrieve(database_id=db_id)
        propiedades = resultado.get("properties", {})
        nombre_prop = None
        for candidato in ("Name", "Empleado", "Nombre", "Employee", "Employee Name"):
            if candidato in propiedades:
                nombre_prop = candidato
                break
        if nombre_prop is None:
            logging.warning("No se encontró una propiedad de nombre para la lista de empleados en Notion.")
            return []

        empleados = []
        cursor = None
        while True:
            kwargs = {"page_size": 100}
            if cursor:
                kwargs["start_cursor"] = cursor
            resp = notion.databases.query(database_id=db_id, **kwargs)
            for pagina in resp.get("results", []):
                props = pagina.get("properties", {})
                if nombre_prop == "Name":
                    valor = _texto_title(props, nombre_prop)
                else:
                    valor = _texto_rich_text(props, nombre_prop)
                if valor:
                    empleados.append(valor.strip())
            if not resp.get("has_more"):
                break
            cursor = resp.get("next_cursor")
        return empleados
    except Exception:
        logging.exception("Error leyendo la lista de empleados desde Notion")
        return []


def validar_empleado_en_lista(nombre: str) -> bool:
    """Comprueba si un nombre coincide con algún empleado de la lista de Notion."""
    nombre_limpio = normalizar_nombre(nombre)
    if not nombre_limpio:
        return False
    empleados = obtener_lista_empleados()
    nombres = {normalizar_nombre(e) for e in empleados if e}
    return nombre_limpio in nombres


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
                nombre = titulo_items[0]["text"]["content"] if titulo_items else "Desconocido"
                fecha = (props.get("Fecha", {}).get("date") or {}).get("start", "")
                evaluaciones.append({
                    "nombre": nombre,
                    "evaluado": evaluado,
                    "persona_evaluada": _texto_rich_text(props, "Persona evaluada") or evaluado,
                    "persona_que_evalua": _texto_rich_text(props, "Persona que evalua") or nombre,
                    "proyecto": _texto_rich_text(props, "Proyecto"),
                    "satisfaccion": _texto_rich_text(props, "Satisfaccion"),
                    "mejor_aspecto": _texto_rich_text(props, "Mejor aspecto"),
                    "peor_aspecto": _texto_rich_text(props, "Peor aspecto"),
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
    if not evaluado or evaluado == "__todas__":
        return obtener_evaluaciones()
    for bbdd in listar_bbdd_evaluados():
        if bbdd["evaluado"] == evaluado:
            return obtener_evaluaciones_de_bbdd(bbdd["id"], bbdd["evaluado"])
    raise RuntimeError(f"No se encontró una tabla de evaluaciones para {evaluado}.")
