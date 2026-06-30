"""
Evaluaciones por proyecto — lógica de negocio y operaciones Notion.

Estructura en Notion:
  Listas de datos/
    Evaluaciones Proyectos/
      Activaciones Evaluaciones Proyectos  (BD)
      Autoevaluacion (BD de preguntas)
      Evaluacion Mismos Miembros (BD de preguntas)
      Evaluacion Miembros a Manager (BD de preguntas)
      Evaluacion Manager a Miembros (BD de preguntas)

  Evaluaciones por proyecto/
    {AÑO_EMPRESA_NOMBRE}/   (subpágina por proyecto)
      {NombrePersona}/      (subpágina por persona evaluada)
        (bloques con los resultados de cada evaluación)
"""

import logging
import threading
import time
from datetime import datetime, timezone

from . import config
from .clients import notion, slack_app
from .notion_service import (
    _buscar_bbdd_en_pagina_id,
    _data_source_id,
    _iter_blocks,
    _page_or_database_link_by_name,
    _parent_bbdd_en_pagina,
    _parent_bbdd_referencia,
    _query_bbdd,
    _titulo_child_page,
    _usa_data_sources,
    obtener_registros_empleados,
)
from .utils import normalizar_nombre

# ---------------------------------------------------------------------------
# Constantes de nombres Notion
# ---------------------------------------------------------------------------

_NOMBRE_SUBPAGINA_EVAL_PROYECTOS = "Evaluacion al finalizar proyecto"
_NOMBRE_SUBPAGINA_EVAL_PROYECTOS_ANTIGUO = "Evaluaciones Proyectos"
_NOMBRE_PAGINA_RAIZ_RESULTADOS = "Evaluaciones por proyecto"
_NOMBRE_BBDD_ACTIVACIONES = "Acceso Evaluaciones Proyecto"
_NOMBRE_BBDD_ACTIVACIONES_ANTIGUO = "Activaciones Evaluaciones Proyectos"

TIPOS_EVALUACION = {
    "autoevaluacion": "Autoevaluacion",
    "mismos_miembros": "Evaluacion Mismos Miembros",
    "miembros_a_manager": "Evaluacion Miembros a Manager",
    "manager_a_miembros": "Evaluacion Manager a Miembros",
}

LABELS_TIPOS = {
    "autoevaluacion": "Autoevaluación",
    "mismos_miembros": "Evaluación a tus miembros del equipo del mismo nivel",
    "miembros_a_manager": "Evaluación de miembros del equipo a managers",
    "manager_a_miembros": "Evaluación de managers a miembros del equipo",
}

# ---------------------------------------------------------------------------
# Preguntas iniciales por tipo
# ---------------------------------------------------------------------------

_PREGUNTAS_INICIALES = {
    "autoevaluacion": [
        {"categoria": "", "texto": "Grado de satisfacción contigo mismo", "tipo": "escala_1_5", "opciones": "", "orden": 1},
        {"categoria": "", "texto": "Justifica tu respuesta", "tipo": "abierta", "opciones": "", "orden": 2},
    ],
    "mismos_miembros": [
        {"categoria": "", "texto": "Grado de satisfacción con tu equipo", "tipo": "escala_1_5", "opciones": "", "orden": 1},
        {"categoria": "", "texto": "Justifica tu respuesta", "tipo": "abierta", "opciones": "", "orden": 2},
    ],
    "miembros_a_manager": [
        {"categoria": "GESTIÓN DE PROYECTO", "texto": "¿Se han definido con claridad los objetivos del proyecto y la estrategia necesaria para conseguirlos; marcando tiempos, identificando barreras, días clave, hitos. etc.?", "tipo": "escala_1_5", "opciones": "", "orden": 1},
        {"categoria": "GESTIÓN DE PROYECTO", "texto": "¿Ejerce una buena gestión de los tiempos durante la organización del proyecto?", "tipo": "escala_1_5", "opciones": "", "orden": 2},
        {"categoria": "GESTIÓN DE PROYECTO", "texto": "¿Prevé la organización y los posibles riesgos del proyecto, siendo capaz de priorizar tareas con el equipo que estratégicamente tengan sentido?", "tipo": "escala_1_5", "opciones": "", "orden": 3},
        {"categoria": "GESTIÓN DE PROYECTO", "texto": "¿Existe una previsión adecuada que posibilitaba cuidar el worklife balance?", "tipo": "escala_1_5", "opciones": "", "orden": 4},
        {"categoria": "GESTIÓN DE PROYECTO", "texto": "¿Es capaz de gestionar situaciones de conflicto en el equipo con solvencia y tomar responsabilidad al respecto?", "tipo": "escala_1_5", "opciones": "", "orden": 5},
        {"categoria": "GESTIÓN DE PROYECTO", "texto": "Añadir comentarios que aporten información", "tipo": "abierta", "opciones": "", "orden": 6},
        {"categoria": "CALIDAD TÉCNICA", "texto": "¿Ha marcado correctamente el nivel de conocimiento técnico que el equipo debía adquirir para ser solventes en el desempeño del proyecto? Por ejemplo, ha hecho una inmersión rápida para entender el sector del cliente", "tipo": "escala_1_5", "opciones": "", "orden": 7},
        {"categoria": "CALIDAD TÉCNICA", "texto": "Añadir comentarios que aporten información", "tipo": "abierta", "opciones": "", "orden": 8},
        {"categoria": "TRABAJO EN EQUIPO", "texto": "¿Conoce bien el rol de cada miembro de su equipo y reparte las tareas en función de capacidades y seniority? (Teniendo en cuenta las responsabilidades que te corresponden por tu posición)", "tipo": "escala_1_5", "opciones": "", "orden": 9},
        {"categoria": "TRABAJO EN EQUIPO", "texto": "Justifica tu respuesta anterior con ejemplos", "tipo": "abierta", "opciones": "", "orden": 10},
        {"categoria": "TRABAJO EN EQUIPO", "texto": "¿Ha mantenido una comunicación eficaz, permitiéndote entender la razón por la que se han tomado algunas decisiones?", "tipo": "escala_1_5", "opciones": "", "orden": 11},
        {"categoria": "TRABAJO EN EQUIPO", "texto": "Justifica tu respuesta anterior con ejemplos", "tipo": "abierta", "opciones": "", "orden": 12},
        {"categoria": "LIDERAZGO", "texto": "¿Ha hecho un seguimiento y te ha dado feedback constante durante el proyecto de forma constructiva, apoyando así tu evolución?", "tipo": "escala_1_5", "opciones": "", "orden": 13},
        {"categoria": "LIDERAZGO", "texto": "Justifica tu respuesta anterior con ejemplos", "tipo": "abierta", "opciones": "", "orden": 14},
        {"categoria": "LIDERAZGO", "texto": "¿Establece un entorno de trabajo adecuado para construir soluciones donde todos puedan contribuir?", "tipo": "escala_1_5", "opciones": "", "orden": 15},
        {"categoria": "LIDERAZGO", "texto": "Justifica tu respuesta anterior con ejemplos", "tipo": "abierta", "opciones": "", "orden": 16},
        {"categoria": "LIDERAZGO", "texto": "¿Es capaz de mantener una buena actitud transmitiendo entusiasmo y haciendo por filtrar entre las expectativas del cliente (más o menos demandante) y el buen rumbo?", "tipo": "escala_1_5", "opciones": "", "orden": 17},
        {"categoria": "LIDERAZGO", "texto": "¿Es alguien del que has podido aprender no sólo técnicamente sino también como un ejemplo inspiracional?", "tipo": "escala_1_5", "opciones": "", "orden": 18},
        {"categoria": "LIDERAZGO", "texto": "Añadir comentarios que aporten información", "tipo": "abierta", "opciones": "", "orden": 19},
    ],
    "manager_a_miembros": [
        {"categoria": "Gestión del proyecto", "texto": "Gestión del proyecto", "tipo": "radio_3", "opciones": "Exceeds|Achieves|Expects more", "orden": 1},
        {"categoria": "Calidad técnica", "texto": "Calidad técnica", "tipo": "radio_3", "opciones": "Exceeds|Achieves|Expects more", "orden": 2},
        {"categoria": "Trabajo en equipo", "texto": "Trabajo en equipo", "tipo": "radio_3", "opciones": "Exceeds|Achieves|Expects more", "orden": 3},
        {"categoria": "Comunicación", "texto": "Comunicación", "tipo": "radio_3", "opciones": "Exceeds|Achieves|Expects more", "orden": 4},
        {"categoria": "Relación con cliente", "texto": "Relación con cliente", "tipo": "radio_3", "opciones": "Exceeds|Achieves|Expects more", "orden": 5},
        {"categoria": "", "texto": "Describe cómo ha sido el desempeño del empleado a lo largo del proyecto. Justifica todos los criterios de evaluación previamente rellenados", "tipo": "abierta", "opciones": "", "orden": 6},
    ],
}

# ---------------------------------------------------------------------------
# Propiedades de las BDs de preguntas
# ---------------------------------------------------------------------------

def _props_bbdd_preguntas_proyecto():
    return {
        "Texto": {"title": {}},
        "Categoria": {"rich_text": {}},
        "Tipo": {"select": {"options": [
            {"name": "escala_1_5"},
            {"name": "radio_3"},
            {"name": "abierta"},
        ]}},
        "Opciones": {"rich_text": {}},
        "Orden": {"number": {"format": "number"}},
    }


def _props_bbdd_activaciones():
    return {
        "Empleado": {"title": {}},
        "Proyecto": {"rich_text": {}},
        "Activado_por": {"rich_text": {}},
        "Activo": {"checkbox": {}},
    }

# ---------------------------------------------------------------------------
# Caches
# ---------------------------------------------------------------------------

_lock_subpagina = threading.Lock()
_cache_subpagina_id: dict = {"page_id": None}

_lock_raiz_resultados = threading.Lock()
_cache_raiz_resultados_id: dict = {"page_id": None}

_lock_activaciones = threading.Lock()
_cache_activaciones_id: dict = {"db_id": None}

_lock_preguntas_proyecto: dict = {}
_cache_preguntas_proyecto: dict = {}
_CACHE_TTL = 300

_lock_bbdd_evaluacion = threading.Lock()
_cache_bbdd_evaluacion: dict = {}  # persona_page_id -> db_id

_NOMBRE_BBDD_EVALUACION = "Evaluaciones"
_PROPS_EVALUACION_PROYECTO = {
    "Name": {"title": {}},
    "Fecha": {"date": {}},
    "Tipo": {"select": {}},
    "Evaluador": {"rich_text": {}},
    "Respuestas": {"rich_text": {}},
}


# ---------------------------------------------------------------------------
# Helpers internos de Notion
# ---------------------------------------------------------------------------

def _crear_bbdd(parent_page_id: str, titulo: str, props: dict) -> str:
    if _usa_data_sources():
        nueva = notion.databases.create(
            parent={"type": "page_id", "page_id": parent_page_id},
            title=[{"type": "text", "text": {"content": titulo}}],
            initial_data_source={
                "title": [{"type": "text", "text": {"content": titulo}}],
                "properties": props,
            },
        )
        nueva = notion.databases.retrieve(database_id=nueva["id"])
    else:
        nueva = notion.databases.create(
            parent={"type": "page_id", "page_id": parent_page_id},
            title=[{"type": "text", "text": {"content": titulo}}],
            properties=props,
        )
    return _data_source_id(nueva)


def _crear_pagina_en_bbdd(database_id: str, properties: dict) -> dict:
    parent = {"data_source_id": database_id} if _usa_data_sources() else {"database_id": database_id}
    return notion.pages.create(parent=parent, properties=properties)


def _crear_subpagina(parent_page_id: str, titulo: str) -> str:
    pagina = notion.pages.create(
        parent={"type": "page_id", "page_id": parent_page_id},
        properties={"title": {"title": [{"type": "text", "text": {"content": titulo}}]}},
    )
    return pagina["id"]


def _buscar_child_page_id(parent_id: str, nombre: str) -> str | None:
    objetivo = normalizar_nombre(nombre)
    for bloque in _iter_blocks(parent_id):
        if bloque.get("type") == "child_page":
            if normalizar_nombre(_titulo_child_page(bloque)) == objetivo:
                return bloque["id"]
    return None


def _obtener_o_crear_bbdd_evaluacion_proyecto(persona_page_id: str) -> str | None:
    with _lock_bbdd_evaluacion:
        if persona_page_id in _cache_bbdd_evaluacion:
            return _cache_bbdd_evaluacion[persona_page_id]
    db_id = _buscar_bbdd_en_pagina_id(persona_page_id, _NOMBRE_BBDD_EVALUACION)
    if not db_id:
        try:
            db_id = _crear_bbdd(persona_page_id, _NOMBRE_BBDD_EVALUACION, _PROPS_EVALUACION_PROYECTO)
        except Exception:
            logging.exception("Error creando BD evaluación en página '%s'", persona_page_id)
            return None
    with _lock_bbdd_evaluacion:
        _cache_bbdd_evaluacion[persona_page_id] = db_id
    return db_id


def _formatear_respuestas(preguntas: list, respuestas: dict) -> str:
    lineas = []
    for p in preguntas:
        pid = p.get("id", "")
        texto = p.get("texto", "")
        respuesta = respuestas.get(pid, "—")
        lineas.append(f"{texto}: {respuesta}")
    return "\n".join(lineas)


# ---------------------------------------------------------------------------
# Obtener o crear subpágina "Evaluaciones Proyectos" dentro de "Listas de datos"
# ---------------------------------------------------------------------------

def _obtener_o_crear_subpagina_evaluaciones_proyectos() -> str | None:
    with _lock_subpagina:
        cached = _cache_subpagina_id["page_id"]
    if cached:
        return cached

    # Buscar en nueva ubicación (Datos opcionalmente modificables) primero, luego en antigua
    page_id = None
    for nombre_contenedor in (config.NOTION_DATA_MODIFICABLES_PAGE_NAME, config.NOTION_DATA_LISTS_PAGE_NAME):
        contenedor_parent = _parent_bbdd_en_pagina(nombre_contenedor, crear=False)
        if contenedor_parent.get("type") != "page_id":
            continue
        contenedor_id = contenedor_parent["page_id"]
        # Buscar por nombre nuevo y antiguo
        for nombre_sub in (_NOMBRE_SUBPAGINA_EVAL_PROYECTOS, _NOMBRE_SUBPAGINA_EVAL_PROYECTOS_ANTIGUO):
            page_id = _page_or_database_link_by_name(contenedor_id, nombre_sub)
            if page_id:
                break
        if page_id:
            break

    if not page_id:
        # Crear bajo "Datos opcionalmente modificables" si existe, si no bajo Datos a Monitorizar
        for nombre_crear_en in (config.NOTION_DATA_MODIFICABLES_PAGE_NAME, config.NOTION_DATA_LISTS_PAGE_NAME):
            parent_crear = _parent_bbdd_en_pagina(nombre_crear_en, crear=True)
            if parent_crear.get("type") == "page_id":
                try:
                    page_id = _crear_subpagina(parent_crear["page_id"], _NOMBRE_SUBPAGINA_EVAL_PROYECTOS)
                    logging.info("Sub-página '%s' creada en '%s'", _NOMBRE_SUBPAGINA_EVAL_PROYECTOS, nombre_crear_en)
                    break
                except Exception:
                    logging.exception("Error creando sub-página '%s'", _NOMBRE_SUBPAGINA_EVAL_PROYECTOS)
        if not page_id:
            return None

    with _lock_subpagina:
        _cache_subpagina_id["page_id"] = page_id
    return page_id


# ---------------------------------------------------------------------------
# Obtener o crear página raíz "Evaluaciones por proyecto"
# ---------------------------------------------------------------------------

def _obtener_o_crear_pagina_raiz_resultados() -> str | None:
    with _lock_raiz_resultados:
        cached = _cache_raiz_resultados_id["page_id"]
    if cached:
        return cached

    parent_raiz = _parent_bbdd_referencia()
    parent_page_id = parent_raiz["page_id"]

    page_id = _page_or_database_link_by_name(parent_page_id, _NOMBRE_PAGINA_RAIZ_RESULTADOS)
    if not page_id:
        try:
            page_id = _crear_subpagina(parent_page_id, _NOMBRE_PAGINA_RAIZ_RESULTADOS)
            logging.info("Página raíz '%s' creada en Notion", _NOMBRE_PAGINA_RAIZ_RESULTADOS)
        except Exception:
            logging.exception("Error creando página raíz '%s'", _NOMBRE_PAGINA_RAIZ_RESULTADOS)
            return None

    with _lock_raiz_resultados:
        _cache_raiz_resultados_id["page_id"] = page_id
    return page_id


# ---------------------------------------------------------------------------
# BD de activaciones
# ---------------------------------------------------------------------------

def _obtener_o_crear_bbdd_activaciones() -> str | None:
    with _lock_activaciones:
        cached = _cache_activaciones_id["db_id"]
    if cached:
        return cached

    db_id = None
    # 1. Buscar en "Activaciones de permisos" (nueva ubicación)
    activaciones_parent = _parent_bbdd_en_pagina(config.NOTION_ACTIVACIONES_PERMISOS_PAGE_NAME, crear=False)
    if activaciones_parent.get("type") == "page_id":
        for nombre_db in (_NOMBRE_BBDD_ACTIVACIONES, _NOMBRE_BBDD_ACTIVACIONES_ANTIGUO):
            db_id = _buscar_bbdd_en_pagina_id(activaciones_parent["page_id"], nombre_db)
            if db_id:
                break

    # 2. Buscar en subpágina de evaluaciones proyectos (ubicación antigua)
    if not db_id:
        subpagina_id = _obtener_o_crear_subpagina_evaluaciones_proyectos()
        if subpagina_id:
            for nombre_db in (_NOMBRE_BBDD_ACTIVACIONES, _NOMBRE_BBDD_ACTIVACIONES_ANTIGUO):
                db_id = _buscar_bbdd_en_pagina_id(subpagina_id, nombre_db)
                if db_id:
                    break

    # 3. Crear si no existe
    if not db_id:
        # Crear bajo "Activaciones de permisos" si existe, si no bajo subpágina proyectos
        if activaciones_parent.get("type") == "page_id":
            parent_crear = activaciones_parent["page_id"]
        else:
            parent_crear = _obtener_o_crear_subpagina_evaluaciones_proyectos()
        if not parent_crear:
            return None
        try:
            db_id = _crear_bbdd(parent_crear, _NOMBRE_BBDD_ACTIVACIONES, _props_bbdd_activaciones())
            logging.info("BD '%s' creada en Notion", _NOMBRE_BBDD_ACTIVACIONES)
        except Exception:
            logging.exception("Error creando BD '%s'", _NOMBRE_BBDD_ACTIVACIONES)
            return None

    with _lock_activaciones:
        _cache_activaciones_id["db_id"] = db_id
    return db_id


# ---------------------------------------------------------------------------
# BDs de preguntas por tipo
# ---------------------------------------------------------------------------

def _obtener_o_crear_bbdd_preguntas_tipo(tipo_clave: str) -> str | None:
    nombre_bbdd = TIPOS_EVALUACION.get(tipo_clave)
    if not nombre_bbdd:
        return None

    lock = _lock_preguntas_proyecto.setdefault(tipo_clave, threading.Lock())
    with lock:
        cached = _cache_preguntas_proyecto.get(f"db_{tipo_clave}")
    if cached:
        return cached

    subpagina_id = _obtener_o_crear_subpagina_evaluaciones_proyectos()
    if not subpagina_id:
        return None

    db_id = _buscar_bbdd_en_pagina_id(subpagina_id, nombre_bbdd)
    if not db_id:
        try:
            db_id = _crear_bbdd(subpagina_id, nombre_bbdd, _props_bbdd_preguntas_proyecto())
            logging.info("BD de preguntas '%s' creada en Notion", nombre_bbdd)
        except Exception:
            logging.exception("Error creando BD de preguntas '%s'", nombre_bbdd)
            return None

    _poblar_bbdd_preguntas_tipo(db_id, tipo_clave)

    with lock:
        _cache_preguntas_proyecto[f"db_{tipo_clave}"] = db_id
    return db_id


_bbdd_preguntas_pobladas: set = set()


def _poblar_bbdd_preguntas_tipo(db_id: str, tipo_clave: str):
    if db_id in _bbdd_preguntas_pobladas:
        return
    preguntas_default = _PREGUNTAS_INICIALES.get(tipo_clave, [])
    if not preguntas_default:
        _bbdd_preguntas_pobladas.add(db_id)
        return
    try:
        resp = _query_bbdd(db_id)
        if resp.get("results"):
            _bbdd_preguntas_pobladas.add(db_id)
            return
    except Exception:
        logging.exception("Error comprobando BD preguntas '%s'", tipo_clave)
        return

    for p in preguntas_default:
        try:
            _crear_pagina_en_bbdd(db_id, {
                "Texto": {"title": [{"type": "text", "text": {"content": p["texto"]}}]},
                "Categoria": {"rich_text": [{"type": "text", "text": {"content": p["categoria"]}}]},
                "Tipo": {"select": {"name": p["tipo"]}},
                "Opciones": {"rich_text": [{"type": "text", "text": {"content": p["opciones"]}}]},
                "Orden": {"number": p["orden"]},
            })
        except Exception:
            logging.exception("Error poblando pregunta '%s' en '%s'", p["texto"][:40], tipo_clave)
    _bbdd_preguntas_pobladas.add(db_id)


# ---------------------------------------------------------------------------
# Obtener preguntas de un tipo (con caché)
# ---------------------------------------------------------------------------

def obtener_preguntas_tipo(tipo_clave: str) -> list:
    """Devuelve lista de preguntas para el tipo dado. Caché 5 min."""
    cache_key = f"preguntas_{tipo_clave}"
    ahora = time.time()
    lock = _lock_preguntas_proyecto.setdefault(cache_key, threading.Lock())
    with lock:
        entrada = _cache_preguntas_proyecto.get(cache_key)
        if entrada and (ahora - entrada["t"]) < _CACHE_TTL:
            return entrada["data"]

    db_id = _obtener_o_crear_bbdd_preguntas_tipo(tipo_clave)
    if not db_id:
        return []
    try:
        resp = _query_bbdd(db_id, sorts=[{"property": "Orden", "direction": "ascending"}])
        preguntas = []
        for pagina in resp.get("results", []):
            props = pagina.get("properties", {})
            texto = "".join(t.get("plain_text", "") for t in (props.get("Texto") or {}).get("title", []))
            categoria = "".join(t.get("plain_text", "") for t in (props.get("Categoria") or {}).get("rich_text", []))
            tipo = ((props.get("Tipo") or {}).get("select") or {}).get("name", "abierta")
            opciones = "".join(t.get("plain_text", "") for t in (props.get("Opciones") or {}).get("rich_text", []))
            orden = (props.get("Orden") or {}).get("number") or 0
            if texto:
                preguntas.append({
                    "id": pagina["id"],
                    "texto": texto,
                    "categoria": categoria,
                    "tipo": tipo,
                    "opciones": opciones.split("|") if opciones else [],
                    "orden": orden,
                })
        with lock:
            _cache_preguntas_proyecto[cache_key] = {"data": preguntas, "t": ahora}
        return preguntas
    except Exception:
        logging.exception("Error obteniendo preguntas de tipo '%s'", tipo_clave)
        return []


# ---------------------------------------------------------------------------
# Activación de evaluaciones
# ---------------------------------------------------------------------------

def obtener_proyectos_activos_empleado(nombre_empleado: str) -> list:
    """Devuelve [{nombre_proyecto, activado_por}] para el empleado dado."""
    db_id = _obtener_o_crear_bbdd_activaciones()
    if not db_id:
        return []
    objetivo = normalizar_nombre(nombre_empleado)
    try:
        resp = _query_bbdd(db_id, filter={
            "and": [
                {"property": "Activo", "checkbox": {"equals": True}},
            ]
        })
        proyectos = []
        for pag in resp.get("results", []):
            props = pag.get("properties", {})
            empleado_titulo = "".join(t.get("plain_text", "") for t in (props.get("Empleado") or {}).get("title", []))
            if normalizar_nombre(empleado_titulo) != objetivo:
                continue
            proyecto = "".join(t.get("plain_text", "") for t in (props.get("Proyecto") or {}).get("rich_text", []))
            activado_por = "".join(t.get("plain_text", "") for t in (props.get("Activado_por") or {}).get("rich_text", []))
            if proyecto:
                proyectos.append({"nombre_proyecto": proyecto, "activado_por": activado_por})
        return proyectos
    except Exception:
        logging.exception("Error obteniendo proyectos activos para '%s'", nombre_empleado)
        return []


def obtener_equipo_proyecto(nombre_proyecto: str) -> list:
    """Devuelve la lista de empleados activados para un proyecto."""
    db_id = _obtener_o_crear_bbdd_activaciones()
    if not db_id:
        return []
    objetivo = normalizar_nombre(nombre_proyecto)
    try:
        resp = _query_bbdd(db_id, filter={"property": "Activo", "checkbox": {"equals": True}})
        empleados = []
        for pag in resp.get("results", []):
            props = pag.get("properties", {})
            proyecto = "".join(t.get("plain_text", "") for t in (props.get("Proyecto") or {}).get("rich_text", []))
            if normalizar_nombre(proyecto) != objetivo:
                continue
            empleado = "".join(t.get("plain_text", "") for t in (props.get("Empleado") or {}).get("title", []))
            if empleado:
                empleados.append(empleado)
        return empleados
    except Exception:
        logging.exception("Error obteniendo equipo del proyecto '%s'", nombre_proyecto)
        return []


def activar_evaluaciones_empleados(manager: str, proyecto: str, empleados: list) -> dict:
    """
    Activa evaluaciones de proyecto para los empleados indicados.
    Crea/actualiza registros en la BD de activaciones y envía notificaciones Slack.
    """
    db_id = _obtener_o_crear_bbdd_activaciones()
    if not db_id:
        return {"ok": False, "error": "No se pudo acceder a la BD de activaciones en Notion."}

    _obtener_o_crear_pagina_raiz_resultados()

    # Bloquear si ya existe un proyecto activo con el mismo nombre
    try:
        resp_check = _query_bbdd(db_id, filter={
            "and": [
                {"property": "Activo", "checkbox": {"equals": True}},
                {"property": "Proyecto", "rich_text": {"equals": proyecto}},
            ]
        }, page_size=1)
        if resp_check.get("results"):
            return {"ok": False, "error": f"Ya existe un proyecto activo con el nombre «{proyecto}». Elige un nombre diferente."}
    except Exception:
        logging.exception("Error comprobando duplicado para proyecto '%s'", proyecto)

    empleados_notion = {}
    try:
        for r in obtener_registros_empleados():
            if r.get("nombre") and r.get("id_usuario"):
                empleados_notion[normalizar_nombre(r["nombre"])] = r["id_usuario"]
    except Exception:
        logging.warning("No se pudieron obtener registros de empleados para notificaciones Slack")

    # Incluir al propio manager si no está ya en la lista
    todos = list(empleados)
    if manager and normalizar_nombre(manager) not in [normalizar_nombre(e) for e in todos]:
        todos.append(manager)

    activados = []
    errores = []
    for nombre_empleado in todos:
        try:
            _crear_pagina_en_bbdd(db_id, {
                "Empleado": {"title": [{"type": "text", "text": {"content": nombre_empleado}}]},
                "Proyecto": {"rich_text": [{"type": "text", "text": {"content": proyecto}}]},
                "Activado_por": {"rich_text": [{"type": "text", "text": {"content": manager}}]},
                "Activo": {"checkbox": True},
            })
            activados.append(nombre_empleado)
            slack_id = empleados_notion.get(normalizar_nombre(nombre_empleado))
            if slack_id:
                _notificar_evaluacion_activada(nombre_empleado, proyecto, slack_id)
        except Exception:
            logging.exception("Error activando evaluación para '%s' en proyecto '%s'", nombre_empleado, proyecto)
            errores.append(nombre_empleado)

    return {"ok": True, "activados": activados, "errores": errores}


def añadir_miembro_proyecto(manager: str, proyecto: str, empleado: str) -> dict:
    """Añade (o reactiva) un empleado a un proyecto activo."""
    db_id = _obtener_o_crear_bbdd_activaciones()
    if not db_id:
        return {"ok": False, "error": "No se pudo acceder a la BD de activaciones."}
    try:
        resp = _query_bbdd(db_id, filter={
            "and": [
                {"property": "Proyecto", "rich_text": {"equals": proyecto}},
                {"property": "Empleado", "title": {"equals": empleado}},
            ]
        }, page_size=1)
        existing = resp.get("results", [])
        if existing:
            notion.pages.update(page_id=existing[0]["id"], properties={"Activo": {"checkbox": True}})
        else:
            _crear_pagina_en_bbdd(db_id, {
                "Empleado": {"title": [{"type": "text", "text": {"content": empleado}}]},
                "Proyecto": {"rich_text": [{"type": "text", "text": {"content": proyecto}}]},
                "Activado_por": {"rich_text": [{"type": "text", "text": {"content": manager}}]},
                "Activo": {"checkbox": True},
            })
        slack_id = None
        for r in obtener_registros_empleados():
            if normalizar_nombre(r.get("nombre", "")) == normalizar_nombre(empleado):
                slack_id = r.get("id_usuario")
                break
        if slack_id:
            _notificar_evaluacion_activada(empleado, proyecto, slack_id)
        return {"ok": True}
    except Exception:
        logging.exception("Error añadiendo miembro '%s' al proyecto '%s'", empleado, proyecto)
        return {"ok": False, "error": "Error interno al añadir miembro."}


def _listar_child_pages_proyecto(proyecto_page_id: str) -> list:
    """Devuelve [{id, title}] de las subpáginas (evaluados) de un proyecto."""
    resultado = []
    for bloque in _iter_blocks(proyecto_page_id):
        if bloque.get("type") == "child_page":
            titulo = _titulo_child_page(bloque)
            if titulo:
                resultado.append({"id": bloque["id"], "title": titulo})
    return resultado


def _archivar_filas_evaluador_en_pagina(evaluado_page_id: str, evaluador: str) -> None:
    """Archiva en Notion las filas de la BD de evaluación donde Evaluador = evaluador."""
    db_id = _buscar_bbdd_en_pagina_id(evaluado_page_id, _NOMBRE_BBDD_EVALUACION)
    if not db_id:
        return
    try:
        resp = _query_bbdd(db_id, filter={
            "property": "Evaluador",
            "rich_text": {"equals": evaluador},
        })
        for row in resp.get("results", []):
            try:
                notion.pages.update(page_id=row["id"], archived=True)
            except Exception:
                logging.exception("Error archivando fila de eval (id=%s)", row["id"])
    except Exception:
        logging.exception("Error buscando filas de evaluador '%s'", evaluador)


def _limpiar_registros_evaluacion_miembro(proyecto: str, empleado: str) -> None:
    """
    Limpia en Notion los registros de evaluación de un miembro eliminado:
    - Archiva su página de resultados completa (evaluaciones recibidas + su autoevaluación).
    - Archiva las filas que él/ella envió en las páginas de los demás miembros.
    """
    raiz_id = _obtener_o_crear_pagina_raiz_resultados()
    if not raiz_id:
        return
    proyecto_page_id = _buscar_child_page_id(raiz_id, proyecto)
    if not proyecto_page_id:
        return

    child_pages = _listar_child_pages_proyecto(proyecto_page_id)
    obj_empleado = normalizar_nombre(empleado)

    for page in child_pages:
        if normalizar_nombre(page["title"]) == obj_empleado:
            # Archivar la página entera del miembro eliminado
            try:
                notion.pages.update(page_id=page["id"], archived=True)
            except Exception:
                logging.exception("Error archivando página de '%s' en proyecto '%s'", empleado, proyecto)
        else:
            # Archivar las filas que el miembro eliminado envió a otros
            _archivar_filas_evaluador_en_pagina(page["id"], empleado)


def eliminar_miembro_proyecto(proyecto: str, empleado: str) -> dict:
    """Desactiva a un empleado de un proyecto (Activo=False) y limpia sus registros."""
    db_id = _obtener_o_crear_bbdd_activaciones()
    if not db_id:
        return {"ok": False, "error": "No se pudo acceder a la BD de activaciones."}
    try:
        resp = _query_bbdd(db_id, filter={
            "and": [
                {"property": "Activo", "checkbox": {"equals": True}},
                {"property": "Proyecto", "rich_text": {"equals": proyecto}},
                {"property": "Empleado", "title": {"equals": empleado}},
            ]
        }, page_size=1)
        existing = resp.get("results", [])
        if not existing:
            return {"ok": False, "error": "No se encontró ese miembro en el proyecto."}
        notion.pages.update(page_id=existing[0]["id"], properties={"Activo": {"checkbox": False}})
        threading.Thread(target=_limpiar_registros_evaluacion_miembro, args=(proyecto, empleado), daemon=True).start()
        return {"ok": True}
    except Exception:
        logging.exception("Error eliminando miembro '%s' del proyecto '%s'", empleado, proyecto)
        return {"ok": False, "error": "Error interno al eliminar miembro."}


def obtener_evals_completadas_proyecto(evaluador: str, proyecto: str) -> list:
    """
    Devuelve [{tipo, evaluado}] de evaluaciones ya enviadas por evaluador en este proyecto.
    Consulta las páginas de resultados en Notion.
    """
    raiz_id = _obtener_o_crear_pagina_raiz_resultados()
    if not raiz_id:
        return []
    proyecto_page_id = _buscar_child_page_id(raiz_id, proyecto)
    if not proyecto_page_id:
        return []

    label_to_tipo = {v: k for k, v in LABELS_TIPOS.items()}
    completadas = []

    for page in _listar_child_pages_proyecto(proyecto_page_id):
        evaluado = page["title"]
        db_id = _buscar_bbdd_en_pagina_id(page["id"], _NOMBRE_BBDD_EVALUACION)
        if not db_id:
            continue
        try:
            resp = _query_bbdd(db_id, filter={
                "property": "Evaluador",
                "rich_text": {"equals": evaluador},
            })
            for row in resp.get("results", []):
                props = row.get("properties", {})
                tipo_label = (props.get("Tipo") or {}).get("select", {}).get("name", "")
                tipo_key = label_to_tipo.get(tipo_label)
                if tipo_key:
                    completadas.append({"tipo": tipo_key, "evaluado": evaluado})
        except Exception:
            logging.exception("Error consultando completadas de '%s' en '%s'", evaluado, proyecto)

    return completadas


def obtener_evaluaciones_proyecto_por_evaluado(evaluado: str) -> list[dict]:
    """Devuelve TODAS las evaluaciones de proyecto recibidas por `evaluado`, de todos los proyectos.

    Recorre la jerarquía "Evaluaciones por proyecto" → {proyecto} → {evaluado} → BD interna.
    Cada elemento: {proyecto, evaluador, tipo, respuestas, fecha (YYYY-MM-DD), page_id, url}.
    """
    raiz_id = _obtener_o_crear_pagina_raiz_resultados()
    if not raiz_id:
        return []
    objetivo = normalizar_nombre(evaluado)
    resultado: list[dict] = []
    try:
        for proyecto_page in _listar_child_pages_proyecto(raiz_id):
            proyecto = proyecto_page["title"]
            persona_page_id = None
            for persona_page in _listar_child_pages_proyecto(proyecto_page["id"]):
                if normalizar_nombre(persona_page["title"]) == objetivo:
                    persona_page_id = persona_page["id"]
                    break
            if not persona_page_id:
                continue
            db_id = _buscar_bbdd_en_pagina_id(persona_page_id, _NOMBRE_BBDD_EVALUACION)
            if not db_id:
                continue
            cursor = None
            while True:
                kwargs: dict = {"page_size": 100}
                if cursor:
                    kwargs["start_cursor"] = cursor
                resp = _query_bbdd(db_id, **kwargs)
                for fila in resp.get("results", []):
                    props = fila.get("properties", {})
                    evaluador = "".join(
                        p.get("plain_text", "") for p in (props.get("Evaluador") or {}).get("rich_text", [])
                    ).strip()
                    tipo = ((props.get("Tipo") or {}).get("select") or {}).get("name", "")
                    respuestas = "".join(
                        p.get("plain_text", "") for p in (props.get("Respuestas") or {}).get("rich_text", [])
                    ).strip()
                    fecha = ((props.get("Fecha") or {}).get("date") or {}).get("start", "")
                    if not (respuestas or evaluador):
                        continue
                    resultado.append({
                        "proyecto": proyecto,
                        "evaluador": evaluador,
                        "tipo": tipo,
                        "respuestas": respuestas,
                        "fecha": (fecha or "")[:10],
                        "page_id": fila.get("id", ""),
                        "url": fila.get("url", ""),
                    })
                if not resp.get("has_more"):
                    break
                cursor = resp.get("next_cursor")
    except Exception:
        logging.exception("Error leyendo evaluaciones de proyecto de '%s'", evaluado)
    resultado.sort(key=lambda x: x.get("fecha", ""))
    return resultado


def obtener_proyectos_manager(manager_nombre: str) -> list:
    """Proyectos activos activados por este manager, con su equipo."""
    db_id = _obtener_o_crear_bbdd_activaciones()
    if not db_id:
        return []
    objetivo = normalizar_nombre(manager_nombre)
    try:
        resp = _query_bbdd(db_id, filter={"property": "Activo", "checkbox": {"equals": True}})
        proyectos_map: dict = {}
        for pag in resp.get("results", []):
            props = pag.get("properties", {})
            activado_por = "".join(t.get("plain_text", "") for t in (props.get("Activado_por") or {}).get("rich_text", []))
            if normalizar_nombre(activado_por) != objetivo:
                continue
            proy = "".join(t.get("plain_text", "") for t in (props.get("Proyecto") or {}).get("rich_text", []))
            empleado = "".join(t.get("plain_text", "") for t in (props.get("Empleado") or {}).get("title", []))
            if proy:
                if proy not in proyectos_map:
                    proyectos_map[proy] = []
                if empleado and empleado not in proyectos_map[proy]:
                    proyectos_map[proy].append(empleado)
        return [{"nombre_proyecto": p, "equipo": e} for p, e in proyectos_map.items()]
    except Exception:
        logging.exception("Error obteniendo proyectos del manager '%s'", manager_nombre)
        return []


def obtener_estado_evaluaciones_proyecto(proyecto: str) -> list:
    """Para cada miembro del proyecto, devuelve evaluaciones recibidas y pendientes."""
    equipo = obtener_equipo_proyecto(proyecto)
    if not equipo:
        return []

    raiz_id = _obtener_o_crear_pagina_raiz_resultados()
    proyecto_page_id = _buscar_child_page_id(raiz_id, proyecto) if raiz_id else None

    resultado = []
    for miembro in equipo:
        if not proyecto_page_id:
            resultado.append({"nombre": miembro, "n_evaluaciones": 0, "evaluadores": [], "pendientes": [m for m in equipo if m != miembro], "autoevaluacion_hecha": False})
            continue
        persona_page_id = _buscar_child_page_id(proyecto_page_id, miembro)
        if not persona_page_id:
            resultado.append({"nombre": miembro, "n_evaluaciones": 0, "evaluadores": [], "pendientes": [m for m in equipo if m != miembro], "autoevaluacion_hecha": False})
            continue
        evaluadores: list = []
        autoevaluacion_hecha = False
        try:
            db_id = _buscar_bbdd_en_pagina_id(persona_page_id, _NOMBRE_BBDD_EVALUACION)
            if db_id:
                resp = _query_bbdd(db_id, page_size=100)
                for fila in resp.get("results", []):
                    props = fila.get("properties", {})
                    ev = "".join(p.get("plain_text", "") for p in props.get("Evaluador", {}).get("rich_text", [])).strip()
                    tipo = ((props.get("Tipo") or {}).get("select") or {}).get("name", "")
                    if normalizar_nombre(ev) == normalizar_nombre(miembro):
                        autoevaluacion_hecha = True
                    elif ev and ev not in evaluadores:
                        evaluadores.append(ev)
            else:
                for bloque in _iter_blocks(persona_page_id):
                    if bloque.get("type") == "paragraph":
                        texto = "".join(t.get("plain_text", "") for t in bloque.get("paragraph", {}).get("rich_text", []))
                        if texto.startswith("Evaluado por: "):
                            ev = texto.replace("Evaluado por: ", "").strip()
                            if ev:
                                if normalizar_nombre(ev) == normalizar_nombre(miembro):
                                    autoevaluacion_hecha = True
                                elif ev not in evaluadores:
                                    evaluadores.append(ev)
        except Exception:
            logging.exception("Error leyendo evaluaciones de '%s' en proyecto '%s'", miembro, proyecto)
        evaluadores_norm = {normalizar_nombre(e) for e in evaluadores}
        pendientes = [m for m in equipo if m != miembro and normalizar_nombre(m) not in evaluadores_norm]
        resultado.append({"nombre": miembro, "n_evaluaciones": len(evaluadores), "evaluadores": evaluadores, "pendientes": pendientes, "autoevaluacion_hecha": autoevaluacion_hecha})

    return resultado


def _desactivar_proyecto(proyecto: str) -> bool:
    """Marca todas las filas activas del proyecto como inactivas. Devuelve True si desactivó algo."""
    db_id = _obtener_o_crear_bbdd_activaciones()
    if not db_id:
        return False
    desactivados = 0
    try:
        cursor = None
        while True:
            kwargs: dict = {
                "filter": {"and": [
                    {"property": "Activo", "checkbox": {"equals": True}},
                    {"property": "Proyecto", "rich_text": {"equals": proyecto}},
                ]},
                "page_size": 100,
            }
            if cursor:
                kwargs["start_cursor"] = cursor
            resp = _query_bbdd(db_id, **kwargs)
            for pag in resp.get("results", []):
                notion.pages.update(page_id=pag["id"], properties={"Activo": {"checkbox": False}})
                desactivados += 1
            if not resp.get("has_more"):
                break
            cursor = resp.get("next_cursor")
    except Exception:
        logging.exception("Error desactivando proyecto '%s'", proyecto)
    return desactivados > 0


def _notificar_proyecto_completado(manager_nombre: str, proyecto: str) -> None:
    try:
        slack_id = None
        for r in obtener_registros_empleados():
            if normalizar_nombre(r.get("nombre", "")) == normalizar_nombre(manager_nombre):
                slack_id = r.get("id_usuario")
                break
        if not slack_id:
            logging.warning("No se encontró Slack ID para el manager '%s'", manager_nombre)
            return
        dm = slack_app.client.conversations_open(users=[slack_id])
        channel = dm["channel"]["id"]
        slack_app.client.chat_postMessage(
            channel=channel,
            text=(
                f"✅ Todos los miembros de tu equipo han terminado las evaluaciones del proyecto *{proyecto}*. "
                "Se cerrará el apartado en la web relacionado con este proyecto."
            ),
        )
        logging.info("Notificación de cierre enviada al manager '%s' para proyecto '%s'", manager_nombre, proyecto)
    except Exception:
        logging.exception("Error notificando cierre del proyecto '%s'", proyecto)


def _verificar_y_cerrar_proyecto(proyecto: str) -> None:
    """Si todas las evaluaciones del proyecto están completas, cierra el proyecto y notifica al manager."""
    try:
        estado = obtener_estado_evaluaciones_proyecto(proyecto)
        if not estado or any(m["pendientes"] for m in estado):
            return

        # Buscar el manager antes de desactivar (necesitamos Activo=True todavía)
        db_id = _obtener_o_crear_bbdd_activaciones()
        manager_nombre = None
        if db_id:
            resp = _query_bbdd(db_id, filter={"and": [
                {"property": "Activo", "checkbox": {"equals": True}},
                {"property": "Proyecto", "rich_text": {"equals": proyecto}},
            ]})
            for pag in resp.get("results", []):
                props = pag.get("properties", {})
                activado_por = "".join(t.get("plain_text", "") for t in (props.get("Activado_por") or {}).get("rich_text", []))
                if activado_por:
                    manager_nombre = activado_por
                    break

        # _desactivar_proyecto devuelve False si ya estaba cerrado (evita doble notificación)
        if _desactivar_proyecto(proyecto) and manager_nombre:
            _notificar_proyecto_completado(manager_nombre, proyecto)
    except Exception:
        logging.exception("Error verificando cierre del proyecto '%s'", proyecto)


def _notificar_evaluacion_activada(nombre_empleado: str, proyecto: str, slack_id: str):
    try:
        dm = slack_app.client.conversations_open(users=[slack_id])
        channel = dm["channel"]["id"]
        slack_app.client.chat_postMessage(
            channel=channel,
            text=(
                f"📋 *Evaluaciones de proyecto activas* para el proyecto *{proyecto}*.\n"
                "Recuerda completarlas en la web de evaluaciones."
            ),
        )
        logging.info("Notificación enviada a '%s' (Slack: %s)", nombre_empleado, slack_id)
    except Exception:
        logging.exception("Error enviando notificación Slack a '%s'", nombre_empleado)


# ---------------------------------------------------------------------------
# Guardar resultados de una evaluación en Notion
# ---------------------------------------------------------------------------

def guardar_evaluacion_proyecto(
    evaluador: str,
    evaluado: str,
    proyecto: str,
    tipo_clave: str,
    respuestas: dict,
    preguntas: list,
) -> bool:
    """
    Guarda los resultados de una evaluación de proyecto en Notion.
    Crea la estructura de páginas si no existe.
    """
    raiz_id = _obtener_o_crear_pagina_raiz_resultados()
    if not raiz_id:
        return False

    proyecto_page_id = _buscar_child_page_id(raiz_id, proyecto)
    if not proyecto_page_id:
        try:
            proyecto_page_id = _crear_subpagina(raiz_id, proyecto)
        except Exception:
            logging.exception("Error creando página de proyecto '%s'", proyecto)
            return False

    persona_page_id = _buscar_child_page_id(proyecto_page_id, evaluado)
    if not persona_page_id:
        try:
            persona_page_id = _crear_subpagina(proyecto_page_id, evaluado)
        except Exception:
            logging.exception("Error creando página de persona '%s' en proyecto '%s'", evaluado, proyecto)
            return False

    db_id = _obtener_o_crear_bbdd_evaluacion_proyecto(persona_page_id)
    if not db_id:
        return False

    tipo_label = LABELS_TIPOS.get(tipo_clave, tipo_clave)
    fecha = datetime.now(timezone.utc)
    respuestas_texto = _formatear_respuestas(preguntas, respuestas)

    try:
        _crear_pagina_en_bbdd(db_id, {
            "Name": {"title": [{"type": "text", "text": {"content": evaluador}}]},
            "Fecha": {"date": {"start": fecha.isoformat()}},
            "Tipo": {"select": {"name": tipo_label}},
            "Evaluador": {"rich_text": [{"type": "text", "text": {"content": evaluador}}]},
            "Respuestas": {"rich_text": [{"type": "text", "text": {"content": respuestas_texto[:2000]}}]},
        })
        threading.Thread(target=_verificar_y_cerrar_proyecto, args=(proyecto,), daemon=True).start()
        return True
    except Exception:
        logging.exception("Error guardando evaluación en BD Notion para '%s'", evaluado)
        return False


def _rt(texto: str, negrita: bool = False) -> list:
    return [{
        "type": "text",
        "text": {"content": texto},
        "annotations": {"bold": negrita, "italic": False, "strikethrough": False, "underline": False, "code": False, "color": "default"},
    }]


def _construir_bloques_evaluacion(evaluador: str, tipo_label: str, fecha: str, preguntas: list, respuestas: dict) -> list:
    bloques = [
        {
            "object": "block",
            "type": "heading_2",
            "heading_2": {"rich_text": _rt(f"{tipo_label} — {fecha}")},
        },
        {
            "object": "block",
            "type": "paragraph",
            "paragraph": {"rich_text": _rt(f"Evaluado por: {evaluador}", negrita=True)},
        },
    ]

    categoria_actual = None
    for pregunta in preguntas:
        cat = pregunta.get("categoria", "")
        if cat and cat != categoria_actual:
            categoria_actual = cat
            bloques.append({
                "object": "block",
                "type": "heading_3",
                "heading_3": {"rich_text": _rt(cat)},
            })
        texto = pregunta.get("texto", "")
        pid = pregunta.get("id", "")
        respuesta = respuestas.get(pid, "—")
        bloques.append({
            "object": "block",
            "type": "paragraph",
            "paragraph": {
                "rich_text": _rt(f"{texto}: ", negrita=True) + _rt(respuesta),
            },
        })

    bloques.append({"object": "block", "type": "divider", "divider": {}})
    return bloques
