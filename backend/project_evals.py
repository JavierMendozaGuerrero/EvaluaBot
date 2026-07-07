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

  TO-SEE → Resultados Evaluaciones/
    Resultados Evaluaciones al final de proyecto/   (página contenedora)
      {NombreProyecto}/                              (subpágina por proyecto)
        Evaluaciones                                 (BD con todas las evaluaciones del proyecto)
"""

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from . import config
from .clients import notion, slack_app
from .i18n import t, traducir_dimension
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
    idioma_por_slack_id,
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

# Estructura: página contenedora → subpágina por proyecto → BD 'Evaluaciones' dentro de
# cada proyecto (todas con las mismas columnas que antes tenía la BD plana).
_NOMBRE_PAGINA_RESULTADOS_FINAL = "Resultados Evaluaciones al final de proyecto"
_NOMBRE_BBDD_EVALS_PROYECTO = "Evaluaciones"
_PROPS_RESULTADOS_PROYECTO = {
    "Name": {"title": {}},
    "Fecha": {"date": {}},
    "Tipo": {"select": {}},
    "Evaluador": {"rich_text": {}},
    "Evaluado": {"rich_text": {}},
    "Proyecto": {"rich_text": {}},
    "Respuestas": {"rich_text": {}},
}

_lock_pagina_final = threading.Lock()
_cache_pagina_final_id: dict = {"page_id": None}

_lock_proyecto_pages = threading.Lock()
_cache_proyecto_page_id: dict = {}          # normalizar_nombre(proyecto) -> page_id

_lock_bbdd_evals_proyecto = threading.Lock()
_cache_bbdd_evals_proyecto: dict = {}       # proyecto_page_id -> db_id


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


def _obtener_o_crear_pagina_resultados_final() -> str | None:
    """Página contenedora 'Resultados Evaluaciones al final de proyecto' bajo 'Resultados Evaluaciones'."""
    with _lock_pagina_final:
        cached = _cache_pagina_final_id["page_id"]
    if cached:
        return cached
    parent = _parent_bbdd_en_pagina(config.NOTION_RESULTADOS_EVAL_PAGE_NAME, crear=True)
    if parent.get("type") != "page_id":
        return None
    parent_id = parent["page_id"]
    page_id = _buscar_child_page_id(parent_id, _NOMBRE_PAGINA_RESULTADOS_FINAL)
    if not page_id:
        try:
            page_id = _crear_subpagina(parent_id, _NOMBRE_PAGINA_RESULTADOS_FINAL)
            logging.info("Página '%s' creada en Notion", _NOMBRE_PAGINA_RESULTADOS_FINAL)
        except Exception:
            logging.exception("Error creando página '%s'", _NOMBRE_PAGINA_RESULTADOS_FINAL)
            return None
    with _lock_pagina_final:
        _cache_pagina_final_id["page_id"] = page_id
    return page_id


def _obtener_o_crear_pagina_proyecto(proyecto: str) -> str | None:
    """Subpágina de un proyecto dentro de la página de resultados finales."""
    clave = normalizar_nombre(proyecto)
    with _lock_proyecto_pages:
        cached = _cache_proyecto_page_id.get(clave)
    if cached:
        return cached
    contenedor = _obtener_o_crear_pagina_resultados_final()
    if not contenedor:
        return None
    page_id = _buscar_child_page_id(contenedor, proyecto)
    if not page_id:
        try:
            page_id = _crear_subpagina(contenedor, proyecto)
            logging.info("Subpágina de proyecto '%s' creada en Notion", proyecto)
        except Exception:
            logging.exception("Error creando subpágina de proyecto '%s'", proyecto)
            return None
    with _lock_proyecto_pages:
        _cache_proyecto_page_id[clave] = page_id
    return page_id


def _obtener_o_crear_bbdd_evals_proyecto(proyecto: str) -> str | None:
    """BD 'Evaluaciones' dentro de la subpágina del proyecto (mismas columnas que la BD plana)."""
    proyecto_page_id = _obtener_o_crear_pagina_proyecto(proyecto)
    if not proyecto_page_id:
        return None
    with _lock_bbdd_evals_proyecto:
        cached = _cache_bbdd_evals_proyecto.get(proyecto_page_id)
    if cached:
        return cached
    db_id = _buscar_bbdd_en_pagina_id(proyecto_page_id, _NOMBRE_BBDD_EVALS_PROYECTO)
    if not db_id:
        try:
            db_id = _crear_bbdd(proyecto_page_id, _NOMBRE_BBDD_EVALS_PROYECTO, _PROPS_RESULTADOS_PROYECTO)
        except Exception:
            logging.exception("Error creando BD '%s' en proyecto '%s'", _NOMBRE_BBDD_EVALS_PROYECTO, proyecto)
            return None
    with _lock_bbdd_evals_proyecto:
        _cache_bbdd_evals_proyecto[proyecto_page_id] = db_id
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

def obtener_preguntas_tipo(tipo_clave: str, idioma: str = "es") -> list:
    """Devuelve lista de preguntas para el tipo dado, en el idioma dado. Caché 5 min.

    Filtra por la columna 'Idioma' (ES/EN); para 'en' usa la fila EN de cada pregunta
    (por 'Orden') y cae a la ES cuando no hay versión EN."""
    idioma = idioma if idioma in ("es", "en", "pt") else "es"
    cache_key = f"preguntas_{tipo_clave}_{idioma}"
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
        por_idioma: dict = {}  # idioma -> {orden: pregunta}
        for pagina in resp.get("results", []):
            props = pagina.get("properties", {})
            texto = "".join(t.get("plain_text", "") for t in (props.get("Texto") or {}).get("title", []))
            if not texto:
                continue
            categoria = "".join(t.get("plain_text", "") for t in (props.get("Categoria") or {}).get("rich_text", []))
            tipo = ((props.get("Tipo") or {}).get("select") or {}).get("name", "abierta")
            opciones = "".join(t.get("plain_text", "") for t in (props.get("Opciones") or {}).get("rich_text", []))
            orden = (props.get("Orden") or {}).get("number") or 0
            idi_raw = ((props.get("Idioma") or {}).get("select") or {}).get("name", "").strip().lower()[:2]
            _lang = idi_raw if idi_raw in ("es", "en", "pt") else "es"
            pregunta = {
                "id": pagina["id"],
                "texto": texto,
                "categoria": categoria,
                "tipo": tipo,
                "opciones": opciones.split("|") if opciones else [],
                "orden": orden,
            }
            por_idioma.setdefault(_lang, {})[orden] = pregunta

        es_by = por_idioma.get("es", {})
        base_by = por_idioma.get(idioma, {})
        ordenes = sorted(set(es_by) | set(base_by))
        preguntas = [base_by.get(o) or es_by.get(o) for o in ordenes]
        # Notion no suele tener filas PT: traducimos las etiquetas fijas (categoría
        # y enunciados recurrentes) al vuelo. Los enunciados largos sin equivalente
        # se quedan en su idioma de Notion (ES/EN).
        if idioma != "es":
            for p in preguntas:
                if not p:
                    continue
                p["categoria"] = traducir_dimension(p.get("categoria", ""), idioma)
                p["texto"] = traducir_dimension(p.get("texto", ""), idioma)
        with lock:
            _cache_preguntas_proyecto[cache_key] = {"data": preguntas, "t": ahora}
        return preguntas
    except Exception:
        logging.exception("Error obteniendo preguntas de tipo '%s'", tipo_clave)
        return []


# ---------------------------------------------------------------------------
# Activación de evaluaciones
# ---------------------------------------------------------------------------

# ── Caches de DATOS con TTL (no solo IDs) ─────────────────────────────────────
# Las lecturas del dashboard (proyectos activos, equipo, evals completadas) pegaban a
# Notion en CADA request y eran el cuello de botella de la carga. Cacheamos los datos
# unos segundos: cambian despacio y el TTL corto mantiene la frescura; además se
# invalidan al escribir (activar/añadir/eliminar/guardar eval) para no mostrar datos
# obsoletos justo tras una acción.
_TTL_DATOS = 60  # segundos

_lock_activaciones_datos = threading.Lock()
# rows = [{empleado, proyecto, activado_por}] de TODAS las filas activas (una sola query).
_cache_activaciones_datos: dict = {"t": 0.0, "rows": None}

_lock_completadas = threading.Lock()
# proyecto_norm -> {"t": float, "rows": [{tipo, evaluado, evaluador_norm}]}
_cache_completadas: dict = {}


def _invalidar_cache_activaciones() -> None:
    with _lock_activaciones_datos:
        _cache_activaciones_datos["rows"] = None


def _invalidar_cache_completadas(proyecto: str = "") -> None:
    clave = normalizar_nombre(proyecto)
    with _lock_completadas:
        if clave:
            _cache_completadas.pop(clave, None)
        else:
            _cache_completadas.clear()


def _leer_activaciones_activas() -> list:
    """Filas activas de la BD de activaciones (una sola query, cacheada TTL corto).

    De aquí se derivan proyectos activos por empleado, equipo por proyecto y proyectos
    por manager sin repetir la consulta a Notion en cada endpoint.
    """
    ahora = time.time()
    with _lock_activaciones_datos:
        rows = _cache_activaciones_datos["rows"]
        if rows is not None and (ahora - _cache_activaciones_datos["t"]) < _TTL_DATOS:
            return rows
    db_id = _obtener_o_crear_bbdd_activaciones()
    filas: list = []
    if db_id:
        try:
            cursor = None
            while True:
                kwargs: dict = {"page_size": 100, "filter": {"property": "Activo", "checkbox": {"equals": True}}}
                if cursor:
                    kwargs["start_cursor"] = cursor
                resp = _query_bbdd(db_id, **kwargs)
                for pag in resp.get("results", []):
                    props = pag.get("properties", {})
                    filas.append({
                        "empleado": "".join(t.get("plain_text", "") for t in (props.get("Empleado") or {}).get("title", [])),
                        "proyecto": "".join(t.get("plain_text", "") for t in (props.get("Proyecto") or {}).get("rich_text", [])),
                        "activado_por": "".join(t.get("plain_text", "") for t in (props.get("Activado_por") or {}).get("rich_text", [])),
                    })
                if not resp.get("has_more"):
                    break
                cursor = resp.get("next_cursor")
        except Exception:
            logging.exception("Error leyendo activaciones activas")
            filas = []
    with _lock_activaciones_datos:
        _cache_activaciones_datos["rows"] = filas
        _cache_activaciones_datos["t"] = ahora
    return filas


def _leer_completadas_proyecto(proyecto: str) -> list:
    """TODAS las filas de evaluaciones enviadas de un proyecto (cacheadas TTL corto).

    Devuelve [{tipo, evaluado, evaluador_norm}]; el filtrado por evaluador se hace en
    Python, así la cache se comparte entre los distintos evaluadores del proyecto.
    """
    clave = normalizar_nombre(proyecto)
    ahora = time.time()
    with _lock_completadas:
        entrada = _cache_completadas.get(clave)
        if entrada and (ahora - entrada["t"]) < _TTL_DATOS:
            return entrada["rows"]
    db_id = _obtener_o_crear_bbdd_evals_proyecto(proyecto)
    filas: list = []
    if db_id:
        label_to_tipo = {v: k for k, v in LABELS_TIPOS.items()}
        try:
            cursor = None
            while True:
                kwargs = {"page_size": 100}
                if cursor:
                    kwargs["start_cursor"] = cursor
                resp = _query_bbdd(db_id, **kwargs)
                for row in resp.get("results", []):
                    props = row.get("properties", {})
                    evaluador_fila = "".join(
                        p.get("plain_text", "") for p in (props.get("Evaluador") or {}).get("rich_text", [])
                    ).strip()
                    evaluado = "".join(
                        p.get("plain_text", "") for p in (props.get("Evaluado") or {}).get("rich_text", [])
                    ).strip()
                    tipo_label = (props.get("Tipo") or {}).get("select", {}).get("name", "")
                    tipo_key = label_to_tipo.get(tipo_label)
                    if tipo_key and evaluado:
                        filas.append({"tipo": tipo_key, "evaluado": evaluado, "evaluador_norm": normalizar_nombre(evaluador_fila)})
                if not resp.get("has_more"):
                    break
                cursor = resp.get("next_cursor")
        except Exception:
            logging.exception("Error obteniendo evals completadas del proyecto '%s'", proyecto)
            filas = []
    with _lock_completadas:
        _cache_completadas[clave] = {"t": ahora, "rows": filas}
    return filas


def obtener_proyectos_activos_empleado(nombre_empleado: str) -> list:
    """Devuelve [{nombre_proyecto, activado_por}] para el empleado dado."""
    objetivo = normalizar_nombre(nombre_empleado)
    proyectos = []
    for r in _leer_activaciones_activas():
        if normalizar_nombre(r["empleado"]) != objetivo:
            continue
        if r["proyecto"]:
            proyectos.append({"nombre_proyecto": r["proyecto"], "activado_por": r["activado_por"]})
    return proyectos


def obtener_equipo_proyecto(nombre_proyecto: str) -> list:
    """Devuelve la lista de empleados activados para un proyecto."""
    objetivo = normalizar_nombre(nombre_proyecto)
    empleados = []
    for r in _leer_activaciones_activas():
        if normalizar_nombre(r["proyecto"]) != objetivo:
            continue
        if r["empleado"]:
            empleados.append(r["empleado"])
    return empleados


def activar_evaluaciones_empleados(manager: str, proyecto: str, empleados: list, idioma: str = "es") -> dict:
    """
    Activa evaluaciones de proyecto para los empleados indicados.
    Crea/actualiza registros en la BD de activaciones y envía notificaciones Slack.
    """
    db_id = _obtener_o_crear_bbdd_activaciones()
    if not db_id:
        return {"ok": False, "error": t("pe.err_db_access_notion", idioma)}

    # Bloquear si ya existe un proyecto activo con el mismo nombre
    try:
        resp_check = _query_bbdd(db_id, filter={
            "and": [
                {"property": "Activo", "checkbox": {"equals": True}},
                {"property": "Proyecto", "rich_text": {"equals": proyecto}},
            ]
        }, page_size=1)
        if resp_check.get("results"):
            return {"ok": False, "error": t("pe.err_project_exists", idioma, proyecto=proyecto)}
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
            from .eval_tracking import registrar_envio
            registrar_envio(nombre_empleado, "proyecto", detalle=proyecto)
            slack_id = empleados_notion.get(normalizar_nombre(nombre_empleado))
            if slack_id:
                _notificar_evaluacion_activada(nombre_empleado, proyecto, slack_id)
        except Exception:
            logging.exception("Error activando evaluación para '%s' en proyecto '%s'", nombre_empleado, proyecto)
            errores.append(nombre_empleado)

    _invalidar_cache_activaciones()
    return {"ok": True, "activados": activados, "errores": errores}


def añadir_miembro_proyecto(manager: str, proyecto: str, empleado: str, idioma: str = "es") -> dict:
    """Añade (o reactiva) un empleado a un proyecto activo."""
    db_id = _obtener_o_crear_bbdd_activaciones()
    if not db_id:
        return {"ok": False, "error": t("pe.err_db_access", idioma)}
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
        _invalidar_cache_activaciones()
        return {"ok": True}
    except Exception:
        logging.exception("Error añadiendo miembro '%s' al proyecto '%s'", empleado, proyecto)
        return {"ok": False, "error": t("pe.err_add_member", idioma)}


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
    """Archiva las filas de evaluación del miembro eliminado en la BD del proyecto."""
    db_id = _obtener_o_crear_bbdd_evals_proyecto(proyecto)
    if not db_id:
        return
    try:
        resp = _query_bbdd(db_id, filter={
            "property": "Evaluador", "rich_text": {"equals": empleado},
        }, page_size=100)
        for fila in resp.get("results", []):
            try:
                notion.pages.update(page_id=fila["id"], archived=True)
            except Exception:
                logging.exception("Error archivando fila de '%s' en proyecto '%s'", empleado, proyecto)
    except Exception:
        logging.exception("Error limpiando registros de '%s' en proyecto '%s'", empleado, proyecto)


def eliminar_miembro_proyecto(proyecto: str, empleado: str, idioma: str = "es") -> dict:
    """Desactiva a un empleado de un proyecto (Activo=False) y limpia sus registros."""
    db_id = _obtener_o_crear_bbdd_activaciones()
    if not db_id:
        return {"ok": False, "error": t("pe.err_db_access", idioma)}
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
            return {"ok": False, "error": t("pe.err_member_not_found", idioma)}
        notion.pages.update(page_id=existing[0]["id"], properties={"Activo": {"checkbox": False}})
        threading.Thread(target=_limpiar_registros_evaluacion_miembro, args=(proyecto, empleado), daemon=True).start()
        _invalidar_cache_activaciones()
        _invalidar_cache_completadas(proyecto)
        return {"ok": True}
    except Exception:
        logging.exception("Error eliminando miembro '%s' del proyecto '%s'", empleado, proyecto)
        return {"ok": False, "error": t("pe.err_remove_member", idioma)}


def obtener_evals_completadas_proyecto(evaluador: str, proyecto: str) -> list:
    """
    Devuelve [{tipo, evaluado}] de evaluaciones ya enviadas por evaluador en este proyecto.
    Lee de la cache TTL del proyecto (comparte lectura entre evaluadores) y filtra por
    Evaluador NORMALIZADO, para que case aunque el nombre guardado varíe.
    """
    evaluador_norm = normalizar_nombre(evaluador)
    return [
        {"tipo": r["tipo"], "evaluado": r["evaluado"]}
        for r in _leer_completadas_proyecto(proyecto)
        if r["evaluador_norm"] == evaluador_norm
    ]


def obtener_evaluaciones_proyecto_por_evaluado(evaluado: str) -> list[dict]:
    """Devuelve TODAS las evaluaciones de proyecto recibidas por `evaluado`.

    Recorre cada subpágina de proyecto bajo 'Resultados Evaluaciones al final de proyecto'
    (TO-SEE → Resultados Evaluaciones) y su BD 'Evaluaciones' interna.
    Cada elemento: {proyecto, evaluador, tipo, respuestas, fecha (YYYY-MM-DD), page_id, url}.
    """
    contenedor = _obtener_o_crear_pagina_resultados_final()
    if not contenedor:
        return []
    objetivo = normalizar_nombre(evaluado)
    resultado: list[dict] = []
    for proy in _listar_child_pages_proyecto(contenedor):
        proyecto_nombre = proy["title"]
        db_id = _buscar_bbdd_en_pagina_id(proy["id"], _NOMBRE_BBDD_EVALS_PROYECTO)
        if not db_id:
            continue
        try:
            cursor = None
            while True:
                kwargs: dict = {"page_size": 100}
                if cursor:
                    kwargs["start_cursor"] = cursor
                resp = _query_bbdd(db_id, **kwargs)
                for fila in resp.get("results", []):
                    props = fila.get("properties", {})
                    ev_evaluado = "".join(
                        p.get("plain_text", "") for p in (props.get("Evaluado") or {}).get("rich_text", [])
                    ).strip()
                    if normalizar_nombre(ev_evaluado) != objetivo:
                        continue
                    evaluador = "".join(
                        p.get("plain_text", "") for p in (props.get("Evaluador") or {}).get("rich_text", [])
                    ).strip()
                    proyecto = "".join(
                        p.get("plain_text", "") for p in (props.get("Proyecto") or {}).get("rich_text", [])
                    ).strip()
                    tipo = ((props.get("Tipo") or {}).get("select") or {}).get("name", "")
                    respuestas = "".join(
                        p.get("plain_text", "") for p in (props.get("Respuestas") or {}).get("rich_text", [])
                    ).strip()
                    fecha = ((props.get("Fecha") or {}).get("date") or {}).get("start", "")
                    if not (respuestas or evaluador):
                        continue
                    resultado.append({
                        "proyecto": proyecto or proyecto_nombre,
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
            logging.exception("Error leyendo evaluaciones del proyecto '%s' de '%s'", proyecto_nombre, evaluado)
    resultado.sort(key=lambda x: x.get("fecha", ""))
    return resultado


def obtener_proyectos_manager(manager_nombre: str) -> list:
    """Proyectos activos activados por este manager, con su equipo."""
    objetivo = normalizar_nombre(manager_nombre)
    proyectos_map: dict = {}
    for r in _leer_activaciones_activas():
        if normalizar_nombre(r["activado_por"]) != objetivo:
            continue
        proy = r["proyecto"]
        if not proy:
            continue
        if proy not in proyectos_map:
            proyectos_map[proy] = []
        if r["empleado"] and r["empleado"] not in proyectos_map[proy]:
            proyectos_map[proy].append(r["empleado"])
    return [{"nombre_proyecto": p, "equipo": e} for p, e in proyectos_map.items()]


def obtener_progreso_proyectos_empleado(persona: str) -> list:
    """Equipo + evals completadas de `persona` para CADA uno de sus proyectos activos.

    Reúne en UNA respuesta lo que el dashboard pedía con 1 + 2N peticiones (waterfall):
    las activaciones se leen una vez (cache compartida) y las completadas de cada
    proyecto se consultan EN PARALELO. Devuelve
    [{nombre_proyecto, activado_por, equipo, completadas}].
    """
    activos = obtener_proyectos_activos_empleado(persona)
    if not activos:
        return []
    nombres = [p["nombre_proyecto"] for p in activos]
    # completadas por proyecto, en paralelo (cada una es una query a la BD del proyecto).
    completadas_por_proy: dict = {}
    with ThreadPoolExecutor(max_workers=min(8, len(nombres))) as ex:
        for nombre, comp in zip(nombres, ex.map(lambda p: obtener_evals_completadas_proyecto(persona, p), nombres)):
            completadas_por_proy[nombre] = comp
    salida = []
    for p in activos:
        nombre = p["nombre_proyecto"]
        salida.append({
            "nombre_proyecto": nombre,
            "activado_por": p.get("activado_por", ""),
            "equipo": obtener_equipo_proyecto(nombre),  # de la cache de activaciones ya caliente
            "completadas": completadas_por_proy.get(nombre, []),
        })
    return salida


def obtener_estado_evaluaciones_proyecto(proyecto: str) -> list:
    """Para cada miembro del proyecto, devuelve evaluaciones recibidas y pendientes."""
    equipo = obtener_equipo_proyecto(proyecto)
    if not equipo:
        return []

    db_id = _obtener_o_crear_bbdd_evals_proyecto(proyecto)
    filas_proyecto: list[dict] = []
    if db_id:
        try:
            resp = _query_bbdd(db_id, page_size=100)
            for fila in resp.get("results", []):
                props = fila.get("properties", {})
                ev_evaluado = "".join(p.get("plain_text", "") for p in (props.get("Evaluado") or {}).get("rich_text", [])).strip()
                ev_evaluador = "".join(p.get("plain_text", "") for p in (props.get("Evaluador") or {}).get("rich_text", [])).strip()
                filas_proyecto.append({"evaluado": ev_evaluado, "evaluador": ev_evaluador})
        except Exception:
            logging.exception("Error leyendo estado de evaluaciones del proyecto '%s'", proyecto)

    resultado = []
    for miembro in equipo:
        evaluadores: list = []
        autoevaluacion_hecha = False
        for fila in filas_proyecto:
            if normalizar_nombre(fila["evaluado"]) != normalizar_nombre(miembro):
                continue
            ev = fila["evaluador"]
            if normalizar_nombre(ev) == normalizar_nombre(miembro):
                autoevaluacion_hecha = True
            elif ev and ev not in evaluadores:
                evaluadores.append(ev)
        evaluadores_norm = {normalizar_nombre(e) for e in evaluadores}
        pendientes = [m for m in equipo if m != miembro and normalizar_nombre(m) not in evaluadores_norm]
        resultado.append({"nombre": miembro, "n_evaluaciones": len(evaluadores), "evaluadores": evaluadores, "pendientes": pendientes, "autoevaluacion_hecha": autoevaluacion_hecha})

    # Cuántas evaluaciones de compañeros ha COMPLETADO cada miembro (como evaluador).
    # Se obtiene invirtiendo la relación: si M aparece como evaluador de otro miembro,
    # es que M ha completado esa evaluación de compañero. No incluye la autoevaluación.
    total_companeros = max(len(equipo) - 1, 0)
    hechas_por_evaluador: dict = {normalizar_nombre(m): 0 for m in equipo}
    for r in resultado:
        for ev in r["evaluadores"]:
            k = normalizar_nombre(ev)
            if k in hechas_por_evaluador:
                hechas_por_evaluador[k] += 1
    for r in resultado:
        r["n_completadas"] = hechas_por_evaluador.get(normalizar_nombre(r["nombre"]), 0)
        r["total_companeros"] = total_companeros

    return resultado


def enviar_recordatorios_proyecto(proyecto: str) -> dict:
    """Envía un DM de Slack a cada miembro del proyecto con evaluaciones pendientes.

    A cada miembro se le indica qué le falta: su autoevaluación y/o evaluar a los
    compañeros que aún no ha evaluado. Devuelve {enviados, fallidos, sin_pendientes}.
    """
    estado = obtener_estado_evaluaciones_proyecto(proyecto)
    if not estado:
        return {"enviados": [], "fallidos": [], "sin_pendientes": True}

    # Invertir el estado: para cada evaluador, a qué compañeros aún debe evaluar.
    faltan_evaluar: dict = {m["nombre"]: [] for m in estado}
    for m in estado:
        for evaluador in m.get("pendientes", []):
            if evaluador in faltan_evaluar and evaluador != m["nombre"]:
                faltan_evaluar[evaluador].append(m["nombre"])
    falta_auto = {m["nombre"]: not m.get("autoevaluacion_hecha", False) for m in estado}

    id_por_nombre = {
        normalizar_nombre(r.get("nombre", "")): r.get("id_usuario")
        for r in obtener_registros_empleados()
    }

    enviados: list = []
    fallidos: list = []
    for m in estado:
        nombre = m["nombre"]
        items_pendientes = list(faltan_evaluar.get(nombre, []))
        if not items_pendientes and not falta_auto.get(nombre):
            continue  # este miembro ya lo tiene todo hecho
        slack_id = id_por_nombre.get(normalizar_nombre(nombre))
        if not slack_id:
            logging.warning("No se encontró Slack ID para '%s' (recordatorio proyecto '%s')", nombre, proyecto)
            fallidos.append(nombre)
            continue
        try:
            idioma = idioma_por_slack_id(slack_id)
            lineas = []
            if falta_auto.get(nombre):
                lineas.append(f"• {t('rec.item_self', idioma)}")
            for otro in items_pendientes:
                lineas.append(f"• {t('rec.item_eval', idioma, nombre=otro)}")
            texto = t("rec.reminder", idioma, n=len(lineas), proyecto=proyecto, lista="\n".join(lineas))
            dm = slack_app.client.conversations_open(users=[slack_id])
            slack_app.client.chat_postMessage(channel=dm["channel"]["id"], text=texto)
            enviados.append(nombre)
        except Exception:
            logging.exception("Error enviando recordatorio a '%s' del proyecto '%s'", nombre, proyecto)
            fallidos.append(nombre)

    return {"enviados": enviados, "fallidos": fallidos, "sin_pendientes": not enviados and not fallidos}


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
    if desactivados:
        _invalidar_cache_activaciones()
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
    Escribe en la BD 'Evaluaciones' dentro de la subpágina del proyecto, bajo
    TO-SEE → Resultados Evaluaciones → Resultados Evaluaciones al final de proyecto.
    """
    db_id = _obtener_o_crear_bbdd_evals_proyecto(proyecto)
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
            "Evaluado": {"rich_text": [{"type": "text", "text": {"content": evaluado}}]},
            "Proyecto": {"rich_text": [{"type": "text", "text": {"content": proyecto}}]},
            "Respuestas": {"rich_text": [{"type": "text", "text": {"content": respuestas_texto[:2000]}}]},
        })
        from .eval_tracking import marcar_completada
        marcar_completada(evaluador, "proyecto")
        _invalidar_cache_completadas(proyecto)
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
