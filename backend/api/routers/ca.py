from fastapi import APIRouter, Body, Depends
from fastapi.responses import JSONResponse

from ..deps import exigir_acceso_advisee, require_admin, require_session
from ...anonimato import cargar_config as cargar_anonimato, evaluadores_visibles_para_advisee
from ...ca_reviews import guardar_nota_ca_web, notificar_acceso_informe_final_web, obtener_resumen_advisee_para_ca
from ...eval_tracking import detalle_por_persona, resumen_ciclo_actual
from ...notion_service import (
    advisee_tiene_acceso_individual,
    ca_tiene_acceso_activo,
    guardar_objetivo_persona,
    idioma_por_sesion,
    mover_objetivo_a_antiguos,
    obtener_advisees,
    obtener_criterios_evaluacion,
    obtener_feedback_confidencial_por_evaluado,
    obtener_historial_mis_evaluaciones,
    obtener_objetivos_persona,
    obtener_opiniones_ca_por_advisee,
    obtener_todo_el_feedback_confidencial,
    toggle_acceso_advisee_individual,
    toggle_acceso_advisees,
)
from ...utils import normalizar_nombre

router = APIRouter()

_GRUPO_NOTION = {"negocio": "Negocio", "palantir": "Palantir", "middleoffice": "MiddleOffice"}


@router.get("/api/opiniones-ca")
def opiniones_ca(advisee: str = "", session=Depends(require_session)):
    ca_nombre = session.get("persona", "")
    opiniones = obtener_opiniones_ca_por_advisee(
        ca_nombre, advisee, ca_aliases=[session.get("username", ""), session.get("email", "")],
    )
    cfg_anon = cargar_anonimato()
    if not (session.get("is_admin") or evaluadores_visibles_para_advisee(advisee, cfg_anon)):
        for op in opiniones:
            op["resumen_advisee"] = ""
    return {"opiniones": opiniones}


@router.get("/api/objetivos")
def objetivos_get(nombre: str = "", antiguos: bool = False, session=Depends(require_session)):
    # Una persona siempre puede ver sus propios objetivos VIGENTES (p.ej. sección "Mis
    # objetivos" de su perfil); si consulta los de otra persona, debe ser su CA (o admin).
    # Los antiguos son solo para el CA: al advisee solo se le muestran los actuales.
    es_propio = normalizar_nombre(nombre) == normalizar_nombre(session.get("persona", ""))
    if antiguos or not es_propio:
        exigir_acceso_advisee(session, nombre)
    return {"objetivos": obtener_objetivos_persona(nombre, antiguos=antiguos)}


@router.post("/api/objetivos")
def objetivos_post(datos: dict = Body(default={}), session=Depends(require_session)):
    ca_nombre = session.get("persona", "")
    nombre = datos.get("nombre", "")
    titulo = datos.get("titulo", "").strip()
    kpis = datos.get("kpis", "").strip()
    descripcion = datos.get("descripcion", "").strip()
    tipo = datos.get("tipo", "").strip()
    if not nombre or not titulo:
        return JSONResponse({"error": "Faltan campos obligatorios (nombre y título)."}, status_code=400)
    exigir_acceso_advisee(session, nombre)
    guardar_objetivo_persona(ca_nombre, nombre, titulo, kpis, descripcion, tipo)
    return {"ok": True}


@router.delete("/api/objetivos")
def objetivos_delete(datos: dict = Body(default={}), session=Depends(require_session)):
    page_id = datos.get("page_id", "")
    nombre = datos.get("nombre", "")
    if not page_id or not nombre:
        return JSONResponse({"error": "Faltan page_id y nombre."}, status_code=400)
    # Autorización: puede cerrar un objetivo el propio interesado o su CA (o admin), y el
    # objetivo debe pertenecer realmente a esa persona (evita cerrar por page_id ajeno).
    es_propio = normalizar_nombre(nombre) == normalizar_nombre(session.get("persona", ""))
    if not es_propio:
        exigir_acceso_advisee(session, nombre)
    objetivo = next((o for o in obtener_objetivos_persona(nombre) if o.get("page_id") == page_id), None)
    if objetivo is None:
        raise PermissionError("Ese objetivo no pertenece a la persona indicada.")
    # No se borra: pasa a la base de objetivos antiguos, con quién lo cerró y cuándo.
    ok = mover_objetivo_a_antiguos(nombre, objetivo, session.get("persona", ""))
    return {"ok": ok}


@router.get("/api/acceso-advisees")
def acceso_advisees_get(session=Depends(require_session)):
    ca_aliases_sesion = [session.get("username", ""), session.get("email", "")]
    activo = ca_tiene_acceso_activo(session.get("persona", ""), ca_aliases=ca_aliases_sesion)
    return {"activo": activo}


@router.post("/api/acceso-advisees")
def acceso_advisees_post(datos: dict = Body(default={}), session=Depends(require_session)):
    activo = datos.get("activo", False)
    ca_aliases_sesion = [session.get("username", ""), session.get("email", "")]
    exito = toggle_acceso_advisees(session.get("persona", ""), activo, ca_aliases=ca_aliases_sesion)
    if not exito:
        raise RuntimeError("No se encontró tu fila en Lista CA. Contacta con el administrador.")
    return {"ok": True, "activo": activo}


@router.get("/api/acceso-advisee-individual")
def acceso_advisee_individual_get(advisee: str = "", session=Depends(require_session)):
    if not advisee:
        return JSONResponse({"error": "Falta el parámetro advisee."}, status_code=400)
    activo = advisee_tiene_acceso_individual(advisee, session.get("persona", ""))
    return {"activo": activo}


@router.post("/api/acceso-advisee-individual")
def acceso_advisee_individual_post(datos: dict = Body(default={}), session=Depends(require_session)):
    advisee_nombre = datos.get("advisee", "")
    activo = datos.get("activo", False)
    if not advisee_nombre:
        return JSONResponse({"error": "Falta el campo advisee."}, status_code=400)
    exito = toggle_acceso_advisee_individual(session.get("persona", ""), advisee_nombre, activo)
    if not exito:
        raise RuntimeError("No se pudo actualizar el acceso individual.")
    if activo:
        notificar_acceso_informe_final_web(advisee_nombre)
    return {"ok": True, "activo": activo}


@router.post("/api/notas-ca")
def notas_ca(datos: dict = Body(default={}), session=Depends(require_session)):
    advisee_nombre = datos.get("advisee", "").strip()
    nota = datos.get("nota", "").strip()
    if not advisee_nombre or not nota:
        return JSONResponse({"error": "Faltan datos"}, status_code=400)
    # Autorización: solo el CA (o admin) de esa persona puede escribir notas sobre ella.
    exigir_acceso_advisee(session, advisee_nombre)
    ca_nombre = session.get("persona", "")
    ok, err = guardar_nota_ca_web(ca_nombre, advisee_nombre, nota)
    return {"ok": ok, "error": err}


@router.get("/api/resumen-evaluaciones-advisee")
def resumen_evaluaciones_advisee(advisee: str = "", session=Depends(require_session)):
    if not advisee:
        return JSONResponse({"error": "Falta el advisee."}, status_code=400)
    ca_nombre = session.get("persona", "")
    ca_aliases = [session.get("username", ""), session.get("email", "")]
    advisees_ca = obtener_advisees(ca_nombre, ca_aliases=ca_aliases)
    if normalizar_nombre(advisee) not in [normalizar_nombre(a) for a in advisees_ca]:
        raise PermissionError("No tienes acceso a las evaluaciones de este advisee.")
    resumen, sin_novedades = obtener_resumen_advisee_para_ca(ca_nombre, advisee)
    return {"resumen": resumen, "sinNovedades": sin_novedades}


@router.get("/api/historial-evaluaciones")
def historial_evaluaciones(
    evaluado: str = "", evaluador: str = "", proyecto: str = "", session=Depends(require_session)
):
    if not evaluado or not evaluador:
        return JSONResponse({"error": "Faltan parámetros."}, status_code=400)
    if not session.get("is_admin") and normalizar_nombre(evaluador) != normalizar_nombre(session.get("persona", "")):
        raise PermissionError("Solo puedes consultar tu propio historial de evaluaciones.")
    historial = obtener_historial_mis_evaluaciones(evaluado, evaluador, proyecto)
    return {"historial": historial}


@router.get("/api/feedback-confidencial")
def feedback_confidencial(
    evaluado: str = "",
    session=Depends(require_admin("Solo administradores pueden acceder a este contenido.")),
):
    if not evaluado:
        return JSONResponse({"error": "Falta el parámetro evaluado."}, status_code=400)
    try:
        feedback = obtener_feedback_confidencial_por_evaluado(evaluado)
    except RuntimeError:
        feedback = []
    return {"feedback": feedback}


@router.get("/api/feedback-confidencial-todos")
def feedback_confidencial_todos(
    session=Depends(require_admin("Solo administradores pueden acceder a este contenido.")),
):
    return {"feedback": obtener_todo_el_feedback_confidencial()}


@router.get("/api/criterios-evaluacion")
def criterios_evaluacion(grupo: str = "negocio", session=Depends(require_session)):
    notion_grupo = _GRUPO_NOTION.get(grupo, grupo)
    criterios = obtener_criterios_evaluacion(notion_grupo, idioma_por_sesion(session))
    return {"criterios": criterios}


@router.get("/api/cumplimiento-evaluaciones")
def cumplimiento_evaluaciones(
    session=Depends(require_admin("Solo administradores pueden consultar el cumplimiento de evaluaciones.")),
):
    return {"cumplimiento": resumen_ciclo_actual()}


@router.get("/api/cumplimiento-evaluaciones-detalle")
def cumplimiento_evaluaciones_detalle(
    nombre: str = "",
    session=Depends(require_admin("Solo administradores pueden consultar el cumplimiento de evaluaciones.")),
):
    if not nombre:
        return JSONResponse({"error": "Falta el parámetro nombre."}, status_code=400)
    return {"detalle": detalle_por_persona(nombre)}
