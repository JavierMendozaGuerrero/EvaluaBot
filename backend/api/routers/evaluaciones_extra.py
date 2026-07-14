from fastapi import APIRouter, Body, Depends
from fastapi.responses import JSONResponse

from ..deps import exigir_acceso_advisee, require_session
from ...evaluaciones_extra import (
    guardar_evaluacion_extra,
    obtener_evaluaciones_extra_por_evaluado,
    obtener_solicitudes_pendientes,
    solicitar_evaluacion_extra,
)
from ...notion_service import idioma_por_sesion

router = APIRouter()


@router.get("/api/evaluaciones-extra-pendientes")
def evaluaciones_extra_pendientes(session=Depends(require_session)):
    persona = session.get("persona", "")
    return {"pendientes": obtener_solicitudes_pendientes(persona)}


@router.get("/api/evaluaciones-extra-recibidas")
def evaluaciones_extra_recibidas(evaluado: str = "", session=Depends(require_session)):
    if not evaluado:
        return JSONResponse({"error": "Falta el parámetro evaluado."}, status_code=400)
    exigir_acceso_advisee(session, evaluado)
    return {"evaluaciones": obtener_evaluaciones_extra_por_evaluado(evaluado)}


@router.post("/api/solicitar-evaluacion-extra")
def solicitar_evaluacion_extra_route(datos: dict = Body(default={}), session=Depends(require_session)):
    evaluado = session.get("persona", "")
    idi = idioma_por_sesion(session)
    evaluador = datos.get("evaluador", "").strip()
    contexto = datos.get("contexto", "").strip()
    if not evaluador or not contexto:
        return JSONResponse({"error": "Faltan campos obligatorios."}, status_code=400)
    return solicitar_evaluacion_extra(evaluado, evaluador, contexto, idi)


@router.post("/api/guardar-evaluacion-extra")
def guardar_evaluacion_extra_route(datos: dict = Body(default={}), session=Depends(require_session)):
    evaluador = session.get("persona", "")
    evaluado = datos.get("evaluado", "").strip()
    contexto = datos.get("contexto", "").strip()
    nota = datos.get("nota")
    justificacion = datos.get("justificacion", "").strip()
    solicitud_page_id = datos.get("solicitudPageId", "").strip()
    if not evaluado or not justificacion or nota not in (1, 2, 3, 4, 5):
        return JSONResponse({"error": "Faltan campos obligatorios o la nota no es válida (1-5)."}, status_code=400)
    ok = guardar_evaluacion_extra(evaluado, evaluador, contexto, nota, justificacion, solicitud_page_id)
    if ok:
        return {"ok": True}
    return JSONResponse({"error": "No se pudo guardar la evaluación en Notion."}, status_code=500)
