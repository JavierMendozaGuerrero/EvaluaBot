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


def _extraer_titulo_pagina(pagina):
    for propiedad in pagina.get("properties", {}).values():
        if propiedad.get("type") == "title":
            return " ".join(item.get("plain_text", "") for item in propiedad.get("title", [])).strip()
    return ""


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
    if tipo == "formula":
        formula = propiedad.get("formula") or {}
        if formula.get("type") == "string":
            return (formula.get("string") or "").strip()
    return ""


def obtener_lista_empleados() -> list[str]:
    """Lee la lista de empleados desde la base de datos configurada en Notion."""
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

        empleados = []
        cursor = None
        while True:
            kwargs = {"page_size": 100}
            if cursor:
                kwargs["start_cursor"] = cursor
            resp = _query_bbdd(db_id, **kwargs)
            for pagina in resp.get("results", []):
                props = pagina.get("properties", {})
                for nombre_prop in nombre_props:
                    valor = _texto_propiedad(props, nombre_prop)
                    if valor:
                        empleados.append(valor.strip())
                        break
            if not resp.get("has_more"):
                break
            cursor = resp.get("next_cursor")
        logging.info(
            "Lista de empleados leida desde Notion '%s' (%s): %s nombres. Columnas usadas: %s",
            _extraer_titulo_bbdd(resultado) or "sin titulo",
            db_id,
            len(empleados),
            ", ".join(nombre_props),
        )
        return empleados
    except Exception:
        logging.exception("Error leyendo la lista de empleados desde Notion")
        return []


def _tokens_nombre(nombre):
    return {token for token in normalizar_nombre(nombre).split() if len(token) > 1}


def buscar_empleado_en_lista(nombre: str):
    """Devuelve el nombre de la lista que coincide con el texto recibido."""
    nombre_limpio = normalizar_nombre(nombre)
    if not nombre_limpio:
        return None
    empleados = obtener_lista_empleados()
    tokens_buscados = _tokens_nombre(nombre)
    for empleado in empleados:
        empleado_limpio = normalizar_nombre(empleado)
        if nombre_limpio == empleado_limpio:
            return empleado
        tokens_empleado = _tokens_nombre(empleado)
        if tokens_buscados and tokens_buscados.issubset(tokens_empleado):
            return empleado
    logging.info("Empleado no encontrado en la lista de Notion: %s", nombre)
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
