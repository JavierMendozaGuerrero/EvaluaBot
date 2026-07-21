from fastapi import APIRouter, Body, Depends
from fastapi.responses import JSONResponse

import logging

from ..deps import exigir_acceso_advisee, require_admin, require_session
from ..files import url_archivo
from ... import eval_anual_sesion as eval_sesion
from ...notion_service import guardar_informe_final_estructurado, idioma_por_sesion
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
def eval_anual_plan(evaluado: str = "", forzar: bool = False, session=Depends(require_session)):
    evaluado = _requiere_evaluado(evaluado, session)
    if evaluado is None:
        return JSONResponse({"error": "Falta el parámetro evaluado."}, status_code=400)
    return eval_sesion.obtener_plan_accion(evaluado, forzar=forzar)


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
    # El informe se redacta en el idioma del CA: es el que ve la plantilla y el que
    # decide cómo sale el Word que acabará leyendo el advisee.
    return eval_sesion.iniciar_sesion(evaluado, cargo=datos.get("cargo", "").strip(),
                                      idioma=idioma_por_sesion(session))


@router.post("/api/eval-anual/iniciar-manual")
def eval_anual_iniciar_manual(datos: dict = Body(default={}), session=Depends(require_session)):
    """Abre el informe para rellenarlo manualmente en la web (Word editable en blanco)."""
    evaluado = _requiere_evaluado(datos.get("evaluado", ""), session)
    if evaluado is None:
        return JSONResponse({"error": "Falta el campo evaluado."}, status_code=400)
    return eval_sesion.iniciar_manual(evaluado, cargo=datos.get("cargo", "").strip(),
                                      idioma=idioma_por_sesion(session))


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


@router.post("/api/eval-anual/actualizar-plantilla")
def eval_anual_actualizar_plantilla(datos: dict = Body(default={}), session=Depends(require_session)):
    evaluado = _requiere_evaluado(datos.get("evaluado", ""), session)
    if evaluado is None:
        return JSONResponse({"error": "Falta el campo evaluado."}, status_code=400)
    return eval_sesion.actualizar_plantilla(evaluado)


@router.post("/api/eval-anual/responder-area")
def eval_anual_responder_area(datos: dict = Body(default={}), session=Depends(require_session)):
    evaluado = _requiere_evaluado(datos.get("evaluado", ""), session)
    if evaluado is None:
        return JSONResponse({"error": "Falta el campo evaluado."}, status_code=400)
    return eval_sesion.responder_area(evaluado, datos.get("clave", "").strip(), datos.get("texto", ""))


@router.post("/api/eval-anual/resumen-area")
def eval_anual_resumen_area(datos: dict = Body(default={}), session=Depends(require_session)):
    """Sugerencia final del área (criterio a criterio), generada solo cuando el CA la pide."""
    evaluado = _requiere_evaluado(datos.get("evaluado", ""), session)
    if evaluado is None:
        return JSONResponse({"error": "Falta el campo evaluado."}, status_code=400)
    return eval_sesion.generar_resumen_area(evaluado, datos.get("clave", "").strip())


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


@router.post("/api/eval-anual/plan-chat")
def eval_anual_plan_chat(datos: dict = Body(default={}), session=Depends(require_session)):
    evaluado = _requiere_evaluado(datos.get("evaluado", ""), session)
    if evaluado is None:
        return JSONResponse({"error": "Falta el parámetro evaluado."}, status_code=400)
    try:
        return eval_sesion.chatear_plan(evaluado, datos.get("mensajes", []))
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@router.post("/api/eval-anual/plan-guardar")
def eval_anual_plan_guardar(datos: dict = Body(default={}), session=Depends(require_session)):
    evaluado = _requiere_evaluado(datos.get("evaluado", ""), session)
    if evaluado is None:
        return JSONResponse({"error": "Falta el campo evaluado."}, status_code=400)
    return eval_sesion.guardar_plan_accion(evaluado, datos.get("texto", ""))


@router.get("/api/eval-anual/borrador")
def eval_anual_borrador(evaluado: str = "", session=Depends(require_session)):
    evaluado = _requiere_evaluado(evaluado, session)
    if evaluado is None:
        return JSONResponse({"error": "Falta el parámetro evaluado."}, status_code=400)
    return eval_sesion.obtener_borrador(evaluado)


@router.post("/api/eval-anual/borrador-guardar")
def eval_anual_borrador_guardar(datos: dict = Body(default={}), session=Depends(require_session)):
    evaluado = _requiere_evaluado(datos.get("evaluado", ""), session)
    if evaluado is None:
        return JSONResponse({"error": "Falta el campo evaluado."}, status_code=400)
    res = eval_sesion.guardar_borrador(evaluado, datos.get("borrador") or {})
    # Persiste también el borrador en Notion (Estado='Borrador') para que no dependa de la
    # caché local: si el contenedor se reinicia, el borrador se recupera desde Notion.
    borrador = res.get("borrador") if isinstance(res, dict) else None
    if borrador:
        try:
            ca_nombre = session.get("persona", "") if not session.get("is_admin") else ""
            guardado = guardar_informe_final_estructurado(ca_nombre, borrador, estado="Borrador")
            res["notionUrl"] = guardado.get("url", "")
        except Exception:
            logging.exception("No se pudo persistir el borrador en Notion para %s", evaluado)
    return res


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
