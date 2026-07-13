import html
import os
import urllib.parse

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response

from .. import config
from ..notion_service import (
    advisee_tiene_acceso_individual,
    ca_tiene_acceso_activo,
    obtener_advisees,
    obtener_ca_de_empleado,
)
from ..utils import normalizar_nombre, slug_archivo
from .deps import get_session

router = APIRouter()


def url_archivo(nombre_archivo: str, evaluado: str) -> str:
    query = urllib.parse.urlencode({"evaluado": evaluado})
    return f"/api/files/{urllib.parse.quote(nombre_archivo)}?{query}"


def envolver_informe_final_html(fragmento: str, titulo: str = "Informe final") -> str:
    """Envuelve el fragmento HTML del informe final en un documento completo y con estilo.

    `mammoth.convert_to_html` devuelve solo el cuerpo (<p>, <table>…), SIN <head> ni
    declaración de codificación. El frontend abre este HTML desde un blob: local (ver
    `openAuthedFile` en main.jsx), y ahí se pierde la cabecera `Content-Type; charset=utf-8`:
    el navegador adivina Windows-1252 y el UTF-8 se ve como «EVALUACIÃ"N». Envolverlo con
    <meta charset="utf-8"> corrige la codificación y, de paso, le da el aspecto de IGENERIS
    en lugar del HTML pelado por defecto.
    """
    titulo_esc = html.escape(titulo)
    estilos_doc = (
        ".shell { max-width: 900px; margin: 0 auto; padding-bottom: 60px; }\n"
        ".doc { padding-top: clamp(28px, 6vw, 56px); }\n"
        ".doc p { color: var(--ink); line-height: 1.55; margin: 10px 0; }\n"
        ".doc strong { color: var(--ink); }\n"
        ".doc table { width: 100%; border-collapse: collapse; margin: 20px 0; font-size: 14px; }\n"
        ".doc td, .doc th { border: 1px solid var(--ink); padding: 10px 14px; vertical-align: top; text-align: left; }\n"
        ".doc td:first-child { font-weight: 500; }\n"
        ".doc a { color: #0563C1; }\n"
        ".doc ul, .doc ol { margin: 10px 0; padding-left: 22px; line-height: 1.55; }\n"
        ".doc li { margin-bottom: 4px; }\n"
    )
    return (
        "<!DOCTYPE html>\n<html lang=\"es\">\n<head>\n"
        "<meta charset=\"utf-8\">\n"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
        f"<title>{titulo_esc}</title>\n"
        f"<style>\n{config.IGENERIS_CSS}{estilos_doc}</style>\n"
        "</head>\n<body>\n<main class=\"page shell\">\n"
        "<nav class=\"nav\">\n"
        "  <span class=\"brand\">igeneris</span>\n"
        f"  <span class=\"fine\">{titulo_esc}</span>\n"
        "</nav>\n"
        f"<div class=\"doc\">\n{fragmento}\n</div>\n"
        "</main>\n</body>\n</html>"
    )


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
            # Misma regla que /api/informe-final y /api/trayectoria: vale el acceso
            # global del CA O el acceso individual concedido a este advisee.
            ca_del_evaluado = obtener_ca_de_empleado(evaluado)
            acceso = bool(ca_del_evaluado and (
                ca_tiene_acceso_activo(ca_del_evaluado)
                or advisee_tiene_acceso_individual(evaluado, ca_del_evaluado)
            ))
            if not acceso:
                raise PermissionError("Tu CA aún no ha publicado tu informe.")
        else:
            raise PermissionError("No tienes permiso para ver este archivo.")

    # Defensa en profundidad: solo se sirven documentos generados (html/pdf/docx).
    # Aunque los prefijos ya acotan qué se sirve, esto impide de raíz servir
    # ficheros sensibles que viven en la misma carpeta (users.json, *.csv, cachés).
    base = os.path.basename(nombre_archivo)
    if not base.endswith((".html", ".pdf", ".docx")):
        raise PermissionError("Tipo de archivo no permitido.")

    ruta = os.path.join(config.CARPETA_WEB, base)
    if not os.path.exists(ruta):
        return JSONResponse({"error": "Archivo no encontrado"}, status_code=404)

    if base.endswith(".docx"):
        content_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    elif base.endswith(".pdf"):
        content_type = "application/pdf"
    else:
        content_type = "text/html; charset=utf-8"

    cache_control = "no-cache" if (es_opiniones or es_fuente_pdf or es_borrador) else "private, max-age=300"
    stat = os.stat(ruta)
    etag = '"%x-%x"' % (int(stat.st_mtime), stat.st_size)

    headers = {"Cache-Control": cache_control, "ETag": etag}
    # Los informes finales son HTML convertido de un .docx subido por el CA. Para
    # neutralizar cualquier script incrustado, se sirven con una CSP que prohíbe
    # ejecutar JS. (Los HTML generados por nosotros —trayectoria— sí usan script,
    # por eso la CSP se aplica solo al informe final subido.)
    if es_final and base.endswith(".html"):
        headers["Content-Security-Policy"] = "default-src 'none'; img-src data:; style-src 'unsafe-inline'"

    if request.headers.get("If-None-Match") == etag:
        return Response(status_code=304, headers=headers)

    with open(ruta, "rb") as f:
        body = f.read()

    # Red de seguridad para informes finales antiguos: los que se subieron antes de
    # envolver el HTML se guardaron como fragmento pelado de mammoth (sin <meta charset>).
    # Al abrirse desde un blob: se veían con la codificación rota. Si el fichero aún no
    # trae cabecera <meta charset>, lo envolvemos al vuelo. Los nuevos ya vienen envueltos,
    # así que esta rama no se dispara para ellos (evita doble envoltura).
    if es_final and base.endswith(".html"):
        texto = body.decode("utf-8", "replace")
        if "<meta charset" not in texto[:1000].lower():
            texto = envolver_informe_final_html(texto, f"Informe final · {evaluado}" if evaluado else "Informe final")
            body = texto.encode("utf-8")

    return Response(content=body, media_type=content_type, headers=headers)
