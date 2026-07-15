import logging
import os
import time
import urllib.parse

from fastapi import APIRouter, Body, Depends, File, Form, UploadFile
from fastapi.responses import JSONResponse

try:
    import mammoth
except ImportError:
    mammoth = None

from ... import config
from ... import eval_anual_sesion
from ..deps import exigir_acceso_advisee, require_session
from ..files import envolver_informe_final_html, url_archivo
from ...anonimato import cargar_config as cargar_anonimato, evaluadores_visibles_para_advisee
from ...notion_service import (
    advisee_tiene_acceso_individual,
    ca_tiene_acceso_activo,
    guardar_informe_final,
    idioma_de_persona,
    obtener_advisees,
    obtener_ca_de_empleado,
    obtener_informe_final_reciente,
)
from ...reports import generar_archivo_trayectoria, generar_archivos_informe
from ...skill_informes_anual import generar_informe_anual
from ...skill_opiniones_ca import generar_resumen_opiniones_ca
from ...skill_pdfs_fuentes import (
    generar_pdf_completo,
    generar_pdf_evals_extra,
    generar_pdf_evals_mensuales,
    generar_pdf_evals_proyecto,
    generar_pdf_seguimiento_personal,
)
from ...users import validar_acceso_sesion
from ...utils import normalizar_nombre, slug_archivo

router = APIRouter()


@router.get("/api/informe-final")
def informe_final(evaluado: str = "", session=Depends(require_session)):
    ca_nombre = session.get("persona", "")
    advisees_ca = obtener_advisees(
        ca_nombre,
        ca_aliases=[session.get("username", ""), session.get("email", "")],
    )
    es_admin = session.get("is_admin", False)
    es_ca = normalizar_nombre(evaluado) in [normalizar_nombre(a) for a in advisees_ca]
    es_propio = normalizar_nombre(evaluado) == normalizar_nombre(ca_nombre)
    if es_propio and not es_admin and not es_ca:
        ca_del_evaluado = obtener_ca_de_empleado(evaluado)
        acceso = bool(
            ca_del_evaluado
            and (ca_tiene_acceso_activo(ca_del_evaluado) or advisee_tiene_acceso_individual(evaluado, ca_del_evaluado))
        )
        informe = obtener_informe_final_reciente(evaluado) if acceso else None
        if acceso and informe:
            return {
                "disponible": True,
                "accesoActivo": True,
                "docxUrl": url_archivo(informe["docx"], evaluado),
                "htmlUrl": url_archivo(informe["html"], evaluado) if informe.get("html") else None,
            }
        if acceso:
            return {"disponible": False, "accesoActivo": True, "mensaje": "Tu CA aún no ha subido tu informe final."}
        return {"disponible": False, "accesoActivo": False, "mensaje": "Tu CA aún no ha publicado tu informe final."}
    if not es_admin and not es_ca:
        raise PermissionError("No tienes permiso para ver este informe.")
    informe = obtener_informe_final_reciente(evaluado)
    if not informe:
        return {"disponible": False, "mensaje": "No hay informe final disponible."}
    return {
        "disponible": True,
        "docxUrl": url_archivo(informe["docx"], evaluado),
        "htmlUrl": url_archivo(informe["html"], evaluado) if informe.get("html") else None,
    }


@router.post("/api/generar")
def generar(datos: dict = Body(default={}), session=Depends(require_session)):
    evaluado = datos.get("evaluado", "")
    cargo = datos.get("cargo", "").strip()
    advisees_ca = obtener_advisees(
        session.get("persona", ""),
        ca_aliases=[session.get("username", ""), session.get("email", "")],
    )
    if not session.get("is_admin") and normalizar_nombre(evaluado) not in [normalizar_nombre(a) for a in advisees_ca]:
        raise PermissionError("Solo administradores o CAs pueden generar informes.")
    validar_acceso_sesion(session, evaluado, extra_permitidos=advisees_ca)
    respuesta = {}
    try:
        total, slug, desde_cache = generar_archivos_informe(evaluado)
        respuesta["total"] = total
        respuesta["desdeCache"] = desde_cache
    except Exception:
        logging.exception("No se pudo generar el informe HTML para %s", evaluado)
    try:
        slug_anual = generar_informe_anual(evaluado, cargo=cargo)
        respuesta["docxAnualUrl"] = url_archivo(f"informe_anual_{slug_anual}.docx", evaluado)
        respuesta["htmlUrl"] = url_archivo(f"informe_anual_{slug_anual}.html", evaluado)
    except Exception:
        logging.exception("No se pudo generar el informe anual IGENERIS para %s", evaluado)
    if not respuesta:
        raise RuntimeError("No se pudo generar ningún informe para esta persona. No ha recibido evaluaciones.")
    return respuesta


@router.post("/api/generar-opiniones-ca")
def generar_opiniones_ca_route(datos: dict = Body(default={}), session=Depends(require_session)):
    evaluado = (datos.get("evaluado", "") or datos.get("advisee", "")).strip()
    if not evaluado:
        return JSONResponse({"error": "Selecciona un advisee."}, status_code=400)
    advisees_ca = obtener_advisees(
        session.get("persona", ""),
        ca_aliases=[session.get("username", ""), session.get("email", "")],
    )
    if not session.get("is_admin") and normalizar_nombre(evaluado) not in [normalizar_nombre(a) for a in advisees_ca]:
        raise PermissionError("Solo el CA o un administrador pueden generar este documento.")
    validar_acceso_sesion(session, evaluado, extra_permitidos=advisees_ca)
    cfg_anon = cargar_anonimato()
    anonimo = not (session.get("is_admin") or evaluadores_visibles_para_advisee(evaluado, cfg_anon))
    slug = generar_resumen_opiniones_ca(evaluado, anonimo=anonimo, idioma=idioma_de_persona(evaluado))
    return {
        "pdfUrl": url_archivo(f"opiniones_ca_{slug}.pdf", evaluado),
        "htmlUrl": url_archivo(f"opiniones_ca_{slug}.html", evaluado),
    }


def _generar_pdf_generico(datos, session, generador, prefijo, forzar_no_anonimo):
    evaluado = (datos.get("evaluado", "") or datos.get("advisee", "")).strip()
    if not evaluado:
        return JSONResponse({"error": "Selecciona un advisee."}, status_code=400)
    exigir_acceso_advisee(session, evaluado)
    cfg_anon = cargar_anonimato()
    anonimo = not (session.get("is_admin") or evaluadores_visibles_para_advisee(evaluado, cfg_anon))
    anonimo_ruta = False if forzar_no_anonimo else anonimo
    slug = generador(evaluado, anonimo=anonimo_ruta, idioma=idioma_de_persona(evaluado))
    return {"pdfUrl": url_archivo(f"{prefijo}_{slug}.pdf", evaluado)}


@router.post("/api/generar-pdf-evals-proyecto")
def generar_pdf_evals_proyecto_route(datos: dict = Body(default={}), session=Depends(require_session)):
    # forzar_no_anonimo=False: las evals de proyecto se sirven anónimas al CA como el
    # resto. Antes iba en True y el nombre del evaluador salía siempre, saltándose la
    # jerarquía de privacidad. Los admin siguen viendo nombres por la vía normal.
    return _generar_pdf_generico(datos, session, generar_pdf_evals_proyecto, "evals_proyecto", False)


@router.post("/api/generar-pdf-seguimiento")
def generar_pdf_seguimiento_route(datos: dict = Body(default={}), session=Depends(require_session)):
    return _generar_pdf_generico(datos, session, generar_pdf_seguimiento_personal, "seguimiento_personal", False)


@router.post("/api/generar-pdf-evals-mensuales")
def generar_pdf_evals_mensuales_route(datos: dict = Body(default={}), session=Depends(require_session)):
    return _generar_pdf_generico(datos, session, generar_pdf_evals_mensuales, "evals_mensuales", False)


@router.post("/api/generar-pdf-completo")
def generar_pdf_completo_route(datos: dict = Body(default={}), session=Depends(require_session)):
    return _generar_pdf_generico(datos, session, generar_pdf_completo, "info_completa", False)


@router.post("/api/generar-pdf-evals-extra")
def generar_pdf_evals_extra_route(datos: dict = Body(default={}), session=Depends(require_session)):
    return _generar_pdf_generico(datos, session, generar_pdf_evals_extra, "evals_extra", True)


@router.post("/api/trayectoria")
def trayectoria(datos: dict = Body(default={}), session=Depends(require_session)):
    evaluado = datos.get("evaluado", "")
    advisees_ca = obtener_advisees(
        session.get("persona", ""),
        ca_aliases=[session.get("username", ""), session.get("email", "")],
    )
    es_propio = normalizar_nombre(evaluado) == normalizar_nombre(session.get("persona", ""))
    if not session.get("is_admin") and normalizar_nombre(evaluado) not in [normalizar_nombre(a) for a in advisees_ca]:
        if not es_propio:
            raise PermissionError("Solo administradores o CAs pueden generar informes.")
        ca_tray = obtener_ca_de_empleado(evaluado)
        if not (ca_tray and (ca_tiene_acceso_activo(ca_tray) or advisee_tiene_acceso_individual(evaluado, ca_tray))):
            raise PermissionError("Tu CA aún no ha publicado tu informe final.")
    validar_acceso_sesion(session, evaluado, extra_permitidos=advisees_ca)
    total, slug = generar_archivo_trayectoria(evaluado)
    return {"total": total, "htmlUrl": url_archivo(f"trayectoria_{slug}.html", evaluado)}


def _exigir_ca_del_advisee(session, evaluado: str) -> None:
    advisees_ca = obtener_advisees(
        session.get("persona", ""),
        ca_aliases=[session.get("username", ""), session.get("email", "")],
    )
    if not session.get("is_admin") and normalizar_nombre(evaluado) not in [normalizar_nombre(a) for a in advisees_ca]:
        raise PermissionError("Solo puedes subir informes para tus advisees.")


def _publicar_informe_final(evaluado: str, docx_filename: str, session) -> dict:
    """Convierte el .docx a HTML, lo registra en Notion como informe final y devuelve las URLs."""
    docx_path = os.path.join(config.CARPETA_WEB, docx_filename)
    html_filename = ""
    if mammoth:
        try:
            html_filename = docx_filename[: -len(".docx")] + ".html"
            html_path = os.path.join(config.CARPETA_WEB, html_filename)
            with open(docx_path, "rb") as df:
                resultado_html = mammoth.convert_to_html(df)
            documento = envolver_informe_final_html(
                resultado_html.value, f"Informe final · {evaluado}" if evaluado else "Informe final"
            )
            with open(html_path, "w", encoding="utf-8") as hf:
                hf.write(documento)
        except Exception:
            logging.exception("Error convirtiendo docx a HTML")
            html_filename = ""
    url_notion = (
        f"{config.APP_PUBLIC_URL}/api/files/{urllib.parse.quote(docx_filename)}"
        f"?evaluado={urllib.parse.quote(evaluado)}"
    )
    ca_subida = session.get("persona", "") if not session.get("is_admin") else ""
    guardar_informe_final(
        ca_nombre=ca_subida,
        advisee=evaluado,
        docx_filename=docx_filename,
        html_filename=html_filename,
        url=url_notion,
    )
    resp_data = {"ok": True, "docxUrl": url_archivo(docx_filename, evaluado)}
    if html_filename:
        resp_data["htmlUrl"] = url_archivo(html_filename, evaluado)
    return resp_data


@router.post("/api/subir-informe-final")
def subir_informe_final(
    evaluado: str = Form(...),
    archivo: UploadFile = File(...),
    session=Depends(require_session),
):
    _exigir_ca_del_advisee(session, evaluado)
    slug_ev = slug_archivo(evaluado)
    ts = int(time.time())
    docx_filename = f"informe_final_{slug_ev}_{ts}.docx"
    docx_path = os.path.join(config.CARPETA_WEB, docx_filename)
    with open(docx_path, "wb") as f:
        f.write(archivo.file.read())
    return _publicar_informe_final(evaluado, docx_filename, session)


@router.post("/api/eval-anual/subir-borrador")
def subir_borrador_informe_final(datos: dict = Body(default={}), session=Depends(require_session)):
    """Genera el .docx oficial desde el borrador web editado y lo publica como informe
    final (mismo flujo que /api/subir-informe-final, sin exportar ni subir archivos)."""
    evaluado = (datos.get("evaluado") or "").strip()
    if not evaluado:
        return JSONResponse({"error": "Falta el campo evaluado."}, status_code=400)
    _exigir_ca_del_advisee(session, evaluado)
    if isinstance(datos.get("borrador"), dict):
        eval_anual_sesion.guardar_borrador(evaluado, datos["borrador"])
    slug_ev = slug_archivo(evaluado)
    ts = int(time.time())
    docx_filename = f"informe_final_{slug_ev}_{ts}.docx"
    eval_anual_sesion.generar_docx_borrador(evaluado, docx_filename)
    return _publicar_informe_final(evaluado, docx_filename, session)
