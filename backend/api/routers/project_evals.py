from datetime import datetime, timedelta, timezone

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
    construir_evaluaciones_a_hacer,
    eliminar_borrador_evaluacion_proyecto,
    eliminar_miembro_proyecto,
    enviar_recordatorios_proyecto,
    filtrar_liderazgo_autoeval,
    guardar_borrador_evaluacion_proyecto,
    guardar_evaluacion_proyecto,
    obtener_borrador_evaluacion_proyecto,
    obtener_equipo_proyecto,
    obtener_estado_evaluaciones_proyecto,
    obtener_evals_completadas_proyecto,
    obtener_evaluaciones_proyecto_por_evaluado,
    obtener_evaluaciones_proyecto_por_evaluador,
    obtener_preguntas_tipo,
    obtener_progreso_proyectos_empleado,
    obtener_proyectos_activos_empleado,
    obtener_proyectos_manager,
    tipo_evaluacion_por_jerarquia,
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
    preguntas = obtener_preguntas_tipo(tipo, idioma_por_sesion(session))
    if tipo == "autoevaluacion":
        preguntas = filtrar_liderazgo_autoeval(preguntas, session.get("persona", ""))
    return {"preguntas": preguntas}


@router.get("/api/equipo-proyecto")
def equipo_proyecto(proyecto: str = "", session=Depends(require_session)):
    empleados = obtener_equipo_proyecto(proyecto) if proyecto else []
    return {"empleados": empleados}


@router.get("/api/evaluaciones-proyecto-a-hacer")
def evaluaciones_proyecto_a_hacer(proyecto: str = "", session=Depends(require_session)):
    """Evaluaciones que la persona debe hacer en el proyecto, con el tipo de plantilla
    decidido por JERARQUÍA DE EMPRESA (cargo en Notion), no por rol en el proyecto."""
    if not proyecto:
        return JSONResponse({"error": "Falta el parámetro proyecto."}, status_code=400)
    persona = session.get("persona", "")
    equipo = obtener_equipo_proyecto(proyecto)
    return {"evaluaciones": construir_evaluaciones_a_hacer(persona, equipo)}


@router.get("/api/mis-evaluaciones-proyecto-recibidas")
def mis_evaluaciones_proyecto_recibidas(session=Depends(require_session)):
    """Evaluaciones de proyecto RECIBIDAS por la persona y liberadas para ella.

    Solo devuelve filas con Visible_evaluado=True (evaluaciones top-to-bottom, de
    alguien por encima en la jerarquía de empresa). Las bottom-to-top y las de mismo
    nivel NUNCA se devuelven aquí: siguen siendo visibles solo para el CA.
    """
    persona = session.get("persona", "")
    visibles = [
        e for e in obtener_evaluaciones_proyecto_por_evaluado(persona)
        if e.get("visible_evaluado")
    ]
    visibles.sort(key=lambda x: x.get("fecha", ""), reverse=True)
    return {"evaluaciones": visibles}


@router.get("/api/proyectos-manager")
def proyectos_manager(session=Depends(require_session)):
    persona = session.get("persona", "")
    return {"proyectos": obtener_proyectos_manager(persona)}


@router.get("/api/mis-evaluaciones-proyecto-realizadas")
def mis_evaluaciones_proyecto_realizadas(session=Depends(require_session)):
    """Proyectos de los que la persona ha realizado evals de proyecto en los últimos 2 años,
    cada uno con la lista de evaluaciones que hizo."""
    persona = session.get("persona", "")
    desde = (datetime.now(timezone.utc) - timedelta(days=365 * 2)).date().isoformat()
    return {"proyectos": obtener_evaluaciones_proyecto_por_evaluador(persona, desde)}


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
    resultado = enviar_recordatorios_proyecto(proyecto, manager)
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
    # El tipo lo decide el SERVIDOR por jerarquía de empresa (no confiamos en el que
    # manda el cliente, que podría venir de la lógica antigua por rol de proyecto).
    tipo_calc, relacion = tipo_evaluacion_por_jerarquia(evaluador, evaluado)
    # Solo las top-to-bottom estrictas (evaluador por ENCIMA) se liberan al evaluado.
    visible = relacion == "superior" and tipo_calc != "autoevaluacion"
    preguntas = obtener_preguntas_tipo(tipo_calc, idioma_por_sesion(session))
    if tipo_calc == "autoevaluacion":
        preguntas = filtrar_liderazgo_autoeval(preguntas, evaluador)
    ok = guardar_evaluacion_proyecto(
        evaluador, evaluado, proyecto, tipo_calc, respuestas, preguntas,
        visible_evaluado=visible,
    )
    if ok:
        eliminar_borrador_evaluacion_proyecto(evaluador, proyecto, tipo_calc, evaluado)
        if tipo != tipo_calc:
            eliminar_borrador_evaluacion_proyecto(evaluador, proyecto, tipo, evaluado)
        return {"ok": True, "tipo": tipo_calc, "relacion": relacion}
    return JSONResponse({"error": "No se pudo guardar la evaluación en Notion."}, status_code=500)


@router.get("/api/borrador-evaluacion-proyecto")
def borrador_evaluacion_proyecto_get(proyecto: str = "", tipo: str = "", evaluado: str = "", session=Depends(require_session)):
    if not proyecto or tipo not in LABELS_TIPOS:
        return JSONResponse({"error": "Faltan campos obligatorios."}, status_code=400)
    evaluador = session.get("persona", "")
    return {"borrador": obtener_borrador_evaluacion_proyecto(evaluador, proyecto, tipo, evaluado)}


@router.post("/api/borrador-evaluacion-proyecto")
def borrador_evaluacion_proyecto_post(datos: dict = Body(default={}), session=Depends(require_session)):
    evaluador = session.get("persona", "")
    proyecto = datos.get("proyecto", "").strip()
    tipo = datos.get("tipo", "").strip()
    evaluado = datos.get("evaluado", "").strip()
    respuestas = datos.get("respuestas", {})
    if not proyecto or tipo not in LABELS_TIPOS or not isinstance(respuestas, dict):
        return JSONResponse({"error": "Faltan campos obligatorios."}, status_code=400)
    ok = guardar_borrador_evaluacion_proyecto(evaluador, proyecto, tipo, evaluado, respuestas)
    if ok:
        return {"ok": True}
    return JSONResponse({"error": "No se pudo guardar el borrador."}, status_code=500)


@router.post("/api/borrador-evaluacion-proyecto/eliminar")
def borrador_evaluacion_proyecto_eliminar(datos: dict = Body(default={}), session=Depends(require_session)):
    evaluador = session.get("persona", "")
    proyecto = datos.get("proyecto", "").strip()
    tipo = datos.get("tipo", "").strip()
    evaluado = datos.get("evaluado", "").strip()
    if not proyecto or tipo not in LABELS_TIPOS:
        return JSONResponse({"error": "Faltan campos obligatorios."}, status_code=400)
    eliminar_borrador_evaluacion_proyecto(evaluador, proyecto, tipo, evaluado)
    return {"ok": True}
