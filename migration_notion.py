"""
migration_notion.py — Reorganiza la estructura de Notion según la nueva arquitectura.

Ejecutar UNA SOLA VEZ después de actualizar el código:
    python migration_notion.py

Lee la estructura actual desde el .env del proyecto, crea las páginas contenedoras
(TO-DO, TO-SEE y sub-páginas) y mueve las bases de datos/páginas existentes a sus
nuevas ubicaciones. Es idempotente: si ya existe algo en la ubicación correcta, lo
omite sin error.

Nueva estructura deseada:
  Evaluaciones continuas (raíz, NOTION_PARENT_PAGE_ID)
  ├── TO-DO
  │   ├── Datos a Monitorizar
  │   │   ├── Lista de empleados  (BD)
  │   │   ├── Lista CA / Lista de CAs  (BD)
  │   │   ├── Usuarios Web  (BD)
  │   │   └── Gestión de MiddleOffice  (página → contiene Cargos y Relaciones MO)
  │   └── Datos opcionalmente modificables
  │       ├── Preguntas Chatbot  (página)
  │       │   ├── Preguntas evaluación mensual  (página, contiene Negocio/MO/Palantir)
  │       │   ├── Preguntas evaluación personal  (BD, nueva)
  │       │   └── Preguntas seguimiento CA  (BD, nueva)
  │       ├── Criterios de Evaluaciones  (página)
  │       ├── Evaluacion al finalizar proyecto  (página)
  │       └── Ejemplos de Guia para bot  (BD/página)
  └── TO-SEE
      ├── Resultados Evaluaciones  (página)
      │   ├── Resultados Evaluaciones Mensuales  (página, ex-Evaluaciones Individuales)
      │   ├── Resultados Evaluaciones CA  (página, ex-Seguimiento CA)
      │   ├── Resultados Barbecho  (BD, ex-Registros barbecho)
      │   └── Resultados Seguimiento personal  (BD, ex-Respuestas)
      ├── Informes finales  (BD)
      ├── Activaciones de permisos  (página)
      │   ├── Acceso Individual Advisee  (BD)
      │   └── Acceso Evaluaciones Proyecto  (BD, ex-Activaciones Evaluaciones Proyectos)
      └── Objetivos Empleados  (página)
"""

import os
import sys
import logging
import time

# Cargar .env antes de importar el backend
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
except ImportError:
    pass  # python-dotenv no instalado; las vars deben estar ya en el entorno

# Añadir directorio raíz al path
sys.path.insert(0, os.path.dirname(__file__))

from backend.clients import notion
from backend import config
from backend.notion_service import (
    _parent_bbdd_referencia,
    _page_or_database_link_by_name,
    _extraer_titulo_bbdd,
    _extraer_titulo_pagina,
    _tipo_objeto_busqueda_bbdd,
)
from backend.utils import normalizar_nombre

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("migration")


# ---------------------------------------------------------------------------
# Utilidades de Notion
# ---------------------------------------------------------------------------

def crear_pagina(parent_id: str, nombre: str) -> str:
    """Crea una página con el nombre dado bajo parent_id. Devuelve su ID."""
    resp = notion.pages.create(
        parent={"type": "page_id", "page_id": parent_id},
        properties={"title": {"title": [{"type": "text", "text": {"content": nombre}}]}},
    )
    log.info("  ✓ Página creada: '%s' (ID: %s)", nombre, resp["id"])
    return resp["id"]


def mover_pagina(page_id: str, new_parent_id: str) -> None:
    """Mueve una página o base de datos al nuevo padre."""
    try:
        notion.pages.update(
            page_id=page_id,
            parent={"type": "page_id", "page_id": new_parent_id},
        )
    except Exception:
        # Las bases de datos usan el endpoint de databases, no pages
        notion.databases.update(
            database_id=page_id,
            parent={"type": "page_id", "page_id": new_parent_id},
        )


def renombrar_pagina(page_id: str, nuevo_nombre: str) -> None:
    """Renombra una página."""
    notion.pages.update(
        page_id=page_id,
        properties={"title": {"title": [{"type": "text", "text": {"content": nuevo_nombre}}]}},
    )


def renombrar_bbdd(db_id: str, nuevo_nombre: str) -> None:
    """Renombra una base de datos."""
    notion.databases.update(
        database_id=db_id,
        title=[{"type": "text", "text": {"content": nuevo_nombre}}],
    )


def obtener_o_crear_pagina(parent_id: str, nombre: str) -> str:
    """Devuelve el ID de la página con ese nombre bajo parent_id; la crea si no existe."""
    page_id = _page_or_database_link_by_name(parent_id, nombre)
    if page_id:
        log.info("  → Página ya existe: '%s'", nombre)
        return page_id
    return crear_pagina(parent_id, nombre)


def buscar_en_raiz(root_id: str, nombre: str) -> str | None:
    """Busca directamente bajo root_id por nombre (child_page o child_database)."""
    return _page_or_database_link_by_name(root_id, nombre)


def buscar_global(nombre: str) -> str | None:
    """Busca globalmente en Notion por nombre exacto."""
    tipo_bbdd = _tipo_objeto_busqueda_bbdd()  # "data_source" o "database" según el entorno
    for tipo in (tipo_bbdd, "page"):
        try:
            resp = notion.search(
                query=nombre,
                filter={"value": tipo, "property": "object"},
                page_size=20,
            )
            for item in resp.get("results", []):
                if item.get("object") in ("database", "data_source"):
                    titulo = _extraer_titulo_bbdd(item)
                else:
                    titulo = _extraer_titulo_pagina(item)
                if normalizar_nombre(titulo) == normalizar_nombre(nombre):
                    return item["id"]
        except Exception:
            pass
    return None


def mover_si_existe(nombre_buscar: str, nuevo_parent_id: str, nuevo_nombre: str | None = None, *,
                    root_id: str | None = None, pagina_origen: str | None = None) -> str | None:
    """
    Busca el objeto con nombre_buscar y lo mueve a nuevo_parent_id.
    Opcionalmente lo renombra a nuevo_nombre.
    Busca primero en pagina_origen (si se indica), luego en root, luego globalmente.
    Devuelve el ID del objeto movido o None si no se encontró.
    """
    obj_id = None

    if pagina_origen:
        obj_id = _page_or_database_link_by_name(pagina_origen, nombre_buscar)

    if not obj_id and root_id:
        obj_id = _page_or_database_link_by_name(root_id, nombre_buscar)

    if not obj_id:
        obj_id = buscar_global(nombre_buscar)

    if not obj_id:
        log.warning("  ✗ No encontrado: '%s' — se omite", nombre_buscar)
        return None

    try:
        mover_pagina(obj_id, nuevo_parent_id)
        log.info("  ✓ Movido: '%s' → nuevo padre", nombre_buscar)
    except Exception as e:
        log.error("  ✗ Error moviendo '%s': %s", nombre_buscar, e)
        return obj_id

    if nuevo_nombre and nuevo_nombre != nombre_buscar:
        try:
            renombrar_pagina(obj_id, nuevo_nombre)
            log.info("  ✓ Renombrado: '%s' → '%s'", nombre_buscar, nuevo_nombre)
        except Exception:
            try:
                renombrar_bbdd(obj_id, nuevo_nombre)
                log.info("  ✓ Renombrado (BD): '%s' → '%s'", nombre_buscar, nuevo_nombre)
            except Exception as e2:
                log.warning("  ~ No se pudo renombrar '%s': %s", nombre_buscar, e2)

    time.sleep(0.3)  # Respetar rate limits de Notion API
    return obj_id


def crear_bbdd_si_no_existe(parent_id: str, nombre: str, props: dict) -> str | None:
    """Crea una BD bajo parent_id si no existe ya una con ese nombre."""
    existente = _page_or_database_link_by_name(parent_id, nombre)
    if existente:
        log.info("  → BD ya existe: '%s'", nombre)
        return existente
    try:
        nueva = notion.databases.create(
            parent={"type": "page_id", "page_id": parent_id},
            title=[{"type": "text", "text": {"content": nombre}}],
            properties=props,
        )
        log.info("  ✓ BD creada: '%s'", nombre)
        time.sleep(0.3)
        return nueva["id"]
    except Exception as e:
        log.error("  ✗ Error creando BD '%s': %s", nombre, e)
        return None


# ---------------------------------------------------------------------------
# Propiedades de las nuevas BDs
# ---------------------------------------------------------------------------

def _props_preguntas_seguimiento_ca():
    return {
        "Clave": {"title": {}},
        "Texto": {"rich_text": {}},
    }


def _props_preguntas_eval_personal():
    return {
        "Clave": {"title": {}},
        "Texto": {"rich_text": {}},
    }


def poblar_preguntas_seguimiento_ca(db_id: str) -> None:
    """Inserta las preguntas del flujo CA en la nueva BD."""
    preguntas = [
        ("advisee", "¿Cuál es el nombre de tu advisee?"),
        ("opinion", "¿Qué opinas de las evaluaciones?"),
    ]
    try:
        resp = notion.databases.query(database_id=db_id, page_size=10)
        existentes = {
            " ".join(p.get("plain_text", "") for p in fila.get("properties", {}).get("Clave", {}).get("title", [])).strip()
            for fila in resp.get("results", [])
        }
    except Exception:
        existentes = set()

    for clave, texto in preguntas:
        if clave in existentes:
            continue
        try:
            notion.pages.create(
                parent={"database_id": db_id},
                properties={
                    "Clave": {"title": [{"text": {"content": clave}}]},
                    "Texto": {"rich_text": [{"text": {"content": texto}}]},
                },
            )
            log.info("    + Pregunta CA '%s' añadida", clave)
        except Exception as e:
            log.warning("    ~ Error añadiendo pregunta CA '%s': %s", clave, e)


# ---------------------------------------------------------------------------
# Migración principal
# ---------------------------------------------------------------------------

def main():
    log.info("=" * 60)
    log.info("MIGRACIÓN NOTION — inicio")
    log.info("=" * 60)

    # ID de la página raíz
    try:
        parent_raiz = _parent_bbdd_referencia()
        root_id = parent_raiz["page_id"]
    except Exception as e:
        log.error("No se pudo obtener la página raíz: %s", e)
        sys.exit(1)

    log.info("Raíz Notion: %s", root_id)

    # Buscar la página de Listas de datos actual (puede existir con nombre antiguo)
    listas_antiguo_id = buscar_en_raiz(root_id, "Listas de datos") or buscar_en_raiz(root_id, "Listas datos")

    # ===========================================================
    # FASE 1: Crear estructura TO-DO / TO-SEE y sub-páginas
    # ===========================================================
    log.info("\n── FASE 1: Crear páginas contenedoras ──")

    todo_id = obtener_o_crear_pagina(root_id, config.NOTION_TODO_PAGE_NAME)
    tosee_id = obtener_o_crear_pagina(root_id, config.NOTION_TOSEE_PAGE_NAME)

    # Bajo TO-DO
    datos_monitorizar_id = obtener_o_crear_pagina(todo_id, config.NOTION_DATA_LISTS_PAGE_NAME)
    datos_modificables_id = obtener_o_crear_pagina(todo_id, config.NOTION_DATA_MODIFICABLES_PAGE_NAME)
    preguntas_chatbot_id = obtener_o_crear_pagina(datos_modificables_id, config.NOTION_PREGUNTAS_CHATBOT_PAGE_NAME)

    # Bajo TO-SEE
    resultados_id = obtener_o_crear_pagina(tosee_id, config.NOTION_RESULTADOS_EVAL_PAGE_NAME)
    activaciones_id = obtener_o_crear_pagina(tosee_id, config.NOTION_ACTIVACIONES_PERMISOS_PAGE_NAME)

    # ===========================================================
    # FASE 2: Mover contenido a "Datos a Monitorizar"
    # ===========================================================
    log.info("\n── FASE 2: Mover a 'Datos a Monitorizar' ──")

    mover_si_existe("Lista de empleados", datos_monitorizar_id, root_id=root_id, pagina_origen=listas_antiguo_id)
    # Lista CA puede llamarse "Lista CA" o "Lista de CAs"
    for nombre_ca in ("Lista de CAs", "Lista CA"):
        r = mover_si_existe(nombre_ca, datos_monitorizar_id, "Lista de CAs",
                            root_id=root_id, pagina_origen=listas_antiguo_id)
        if r:
            break
    mover_si_existe("Usarios web", datos_monitorizar_id, "Usuarios Web",
                    root_id=root_id, pagina_origen=listas_antiguo_id)
    # Si ya se llamaba "Usuarios web" o "Usuarios Web"
    mover_si_existe("Usuarios web", datos_monitorizar_id, "Usuarios Web",
                    root_id=root_id, pagina_origen=listas_antiguo_id)
    mover_si_existe("Gestión de MiddleOffice", datos_monitorizar_id,
                    root_id=root_id, pagina_origen=listas_antiguo_id)

    # ===========================================================
    # FASE 3: Mover contenido a "Datos opcionalmente modificables"
    # ===========================================================
    log.info("\n── FASE 3: Mover a 'Datos opcionalmente modificables' ──")

    mover_si_existe("Criterios de evaluaciones", datos_modificables_id, "Criterios de Evaluaciones",
                    root_id=root_id, pagina_origen=listas_antiguo_id)
    mover_si_existe("Evaluaciones Proyectos", datos_modificables_id, "Evaluacion al finalizar proyecto",
                    root_id=root_id, pagina_origen=listas_antiguo_id)
    for nombre_ej in ("Ejemplos de guia", "Ejemplos de guía"):
        r = mover_si_existe(nombre_ej, datos_modificables_id, "Ejemplos de Guia para bot",
                            root_id=root_id, pagina_origen=listas_antiguo_id)
        if r:
            break

    # ===========================================================
    # FASE 4: Mover "Preguntas" a "Preguntas Chatbot" (renombrando)
    # ===========================================================
    log.info("\n── FASE 4: Reorganizar 'Preguntas Chatbot' ──")

    preguntas_antiguo_id = None
    if listas_antiguo_id:
        preguntas_antiguo_id = _page_or_database_link_by_name(listas_antiguo_id, "Preguntas")
    if not preguntas_antiguo_id:
        preguntas_antiguo_id = buscar_global("Preguntas")

    if preguntas_antiguo_id:
        try:
            mover_pagina(preguntas_antiguo_id, preguntas_chatbot_id)
            renombrar_pagina(preguntas_antiguo_id, "Preguntas evaluación mensual")
            log.info("  ✓ 'Preguntas' movida a Preguntas Chatbot y renombrada")
        except Exception as e:
            log.error("  ✗ Error moviendo 'Preguntas': %s", e)
    else:
        log.warning("  ✗ No se encontró la página 'Preguntas' — se omite")

    # Crear BD "Preguntas seguimiento CA" (nueva)
    log.info("  → Creando 'Preguntas seguimiento CA'")
    db_id_ca = crear_bbdd_si_no_existe(preguntas_chatbot_id, "Preguntas seguimiento CA",
                                       _props_preguntas_seguimiento_ca())
    if db_id_ca:
        poblar_preguntas_seguimiento_ca(db_id_ca)

    # "Preguntas evaluación personal": buscar en Evaluaciones Personales y mover
    eval_personales_id = buscar_en_raiz(root_id, "Evaluaciones Personales")
    preguntas_personal_id = None
    if eval_personales_id:
        preguntas_personal_id = _page_or_database_link_by_name(eval_personales_id, "Preguntas")
    if preguntas_personal_id:
        try:
            mover_pagina(preguntas_personal_id, preguntas_chatbot_id)
            renombrar_bbdd(preguntas_personal_id, "Preguntas evaluación personal")
            log.info("  ✓ 'Preguntas' (personal) movida a Preguntas Chatbot y renombrada")
        except Exception as e:
            log.error("  ✗ Error moviendo 'Preguntas' personal: %s", e)
    else:
        # Crear la BD nueva si no existía
        log.info("  → Creando 'Preguntas evaluación personal' (nueva BD)")
        crear_bbdd_si_no_existe(preguntas_chatbot_id, "Preguntas evaluación personal",
                                _props_preguntas_eval_personal())

    # ===========================================================
    # FASE 5: Mover a "Resultados Evaluaciones"
    # ===========================================================
    log.info("\n── FASE 5: Mover a 'Resultados Evaluaciones' ──")

    mover_si_existe("Evaluaciones Individuales", resultados_id, "Resultados Evaluaciones Mensuales",
                    root_id=root_id)
    mover_si_existe("Seguimiento CA", resultados_id, "Resultados Evaluaciones CA", root_id=root_id)

    # Registros barbecho → Resultados Barbecho
    mover_si_existe("Registros barbecho", resultados_id, "Resultados Barbecho", root_id=root_id)

    # Respuestas (de Evaluaciones Personales) → Resultados Seguimiento personal
    if eval_personales_id:
        respuestas_id = _page_or_database_link_by_name(eval_personales_id, "Respuestas")
        if respuestas_id:
            try:
                mover_pagina(respuestas_id, resultados_id)
                renombrar_bbdd(respuestas_id, "Resultados Seguimiento personal")
                log.info("  ✓ 'Respuestas' movida a Resultados Evaluaciones y renombrada")
            except Exception as e:
                log.error("  ✗ Error moviendo 'Respuestas': %s", e)
        else:
            log.warning("  ✗ 'Respuestas' no encontrada en Evaluaciones Personales")
    else:
        mover_si_existe("Respuestas", resultados_id, "Resultados Seguimiento personal", root_id=root_id)

    # ===========================================================
    # FASE 6: Mover a "Activaciones de permisos"
    # ===========================================================
    log.info("\n── FASE 6: Mover a 'Activaciones de permisos' ──")

    mover_si_existe("Acceso Individual Advisee", activaciones_id, root_id=root_id)
    mover_si_existe("Activaciones Evaluaciones Proyectos", activaciones_id, "Acceso Evaluaciones Proyecto",
                    root_id=root_id)

    # ===========================================================
    # FASE 7: Mover a TO-SEE directamente
    # ===========================================================
    log.info("\n── FASE 7: Mover a 'TO-SEE' ──")

    for nombre_informes in ("Informe finales", "Informes Finales", "Informes finales"):
        r = mover_si_existe(nombre_informes, tosee_id, "Informes finales", root_id=root_id)
        if r:
            break
    for nombre_obj in ("Objetivos empleados", "Objetivos Empleados"):
        r = mover_si_existe(nombre_obj, tosee_id, "Objetivos Empleados", root_id=root_id)
        if r:
            break

    # ===========================================================
    # RESUMEN
    # ===========================================================
    log.info("\n" + "=" * 60)
    log.info("MIGRACIÓN COMPLETADA")
    log.info("IDs importantes (para añadir a .env si es necesario):")
    log.info("  NOTION_TODO_PAGE_ID=%s", todo_id)
    log.info("  NOTION_TOSEE_PAGE_ID=%s", tosee_id)
    log.info("  NOTION_DATOS_MONITORIZAR_PAGE_ID=%s", datos_monitorizar_id)
    log.info("  NOTION_DATOS_MODIFICABLES_PAGE_ID=%s", datos_modificables_id)
    log.info("  NOTION_PREGUNTAS_CHATBOT_PAGE_ID=%s", preguntas_chatbot_id)
    log.info("  NOTION_RESULTADOS_EVAL_PAGE_ID=%s", resultados_id)
    log.info("  NOTION_ACTIVACIONES_PERMISOS_PAGE_ID=%s", activaciones_id)
    log.info("=" * 60)
    log.info("Revisa Notion para confirmar que todo se ha reorganizado correctamente.")
    log.info("Recuerda reiniciar la aplicación después de la migración.")


if __name__ == "__main__":
    main()
