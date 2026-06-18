import logging
import os
import re
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
        "Persona evaluada": {"rich_text": {}},
        "Persona que evalua": {"rich_text": {}},
        "Evaluador": {"rich_text": {}},
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
                "Evaluador": {"rich_text": [{"text": {"content": nombre}}]},
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


def _obtener_registros_empleados() -> list[dict]:
    """Lee empleados con su nombre canonico y aliases utiles para busqueda."""
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
                registros.append({"nombre": nombre, "email": email.strip(), "aliases": aliases})
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
        return registros
    except Exception:
        logging.exception("Error leyendo la lista de empleados desde Notion")
        return []


def obtener_lista_empleados() -> list[str]:
    """Lee los nombres canonicos de empleados desde Notion."""
    return [registro["nombre"] for registro in _obtener_registros_empleados()]


def obtener_registros_empleados() -> list[dict]:
    """Lee empleados con nombre, email y aliases desde Notion."""
    return _obtener_registros_empleados()


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


def obtener_advisees(ca_nombre: str) -> list[str]:
    """Retorna los advisees de un CA desde 'Lista CA' (columna CA y columnas A1, A2, ...)."""
    ca_norm = normalizar_nombre(ca_nombre)
    logging.info(f"[advisees] Buscando advisees para CA: '{ca_nombre}' (norm: '{ca_norm}')")
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
                if normalizar_nombre(nombre_ca) != ca_norm:
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


def obtener_opiniones_ca_por_advisee(ca_nombre: str, advisee: str) -> list[dict]:
    """Retorna las opiniones guardadas por el CA sobre el advisee, ordenadas por fecha desc."""
    titulo = f"Opiniones CA - {ca_nombre.strip()}"
    advisee_norm = normalizar_nombre(advisee)
    try:
        resultado = notion.search(
            query=titulo,
            filter={"value": _tipo_objeto_busqueda_bbdd(), "property": "object"},
            page_size=10,
        )
        db_id = None
        for bbdd in resultado.get("results", []):
            if _extraer_titulo_bbdd(bbdd) == titulo:
                db_id = _data_source_id(bbdd)
                break
        if not db_id:
            return []
        opiniones = []
        cursor = None
        while True:
            kwargs: dict = {"page_size": 100}
            if cursor:
                kwargs["start_cursor"] = cursor
            resp = _query_bbdd(db_id, **kwargs)
            for fila in resp.get("results", []):
                props = fila.get("properties", {})
                advisee_texto = "".join(
                    p.get("plain_text", "")
                    for p in props.get("Advisee", {}).get("rich_text", [])
                ).strip()
                if normalizar_nombre(advisee_texto) != advisee_norm:
                    continue
                opinion = "".join(
                    p.get("plain_text", "")
                    for p in props.get("Opinion", {}).get("rich_text", [])
                ).strip()
                resumen = "".join(
                    p.get("plain_text", "")
                    for p in props.get("Resumen_advisee", {}).get("rich_text", [])
                ).strip()
                fecha = (props.get("Fecha", {}).get("date") or {}).get("start", "")
                opiniones.append({"fecha": fecha, "opinion": opinion, "resumen_advisee": resumen})
            if not resp.get("has_more"):
                break
            cursor = resp.get("next_cursor")
        return sorted(opiniones, key=lambda x: x.get("fecha", ""), reverse=True)
    except Exception:
        logging.exception(f"Error obteniendo opiniones de '{ca_nombre}' sobre '{advisee}'")
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
