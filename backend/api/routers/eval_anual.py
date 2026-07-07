from fastapi import APIRouter, Body, Depends
from fastapi.responses import JSONResponse

from ..deps import exigir_acceso_advisee, require_admin, require_session
from ..files import url_archivo
from ... import eval_anual_sesion as eval_sesion
from ...skill_informes_anual import generar_informe_anual, obtener_empleados_evaluacion_anual
from ...utils import slug_archivo

router = APIRouter()


@router.get("/api/evaluados-anual")
def evaluados_anual(
    session=Depends(require_admin("Solo administradores pueden acceder a las evaluaciones anuales.")),
):
    nombres = obtener_empleados_evaluacion_anual()
    return {"evaluados": [{"value": n, "label": n} for n in nombres]}


@router.post("/api/generar-anual")
def generar_anual(
    datos: dict = Body(default={}),
    session=Depends(require_admin("Solo administradores pueden generar informes anuales.")),
):
    evaluado = datos.get("evaluado", "").strip()
    cargo = datos.get("cargo", "").strip()
    if not evaluado:
        return JSONResponse({"error": "Selecciona un empleado."}, status_code=400)
    slug = generar_informe_anual(evaluado, cargo=cargo)
    return {
        "docxUrl": url_archivo(f"informe_anual_{slug}.docx", evaluado),
        "htmlUrl": url_archivo(f"informe_anual_{slug}.html", evaluado),
    }


def _requiere_evaluado(evaluado: str, session):
    evaluado = (evaluado or "").strip()
    if not evaluado:
        return None
    exigir_acceso_advisee(session, evaluado)
    return evaluado


@router.get("/api/eval-anual/estado")
def eval_anual_estado(evaluado: str = "", session=Depends(require_session)):
    evaluado = _requiere_evaluado(evaluado, session)
    if evaluado is None:
        return JSONResponse({"error": "Falta el parámetro evaluado."}, status_code=400)
    return eval_sesion.estado_sesion(evaluado)


@router.get("/api/eval-anual/area")
def eval_anual_area(evaluado: str = "", clave: str = "", session=Depends(require_session)):
    evaluado = _requiere_evaluado(evaluado, session)
    if evaluado is None:
        return JSONResponse({"error": "Falta el parámetro evaluado."}, status_code=400)
    return eval_sesion.obtener_area(evaluado, clave.strip())


@router.get("/api/eval-anual/plan")
def eval_anual_plan(evaluado: str = "", session=Depends(require_session)):
    evaluado = _requiere_evaluado(evaluado, session)
    if evaluado is None:
        return JSONResponse({"error": "Falta el parámetro evaluado."}, status_code=400)
    return eval_sesion.obtener_plan_accion(evaluado)


@router.get("/api/eval-anual/plan-guardado")
def eval_anual_plan_guardado(evaluado: str = "", session=Depends(require_session)):
    evaluado = _requiere_evaluado(evaluado, session)
    if evaluado is None:
        return JSONResponse({"error": "Falta el parámetro evaluado."}, status_code=400)
    return eval_sesion.obtener_plan_guardado(evaluado)


@router.post("/api/eval-anual/iniciar")
def eval_anual_iniciar(datos: dict = Body(default={}), session=Depends(require_session)):
    evaluado = _requiere_evaluado(datos.get("evaluado", ""), session)
    if evaluado is None:
        return JSONResponse({"error": "Falta el campo evaluado."}, status_code=400)
    return eval_sesion.iniciar_sesion(evaluado, cargo=datos.get("cargo", "").strip())


@router.post("/api/eval-anual/confirmar-identidad")
def eval_anual_confirmar_identidad(datos: dict = Body(default={}), session=Depends(require_session)):
    evaluado = _requiere_evaluado(datos.get("evaluado", ""), session)
    if evaluado is None:
        return JSONResponse({"error": "Falta el campo evaluado."}, status_code=400)
    return eval_sesion.confirmar_identidad(evaluado)


@router.post("/api/eval-anual/eliminar")
def eval_anual_eliminar(datos: dict = Body(default={}), session=Depends(require_session)):
    evaluado = _requiere_evaluado(datos.get("evaluado", ""), session)
    if evaluado is None:
        return JSONResponse({"error": "Falta el campo evaluado."}, status_code=400)
    return eval_sesion.eliminar_sesion(evaluado)


@router.post("/api/eval-anual/responder-area")
def eval_anual_responder_area(datos: dict = Body(default={}), session=Depends(require_session)):
    evaluado = _requiere_evaluado(datos.get("evaluado", ""), session)
    if evaluado is None:
        return JSONResponse({"error": "Falta el campo evaluado."}, status_code=400)
    return eval_sesion.responder_area(evaluado, datos.get("clave", "").strip(), datos.get("texto", ""))


@router.post("/api/eval-anual/confirmar-area")
def eval_anual_confirmar_area(datos: dict = Body(default={}), session=Depends(require_session)):
    evaluado = _requiere_evaluado(datos.get("evaluado", ""), session)
    if evaluado is None:
        return JSONResponse({"error": "Falta el campo evaluado."}, status_code=400)
    return eval_sesion.confirmar_area(evaluado, datos.get("clave", "").strip())


@router.post("/api/eval-anual/plan-cambios")
def eval_anual_plan_cambios(datos: dict = Body(default={}), session=Depends(require_session)):
    evaluado = _requiere_evaluado(datos.get("evaluado", ""), session)
    if evaluado is None:
        return JSONResponse({"error": "Falta el campo evaluado."}, status_code=400)
    return eval_sesion.pedir_cambios_plan(evaluado, datos.get("instruccion", ""))


@router.post("/api/eval-anual/plan-guardar")
def eval_anual_plan_guardar(datos: dict = Body(default={}), session=Depends(require_session)):
    evaluado = _requiere_evaluado(datos.get("evaluado", ""), session)
    if evaluado is None:
        return JSONResponse({"error": "Falta el campo evaluado."}, status_code=400)
    return eval_sesion.guardar_plan_accion(evaluado, datos.get("texto", ""))


@router.post("/api/eval-anual/finalizar")
def eval_anual_finalizar(datos: dict = Body(default={}), session=Depends(require_session)):
    evaluado = _requiere_evaluado(datos.get("evaluado", ""), session)
    if evaluado is None:
        return JSONResponse({"error": "Falta el campo evaluado."}, status_code=400)
    res = eval_sesion.finalizar_sesion(evaluado)
    slug_fin = slug_archivo(evaluado)
    res["htmlUrl"] = url_archivo(f"informe_anual_{slug_fin}.html", evaluado)
    res["docxUrl"] = url_archivo(f"informe_anual_{slug_fin}.docx", evaluado)
    return res
