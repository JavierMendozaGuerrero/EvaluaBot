import os
import urllib.parse

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response

from .. import config
from ..notion_service import obtener_advisees, obtener_ca_de_empleado, ca_tiene_acceso_activo
from ..utils import normalizar_nombre, slug_archivo
from .deps import get_session

router = APIRouter()


def url_archivo(nombre_archivo: str, evaluado: str) -> str:
    query = urllib.parse.urlencode({"evaluado": evaluado})
    return f"/api/files/{urllib.parse.quote(nombre_archivo)}?{query}"


@router.get("/api/files/{nombre_archivo:path}")
def servir_archivo_protegido(
    nombre_archivo: str,
    request: Request,
    evaluado: str = "",
    session=Depends(get_session),
):
    if not session:
        raise PermissionError("Inicia sesión para acceder.")
    ca_nombre = session.get("persona", "")
    advisees_ca = obtener_advisees(
        ca_nombre,
        ca_aliases=[session.get("username", ""), session.get("email", "")],
    )
    es_admin = session.get("is_admin", False)
    es_ca = normalizar_nombre(evaluado) in [normalizar_nombre(a) for a in advisees_ca]
    es_propio = normalizar_nombre(evaluado) == normalizar_nombre(ca_nombre)
    slug = slug_archivo(evaluado)
    es_borrador = (
        nombre_archivo.startswith(f"informe_{slug}.")
        or nombre_archivo.startswith(f"informe_anual_{slug}.")
    )
    es_trayectoria = nombre_archivo.startswith(f"trayectoria_{slug}.")
    es_final = nombre_archivo.startswith(f"informe_final_{slug}_")
    es_opiniones = nombre_archivo.startswith(f"opiniones_ca_{slug}.")
    es_fuente_pdf = any(
        nombre_archivo.startswith(f"{p}_{slug}.")
        for p in ("evals_proyecto", "seguimiento_personal", "evals_mensuales", "info_completa")
    )
    if not es_borrador and not es_trayectoria and not es_final and not es_opiniones and not es_fuente_pdf:
        raise PermissionError("El archivo solicitado no corresponde con la persona autorizada.")
    if (es_borrador or es_opiniones or es_fuente_pdf) and not es_admin and not es_ca:
        raise PermissionError("Solo el CA o un administrador pueden ver los documentos generados.")
    if (es_trayectoria or es_final) and not es_admin and not es_ca:
        if es_propio:
            ca_del_evaluado = obtener_ca_de_empleado(evaluado)
            if not (ca_del_evaluado and ca_tiene_acceso_activo(ca_del_evaluado)):
                raise PermissionError("Tu CA aún no ha publicado tu informe.")
        else:
            raise PermissionError("No tienes permiso para ver este archivo.")

    ruta = os.path.join(config.CARPETA_WEB, os.path.basename(nombre_archivo))
    if not os.path.exists(ruta):
        return JSONResponse({"error": "Archivo no encontrado"}, status_code=404)

    if nombre_archivo.endswith(".docx"):
        content_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    elif nombre_archivo.endswith(".pdf"):
        content_type = "application/pdf"
    else:
        content_type = "text/html; charset=utf-8"

    cache_control = "no-cache" if (es_opiniones or es_fuente_pdf or es_borrador) else "private, max-age=300"
    stat = os.stat(ruta)
    etag = '"%x-%x"' % (int(stat.st_mtime), stat.st_size)

    if request.headers.get("If-None-Match") == etag:
        return Response(status_code=304, headers={"ETag": etag, "Cache-Control": cache_control})

    with open(ruta, "rb") as f:
        body = f.read()
    return Response(
        content=body,
        media_type=content_type,
        headers={"Cache-Control": cache_control, "ETag": etag},
    )
