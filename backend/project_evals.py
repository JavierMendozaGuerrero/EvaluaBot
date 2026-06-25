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

_NOMBRE_SUBPAGINA_EVAL_PROYECTOS = "Evaluaciones Proyectos"
_NOMBRE_PAGINA_RAIZ_RESULTADOS = "Evaluaciones por proyecto"
_NOMBRE_BBDD_ACTIVACIONES = "Activaciones Evaluaciones Proyectos"

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


# ---------------------------------------------------------------------------
# Obtener o crear subpágina "Evaluaciones Proyectos" dentro de "Listas de datos"
# ---------------------------------------------------------------------------

def _obtener_o_crear_subpagina_evaluaciones_proyectos() -> str | None:
    with _lock_subpagina:
        cached = _cache_subpagina_id["page_id"]
    if cached:
        return cached

    listas_parent = _parent_bbdd_en_pagina(config.NOTION_DATA_LISTS_PAGE_NAME, crear=True)
    if listas_parent.get("type") != "page_id":
        return None
    listas_page_id = listas_parent["page_id"]

    page_id = _page_or_database_link_by_name(listas_page_id, _NOMBRE_SUBPAGINA_EVAL_PROYECTOS)
    if not page_id:
        try:
            page_id = _crear_subpagina(listas_page_id, _NOMBRE_SUBPAGINA_EVAL_PROYECTOS)
            logging.info("Sub-página '%s' creada en '%s'", _NOMBRE_SUBPAGINA_EVAL_PROYECTOS, config.NOTION_DATA_LISTS_PAGE_NAME)
        except Exception:
            logging.exception("Error creando sub-página '%s'", _NOMBRE_SUBPAGINA_EVAL_PROYECTOS)
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

    subpagina_id = _obtener_o_crear_subpagina_evaluaciones_proyectos()
    if not subpagina_id:
        return None

    db_id = _buscar_bbdd_en_pagina_id(subpagina_id, _NOMBRE_BBDD_ACTIVACIONES)
    if not db_id:
        try:
            db_id = _crear_bbdd(subpagina_id, _NOMBRE_BBDD_ACTIVACIONES, _props_bbdd_activaciones())
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

    tipo_label = LABELS_TIPOS.get(tipo_clave, tipo_clave)
    fecha_str = datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M")
    bloques = _construir_bloques_evaluacion(evaluador, tipo_label, fecha_str, preguntas, respuestas)

    try:
        notion.blocks.children.append(block_id=persona_page_id, children=bloques)
        return True
    except Exception:
        logging.exception("Error guardando evaluación en Notion para '%s'", evaluado)
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
