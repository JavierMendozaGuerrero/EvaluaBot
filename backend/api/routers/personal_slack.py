import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Body, Depends
from fastapi.responses import JSONResponse

from ..deps import require_session
from ...eval_tracking import pendientes_slack_de_persona
from ...hierarchy import comparar_jerarquia, tipo_relacion
from ...notion_service import (
    actualizar_en_notion,
    buscar_empleado_y_cargo,
    evaluacion_personal_guardada_desde,
    evaluacion_proyecto_guardada_desde,
    guardar_en_notion,
    guardar_evaluacion_personal,
    idioma_por_sesion,
    obtener_config_calendario,
    obtener_evaluados_middleoffice,
    obtener_perfil_empleado,
    obtener_preguntas_desde_notion,
    obtener_preguntas_mo,
    obtener_preguntas_palantir,
    siguiente_envio_calendario,
    sugerir_empleados_parecidos,
)
from ...personal_eval import notificar_urgencia_personal_web
from ...utils import normalizar_nombre

router = APIRouter()

_slack_deeplink_cache = {"url": None}

_AREA_DISPLAY = {"negocio": "Negocio", "middleoffice": "MiddleOffice", "palantir": "Palantir"}


def _slack_deeplink() -> str:
    """Deep-link que abre el DM con el bot en la app de Slack (no el chat web). Cacheado."""
    if _slack_deeplink_cache["url"] is None:
        try:
            from ...clients import slack_app

            a = slack_app.client.auth_test()
            _slack_deeplink_cache["url"] = f"slack://user?team={a['team_id']}&id={a['user_id']}"
        except Exception:
            logging.exception("No se pudo obtener el deep-link de Slack")
            _slack_deeplink_cache["url"] = "slack://open"
    return _slack_deeplink_cache["url"]


@router.get("/api/tareas-slack")
def tareas_slack(session=Depends(require_session)):
    persona = session.get("persona", "")
    return {"pendientes": pendientes_slack_de_persona(persona), "slackUrl": _slack_deeplink()}


@router.get("/api/estado-ciclo-slack")
def estado_ciclo_slack(session=Depends(require_session)):
    persona = session.get("persona", "")
    completadas = {"proyecto": False, "personal": False}
    fallback_5w = (datetime.now(timezone.utc) - timedelta(weeks=5)).timestamp()
    try:
        cal = obtener_config_calendario()
        fecha_proyecto = cal.get("proyecto_ca")
        if fecha_proyecto:
            siguiente = siguiente_envio_calendario(fecha_proyecto, 4)
            ultimo = siguiente - timedelta(weeks=4)
            completadas["proyecto"] = evaluacion_proyecto_guardada_desde(persona, ultimo.timestamp())
        else:
            completadas["proyecto"] = evaluacion_proyecto_guardada_desde(persona, fallback_5w)
        fecha_personal = cal.get("personal")
        if fecha_personal:
            siguiente_p = siguiente_envio_calendario(fecha_personal, 4)
            ultimo_p = siguiente_p - timedelta(weeks=4)
            completadas["personal"] = evaluacion_personal_guardada_desde(persona, ultimo_p.timestamp())
        else:
            completadas["personal"] = evaluacion_personal_guardada_desde(persona, fallback_5w)
    except Exception:
        logging.exception("Error comprobando estado ciclo slack")
        completadas["proyecto"] = evaluacion_proyecto_guardada_desde(persona, fallback_5w)
        completadas["personal"] = evaluacion_personal_guardada_desde(persona, fallback_5w)
    return {"cicloActivo": True, "completadas": completadas}


@router.get("/api/buscar-empleado-slack")
def buscar_empleado_slack(nombre: str = "", area: str = "negocio", session=Depends(require_session)):
    area = area.lower()
    persona = session.get("persona", "")
    if area == "middleoffice" and not nombre:
        mo_evaluables = obtener_evaluados_middleoffice(persona)
        return {"moEvaluables": mo_evaluables, "preguntas": obtener_preguntas_mo(idioma_por_sesion(session))}
    if not nombre:
        return JSONResponse({"error": "Falta el nombre."}, status_code=400)
    empleado, cargo_evaluado = buscar_empleado_y_cargo(nombre)
    if area == "middleoffice" and not empleado:
        mo_evaluables = obtener_evaluados_middleoffice(persona)
        for mo_e in mo_evaluables:
            if normalizar_nombre(mo_e) == normalizar_nombre(nombre):
                empleado = mo_e
                break
    if not empleado:
        return {"empleado": None, "sugerencias": sugerir_empleados_parecidos(nombre)}
    evaluador_perfil = obtener_perfil_empleado(persona)
    cargo_evaluador = evaluador_perfil.get("cargo", "")
    relacion = comparar_jerarquia(cargo_evaluador, cargo_evaluado or "")
    tipo = tipo_relacion(relacion)
    idi = idioma_por_sesion(session)
    if area == "middleoffice":
        preguntas = obtener_preguntas_mo(idi)
    elif area == "palantir":
        preguntas = obtener_preguntas_palantir(tipo, idi)
    else:
        pn = obtener_preguntas_desde_notion(tipo, idi)
        nocion_q1 = pn.get("q1", "")

        def _es_default(texto):
            return not texto or texto.startswith("Este mes") or "Puedes considerar claridad" in texto

        if _es_default(nocion_q1):
            sujeto = "del Project Leader" if relacion == "inferior" else f"de {empleado}"
            texto_q1 = f"¿Cómo valorarías del 1 al 5 la contribución {sujeto} al buen avance del proyecto?"
        elif "{nombre}" in nocion_q1:
            nombre_resuelto = empleado if relacion != "inferior" else "el Project Leader"
            texto_q1 = nocion_q1.replace("{nombre}", nombre_resuelto)
        else:
            texto_q1 = nocion_q1
        preguntas = [
            {"clave": "q1", "texto": texto_q1},
            {"clave": "q2", "texto": pn.get("q2") or "Indica un ejemplo concreto que justifique tu valoración"},
        ]
    return {"empleado": empleado, "relacion": relacion, "preguntas": preguntas}


@router.post("/api/urgencia-personal")
def urgencia_personal(datos: dict = Body(default={}), session=Depends(require_session)):
    nombre = session.get("persona", "")
    descripcion = datos.get("descripcion", "").strip()
    if not nombre or not descripcion:
        return JSONResponse({"error": "Faltan datos."}, status_code=400)
    ok = notificar_urgencia_personal_web(nombre, descripcion)
    return {"ok": ok}


@router.post("/api/guardar-evaluacion-personal")
def guardar_evaluacion_personal_route(datos: dict = Body(default={}), session=Depends(require_session)):
    nombre = session.get("persona", "")
    if not nombre:
        return JSONResponse({"error": "Tu sesión no tiene un nombre asociado. Vuelve a iniciar sesión."}, status_code=400)
    comentario = datos.get("comentario", "").strip()
    if not comentario:
        return JSONResponse({"error": "El comentario no puede estar vacío."}, status_code=400)
    ok = guardar_evaluacion_personal(nombre, {"comentario": comentario})
    if not ok:
        return JSONResponse(
            {"error": "No se pudo guardar en Notion. Revisa los permisos del token de Notion."}, status_code=500
        )
    return {"ok": True}


@router.post("/api/guardar-evaluacion-slack")
def guardar_evaluacion_slack(datos: dict = Body(default={}), session=Depends(require_session)):
    persona = session.get("persona", "")
    evaluado_nombre = datos.get("evaluado", "").strip()
    proyecto_nombre = datos.get("proyecto", "").strip()
    area = datos.get("area", "negocio").strip().lower()
    respuestas_usuario = datos.get("respuestas", {})
    if not evaluado_nombre or not persona:
        return JSONResponse({"error": "Faltan campos obligatorios."}, status_code=400)
    respuestas_completas = {"evaluado": evaluado_nombre, "proyecto": proyecto_nombre}
    respuestas_completas.update({k: v for k, v in respuestas_usuario.items() if v})
    _, cargo_evaluado = buscar_empleado_y_cargo(evaluado_nombre)
    evaluador_perfil = obtener_perfil_empleado(persona)
    cargo_evaluador = evaluador_perfil.get("cargo", "")
    relacion = comparar_jerarquia(cargo_evaluador, cargo_evaluado or "")
    page_id = guardar_en_notion(persona, respuestas_completas, relacion=relacion, area=_AREA_DISPLAY.get(area, "Negocio"))
    if page_id:
        return {"ok": True, "page_id": page_id}
    return JSONResponse({"error": "No se pudo guardar en Notion."}, status_code=500)


@router.post("/api/actualizar-evaluacion-slack")
def actualizar_evaluacion_slack(datos: dict = Body(default={}), session=Depends(require_session)):
    persona = session.get("persona", "")
    page_id = datos.get("page_id", "").strip()
    evaluado_nombre = datos.get("evaluado", "").strip()
    proyecto_nombre = datos.get("proyecto", "").strip()
    area = datos.get("area", "negocio").strip().lower()
    respuestas_usuario = datos.get("respuestas", {})
    if not page_id or not persona or not evaluado_nombre:
        return JSONResponse({"error": "Faltan campos obligatorios."}, status_code=400)
    respuestas_completas = {"evaluado": evaluado_nombre, "proyecto": proyecto_nombre}
    respuestas_completas.update({k: v for k, v in respuestas_usuario.items() if v})
    _, cargo_evaluado = buscar_empleado_y_cargo(evaluado_nombre)
    evaluador_perfil = obtener_perfil_empleado(persona)
    cargo_evaluador = evaluador_perfil.get("cargo", "")
    relacion = comparar_jerarquia(cargo_evaluador, cargo_evaluado or "")
    ok = actualizar_en_notion(
        page_id, persona, respuestas_completas, relacion=relacion, area=_AREA_DISPLAY.get(area, "Negocio")
    )
    if ok:
        return {"ok": True}
    return JSONResponse({"error": "No se pudo actualizar en Notion."}, status_code=500)
