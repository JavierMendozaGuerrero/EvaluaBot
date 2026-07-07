from fastapi import APIRouter, Body, Depends
from fastapi.responses import JSONResponse

from ..deps import require_session
from ...anonimato import cargar_config as cargar_anonimato, evaluadores_visibles_para_advisee
from ...i18n import t
from ...notion_service import idioma_por_sesion
from ...project_evals import (
    LABELS_TIPOS,
    activar_evaluaciones_empleados,
    añadir_miembro_proyecto,
    eliminar_miembro_proyecto,
    enviar_recordatorios_proyecto,
    guardar_evaluacion_proyecto,
    obtener_equipo_proyecto,
    obtener_estado_evaluaciones_proyecto,
    obtener_evals_completadas_proyecto,
    obtener_preguntas_tipo,
    obtener_progreso_proyectos_empleado,
    obtener_proyectos_activos_empleado,
    obtener_proyectos_manager,
)

router = APIRouter()


@router.get("/api/evaluaciones-proyecto-activas")
def evaluaciones_proyecto_activas(session=Depends(require_session)):
    persona = session.get("persona", "")
    return {"proyectos": obtener_proyectos_activos_empleado(persona)}


@router.get("/api/proyectos-progreso")
def proyectos_progreso(session=Depends(require_session)):
    """Equipo + evals completadas de CADA proyecto activo de la persona, en una sola
    respuesta (sustituye el waterfall de 1 + 2N peticiones que hacía el dashboard)."""
    persona = session.get("persona", "")
    return {"proyectos": obtener_progreso_proyectos_empleado(persona)}


@router.get("/api/evaluaciones-proyecto-completadas")
def evaluaciones_proyecto_completadas(proyecto: str = "", session=Depends(require_session)):
    if not proyecto:
        return JSONResponse({"error": "Falta el parámetro proyecto."}, status_code=400)
    persona = session.get("persona", "")
    return {"completadas": obtener_evals_completadas_proyecto(persona, proyecto)}


@router.get("/api/preguntas-evaluacion-proyecto")
def preguntas_evaluacion_proyecto(tipo: str = "", session=Depends(require_session)):
    if tipo not in LABELS_TIPOS:
        return JSONResponse({"error": "Tipo no válido."}, status_code=400)
    return {"preguntas": obtener_preguntas_tipo(tipo, idioma_por_sesion(session))}


@router.get("/api/equipo-proyecto")
def equipo_proyecto(proyecto: str = "", session=Depends(require_session)):
    empleados = obtener_equipo_proyecto(proyecto) if proyecto else []
    return {"empleados": empleados}


@router.get("/api/proyectos-manager")
def proyectos_manager(session=Depends(require_session)):
    persona = session.get("persona", "")
    return {"proyectos": obtener_proyectos_manager(persona)}


@router.get("/api/estado-proyecto")
def estado_proyecto(proyecto: str = "", session=Depends(require_session)):
    if not proyecto:
        return JSONResponse({"error": "Falta el proyecto."}, status_code=400)
    estado = obtener_estado_evaluaciones_proyecto(proyecto)
    cfg_anon = cargar_anonimato()
    es_admin = session.get("is_admin", False)
    for m in estado:
        m["n_pendientes"] = len(m["pendientes"])
        if not (es_admin or evaluadores_visibles_para_advisee(m.get("evaluado", ""), cfg_anon)):
            m["evaluadores"] = []
            m["pendientes"] = []
    return {"estado": estado}


@router.post("/api/activar-evaluaciones-proyecto")
def activar_evaluaciones_proyecto(datos: dict = Body(default={}), session=Depends(require_session)):
    manager = session.get("persona", "")
    idi = idioma_por_sesion(session)
    proyecto = datos.get("proyecto", "").strip()
    empleados = datos.get("empleados", [])
    if not proyecto:
        return JSONResponse({"error": t("pe.err_missing_project", idi)}, status_code=400)
    if not empleados or not isinstance(empleados, list):
        return JSONResponse({"error": t("pe.err_select_employee", idi)}, status_code=400)
    return activar_evaluaciones_empleados(manager, proyecto, empleados, idi)


@router.post("/api/modificar-equipo-proyecto")
def modificar_equipo_proyecto(datos: dict = Body(default={}), session=Depends(require_session)):
    manager = session.get("persona", "")
    idi = idioma_por_sesion(session)
    accion = datos.get("accion", "").strip()
    proyecto = datos.get("proyecto", "").strip()
    empleado = datos.get("empleado", "").strip()
    if accion not in ("añadir", "eliminar") or not proyecto or not empleado:
        return JSONResponse({"error": t("pe.err_missing_fields", idi)}, status_code=400)
    if accion == "añadir":
        return añadir_miembro_proyecto(manager, proyecto, empleado, idi)
    return eliminar_miembro_proyecto(proyecto, empleado, idi)


@router.post("/api/recordatorio-proyecto")
def recordatorio_proyecto(datos: dict = Body(default={}), session=Depends(require_session)):
    manager = session.get("persona", "")
    idi = idioma_por_sesion(session)
    proyecto = datos.get("proyecto", "").strip()
    if not proyecto:
        return JSONResponse({"error": t("pe.err_missing_fields", idi)}, status_code=400)
    proyectos_mgr = {p["nombre_proyecto"] for p in obtener_proyectos_manager(manager)}
    if proyecto not in proyectos_mgr:
        return JSONResponse({"error": t("pe.err_not_your_project", idi)}, status_code=403)
    resultado = enviar_recordatorios_proyecto(proyecto)
    return {"ok": True, **resultado}


@router.post("/api/guardar-evaluacion-proyecto")
def guardar_evaluacion_proyecto_route(datos: dict = Body(default={}), session=Depends(require_session)):
    evaluador = session.get("persona", "")
    proyecto = datos.get("proyecto", "").strip()
    tipo = datos.get("tipo", "").strip()
    evaluado = datos.get("evaluado", "").strip()
    respuestas = datos.get("respuestas", {})
    if not proyecto or not tipo or not evaluado:
        return JSONResponse({"error": "Faltan campos obligatorios."}, status_code=400)
    if tipo not in LABELS_TIPOS:
        return JSONResponse({"error": "Tipo de evaluación no válido."}, status_code=400)
    preguntas = obtener_preguntas_tipo(tipo, idioma_por_sesion(session))
    ok = guardar_evaluacion_proyecto(evaluador, evaluado, proyecto, tipo, respuestas, preguntas)
    if ok:
        return {"ok": True}
    return JSONResponse({"error": "No se pudo guardar la evaluación en Notion."}, status_code=500)
