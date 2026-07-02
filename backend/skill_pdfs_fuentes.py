"""
Skill: PDFs de fuentes en bruto por advisee (para realizar el informe final manualmente).

Genera un PDF por fuente, con el estilo de marca IGENERIS (reutiliza fuentes/logo del PDF de
opiniones) y el DATO EN BRUTO ordenado cronológicamente. Tres documentos:
  - Evaluaciones de proyecto   → evals_proyecto_{slug}.pdf
  - Seguimiento personal       → seguimiento_personal_{slug}.pdf
  - Evaluaciones mensuales     → evals_mensuales_{slug}.pdf

Cada uno se guarda en config.CARPETA_WEB y se sirve por /api/files/<archivo>.
El PDF de opiniones ya existe en skill_opiniones_ca.
"""

import html as html_lib
import logging
import os
from datetime import datetime, timezone

from . import config
from .utils import slug_archivo
from .notion_service import (
    obtener_ca_de_empleado,
    obtener_comentarios_personales,
    obtener_evaluaciones_por_evaluado,
    obtener_opiniones_ca_por_advisee,
)
from .project_evals import obtener_evaluaciones_proyecto_por_evaluado
# Reutiliza la maquetación de marca del PDF de opiniones
from .skill_opiniones_ca import _registrar_fuentes, _LOGO_PATH, _REPORTLAB_OK, _MESES

if _REPORTLAB_OK:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, HRFlowable, KeepTogether, Image,
    )


_ORANGE = colors.HexColor('#F23C14') if _REPORTLAB_OK else None
_BLACK = colors.HexColor('#000000') if _REPORTLAB_OK else None


# Etiquetas fijas de la plantilla (el contenido —evaluaciones— va en su idioma original).
_L = {
    "es": {"t_proyecto": "Evaluaciones de proyecto", "t_seguimiento": "Seguimiento personal",
           "t_mensuales": "Evaluaciones mensuales", "t_completo": "Información completa recibida",
           "s_opiniones": "Opiniones del CA", "valoracion": "Valoración", "ejemplo": "Ejemplo",
           "nota_ca": "Nota del CA", "resumen": "Resumen", "opinion_ca": "Opinión CA",
           "sin_datos_fuente": "Sin datos para esta fuente.", "sin_datos": "Sin datos.",
           "generado_el": "Generado el", "sin_proyecto": "Sin proyecto", "sin_fecha": "Sin fecha",
           "lider": "líder", "igual": "igual", "subordinado": "subordinado", "sin_nivel": "sin nivel"},
    "en": {"t_proyecto": "Project evaluations", "t_seguimiento": "Personal tracking",
           "t_mensuales": "Monthly evaluations", "t_completo": "Full information received",
           "s_opiniones": "CA opinions", "valoracion": "Rating", "ejemplo": "Example",
           "nota_ca": "CA note", "resumen": "Summary", "opinion_ca": "CA opinion",
           "sin_datos_fuente": "No data for this source.", "sin_datos": "No data.",
           "generado_el": "Generated on", "sin_proyecto": "No project", "sin_fecha": "No date",
           "lider": "lead", "igual": "same level", "subordinado": "subordinate", "sin_nivel": "no level"},
    "pt": {"t_proyecto": "Avaliações de projeto", "t_seguimiento": "Acompanhamento pessoal",
           "t_mensuales": "Avaliações mensais", "t_completo": "Informação completa recebida",
           "s_opiniones": "Opiniões do CA", "valoracion": "Avaliação", "ejemplo": "Exemplo",
           "nota_ca": "Nota do CA", "resumen": "Resumo", "opinion_ca": "Opinião CA",
           "sin_datos_fuente": "Sem dados para esta fonte.", "sin_datos": "Sem dados.",
           "generado_el": "Gerado a", "sin_proyecto": "Sem projeto", "sin_fecha": "Sem data",
           "lider": "líder", "igual": "mesmo nível", "subordinado": "subordinado", "sin_nivel": "sem nível"},
}
_MESES_ABBR = {
    "es": _MESES,
    "en": ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
    "pt": ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"],
}


def _t(idioma: str, clave: str) -> str:
    return _L.get(idioma, _L["es"]).get(clave, _L["es"].get(clave, clave))


def _rel_label(rel: str, idioma: str) -> str:
    return {"superior": _t(idioma, "lider"), "igual": _t(idioma, "igual"),
            "inferior": _t(idioma, "subordinado"), "": _t(idioma, "sin_nivel")}.get(rel, rel)


def _fecha_es(fecha: str, idioma: str = "es") -> str:
    """'2025-03-15' -> '15 mar 2025' (abreviatura de mes según idioma)."""
    try:
        meses = _MESES_ABBR.get(idioma, _MESES)
        return f"{int(fecha[8:10])} {meses[int(fecha[5:7]) - 1]} {fecha[:4]}"
    except Exception:
        return fecha or _t(idioma, "sin_fecha")


def _esc(t) -> str:
    return html_lib.escape(str(t or "")).replace("\n", "<br/>")


def _construir_pdf(titulo: str, advisee: str, ca: str, entradas: list[dict], nombre_archivo: str, idioma: str = "es") -> str:
    """Construye un PDF de marca con una lista de entradas {header, meta, cuerpo}.

    Devuelve la ruta del PDF. Lanza RuntimeError si falta reportlab.
    """
    if not _REPORTLAB_OK:
        raise RuntimeError("Instala reportlab: pip install reportlab")

    MUTED = colors.Color(0, 0, 0, alpha=0.55)
    fonts = _registrar_fuentes()
    F_LIGHT, F_REG, F_MED = fonts["light"], fonts["regular"], fonts["medium"]

    s_adv     = ParagraphStyle('adv',    fontSize=26, fontName=F_MED,   textColor=_BLACK, leading=30, spaceAfter=4)
    s_titulo  = ParagraphStyle('tit',    fontSize=12, fontName=F_REG,   textColor=MUTED,  leading=16, spaceAfter=2)
    s_meta    = ParagraphStyle('meta',   fontSize=8.5, fontName=F_REG,  textColor=_ORANGE, leading=13, spaceAfter=10)
    s_header  = ParagraphStyle('hdr',    fontSize=11, fontName=F_MED,   textColor=_BLACK, leading=15, spaceAfter=1)
    s_submeta = ParagraphStyle('sub',    fontSize=8,  fontName=F_REG,   textColor=MUTED,  leading=12, spaceAfter=4)
    s_cuerpo  = ParagraphStyle('cpo',    fontSize=9.5, fontName=F_LIGHT, textColor=_BLACK, leading=15, spaceAfter=2)

    os.makedirs(config.CARPETA_WEB, exist_ok=True)
    ruta = os.path.join(config.CARPETA_WEB, nombre_archivo)
    doc = SimpleDocTemplate(
        ruta, pagesize=A4,
        rightMargin=2 * cm, leftMargin=2 * cm, topMargin=2 * cm, bottomMargin=2 * cm,
        title=f"{titulo} — {advisee}",
    )
    story = []
    fecha_gen = datetime.now(timezone.utc).strftime("%d/%m/%Y")

    story.append(Spacer(1, 1.2 * cm))
    cabecera = [[Paragraph(_esc(advisee), s_adv)]]
    if os.path.exists(_LOGO_PATH):
        try:
            from reportlab.lib.utils import ImageReader
            iw, ih = ImageReader(_LOGO_PATH).getSize()
            logo_w = 3.2 * cm
            logo_img = Image(_LOGO_PATH, width=logo_w, height=logo_w * ih / iw)
            cabecera = [[Paragraph(_esc(advisee), s_adv), logo_img]]
            t = Table(cabecera, colWidths=[None, logo_w])
            story.append(t)
        except Exception:
            story.append(Paragraph(_esc(advisee), s_adv))
    else:
        story.append(Paragraph(_esc(advisee), s_adv))

    story.append(Paragraph(_esc(titulo), s_titulo))
    story.append(Paragraph(f"CA: {_esc(ca) or '—'} · {_t(idioma, 'generado_el')} {fecha_gen}", s_meta))
    story.append(HRFlowable(width="100%", thickness=1, color=_BLACK, spaceBefore=2, spaceAfter=12))

    if not entradas:
        story.append(Paragraph(_t(idioma, "sin_datos_fuente"), s_cuerpo))
    for e in entradas:
        bloque = [Paragraph(_esc(e.get("header", "")), s_header)]
        if e.get("meta"):
            bloque.append(Paragraph(_esc(e["meta"]), s_submeta))
        if e.get("cuerpo"):
            bloque.append(Paragraph(_esc(e["cuerpo"]), s_cuerpo))
        bloque.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor('#DBDBDE'),
                                 spaceBefore=8, spaceAfter=8))
        story.append(KeepTogether(bloque))

    doc.build(story)
    logging.info("PDF '%s' generado: %s", titulo, ruta)
    return ruta


def _ca_de(advisee: str) -> str:
    try:
        return obtener_ca_de_empleado(advisee) or ""
    except Exception:
        return ""


def generar_pdf_evals_proyecto(advisee: str, anonimo: bool = True, idioma: str = "es") -> str:
    datos = obtener_evaluaciones_proyecto_por_evaluado(advisee)
    datos = sorted(datos, key=lambda x: x.get("fecha", ""))
    entradas = [{
        "header": d.get("proyecto") or _t(idioma, "sin_proyecto"),
        "meta": " · ".join(p for p in [
            None if anonimo else d.get("evaluador"),
            d.get("tipo"),
            _fecha_es(d.get("fecha", ""), idioma),
        ] if p),
        "cuerpo": d.get("respuestas", ""),
    } for d in datos]
    slug = slug_archivo(advisee)
    _construir_pdf(_t(idioma, "t_proyecto"), advisee, _ca_de(advisee), entradas, f"evals_proyecto_{slug}.pdf", idioma)
    return slug


def generar_pdf_seguimiento_personal(advisee: str, anonimo: bool = True, idioma: str = "es") -> str:
    datos = obtener_comentarios_personales(advisee)
    datos = sorted(datos, key=lambda x: x.get("fecha", ""))
    entradas = [{
        "header": _fecha_es(d.get("fecha", ""), idioma),
        "meta": "" if anonimo else d.get("autor", ""),
        "cuerpo": d.get("comentario", ""),
    } for d in datos]
    slug = slug_archivo(advisee)
    _construir_pdf(_t(idioma, "t_seguimiento"), advisee, _ca_de(advisee), entradas, f"seguimiento_personal_{slug}.pdf", idioma)
    return slug


def generar_pdf_evals_mensuales(advisee: str, anonimo: bool = True, idioma: str = "es") -> str:
    datos = obtener_evaluaciones_por_evaluado(advisee)
    datos = sorted(datos, key=lambda x: x.get("fecha", ""))
    entradas = []
    for d in datos:
        cuerpo = []
        if d.get("q1"):
            cuerpo.append(f"{_t(idioma, 'valoracion')}: {d['q1']}")
        if d.get("q2"):
            cuerpo.append(f"{_t(idioma, 'ejemplo')}: {d['q2']}")
        entradas.append({
            "header": d.get("proyecto") or _t(idioma, "sin_proyecto"),
            "meta": " · ".join(p for p in [
                None if anonimo else (d.get("persona_que_evalua") or d.get("nombre")),
                _rel_label(d.get("relacion", ""), idioma),
                _fecha_es(d.get("fecha", ""), idioma),
            ] if p),
            "cuerpo": "\n".join(cuerpo),
        })
    slug = slug_archivo(advisee)
    _construir_pdf(_t(idioma, "t_mensuales"), advisee, _ca_de(advisee), entradas, f"evals_mensuales_{slug}.pdf", idioma)
    return slug


# ── PDF combinado: toda la información recibida ────────────────────────────────

def _entradas_evals_proyecto(advisee, anonimo, idioma="es"):
    datos = sorted(obtener_evaluaciones_proyecto_por_evaluado(advisee), key=lambda x: x.get("fecha", ""))
    return [{
        "header": d.get("proyecto") or _t(idioma, "sin_proyecto"),
        "meta": " · ".join(p for p in [
            None if anonimo else d.get("evaluador"),
            d.get("tipo"),
            _fecha_es(d.get("fecha", ""), idioma),
        ] if p),
        "cuerpo": d.get("respuestas", ""),
    } for d in datos]


def _entradas_seguimiento(advisee, anonimo, idioma="es"):
    datos = sorted(obtener_comentarios_personales(advisee), key=lambda x: x.get("fecha", ""))
    return [{"header": _fecha_es(d.get("fecha", ""), idioma), "meta": "" if anonimo else d.get("autor", ""),
             "cuerpo": d.get("comentario", "")} for d in datos]


def _entradas_evals_mensuales(advisee, anonimo, idioma="es"):
    datos = sorted(obtener_evaluaciones_por_evaluado(advisee), key=lambda x: x.get("fecha", ""))
    out = []
    for d in datos:
        cuerpo = []
        if d.get("q1"):
            cuerpo.append(f"{_t(idioma, 'valoracion')}: {d['q1']}")
        if d.get("q2"):
            cuerpo.append(f"{_t(idioma, 'ejemplo')}: {d['q2']}")
        out.append({
            "header": d.get("proyecto") or _t(idioma, "sin_proyecto"),
            "meta": " · ".join(p for p in [
                None if anonimo else (d.get("persona_que_evalua") or d.get("nombre")),
                _rel_label(d.get("relacion", ""), idioma),
                _fecha_es(d.get("fecha", ""), idioma),
            ] if p),
            "cuerpo": "\n".join(cuerpo),
        })
    return out


def _entradas_opiniones(advisee, ca, anonimo=True, idioma="es"):
    try:
        datos = obtener_opiniones_ca_por_advisee(ca, advisee)
    except Exception:
        datos = []
    datos = sorted(datos, key=lambda x: x.get("fecha", ""))
    out = []
    for d in datos:
        cuerpo = []
        if d.get("opinion"):
            cuerpo.append(f"{_t(idioma, 'nota_ca')}: {d['opinion']}")
        if not anonimo and d.get("resumen_advisee"):
            cuerpo.append(f"{_t(idioma, 'resumen')}: {d['resumen_advisee']}")
        out.append({"header": _fecha_es(d.get("fecha", ""), idioma), "meta": _t(idioma, "opinion_ca"), "cuerpo": "\n".join(cuerpo)})
    return out


def _construir_pdf_secciones(titulo, advisee, ca, secciones, nombre_archivo, idioma="es"):
    """Como _construir_pdf pero con varias secciones (cada una con su encabezado)."""
    if not _REPORTLAB_OK:
        raise RuntimeError("Instala reportlab: pip install reportlab")
    MUTED = colors.Color(0, 0, 0, alpha=0.55)
    fonts = _registrar_fuentes()
    F_LIGHT, F_REG, F_MED = fonts["light"], fonts["regular"], fonts["medium"]
    s_adv     = ParagraphStyle('adv2',  fontSize=26, fontName=F_MED,   textColor=_BLACK, leading=30, spaceAfter=4)
    s_titulo  = ParagraphStyle('tit2',  fontSize=12, fontName=F_REG,   textColor=MUTED,  leading=16, spaceAfter=2)
    s_meta    = ParagraphStyle('m2',    fontSize=8.5, fontName=F_REG,  textColor=_ORANGE, leading=13, spaceAfter=10)
    s_sec     = ParagraphStyle('sec2',  fontSize=14, fontName=F_MED,   textColor=_BLACK, leading=18, spaceBefore=16, spaceAfter=6)
    s_header  = ParagraphStyle('h2',    fontSize=11, fontName=F_MED,   textColor=_BLACK, leading=15, spaceAfter=1)
    s_submeta = ParagraphStyle('sm2',   fontSize=8,  fontName=F_REG,   textColor=MUTED,  leading=12, spaceAfter=4)
    s_cuerpo  = ParagraphStyle('c2',    fontSize=9.5, fontName=F_LIGHT, textColor=_BLACK, leading=15, spaceAfter=2)

    os.makedirs(config.CARPETA_WEB, exist_ok=True)
    ruta = os.path.join(config.CARPETA_WEB, nombre_archivo)
    doc = SimpleDocTemplate(
        ruta, pagesize=A4, rightMargin=2 * cm, leftMargin=2 * cm, topMargin=2 * cm, bottomMargin=2 * cm,
        title=f"{titulo} — {advisee}",
    )
    fecha_gen = datetime.now(timezone.utc).strftime("%d/%m/%Y")
    story = [
        Spacer(1, 1.2 * cm),
        Paragraph(_esc(advisee), s_adv),
        Paragraph(_esc(titulo), s_titulo),
        Paragraph(f"CA: {_esc(ca) or '—'} · {_t(idioma, 'generado_el')} {fecha_gen}", s_meta),
        HRFlowable(width="100%", thickness=1, color=_BLACK, spaceBefore=2, spaceAfter=6),
    ]
    for sec_tit, entradas in secciones:
        story.append(Paragraph(f"{_esc(sec_tit)}  ({len(entradas)})", s_sec))
        if not entradas:
            story.append(Paragraph(_t(idioma, "sin_datos"), s_cuerpo))
        for e in entradas:
            bloque = [Paragraph(_esc(e.get("header", "")), s_header)]
            if e.get("meta"):
                bloque.append(Paragraph(_esc(e["meta"]), s_submeta))
            if e.get("cuerpo"):
                bloque.append(Paragraph(_esc(e["cuerpo"]), s_cuerpo))
            bloque.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor('#DBDBDE'),
                                     spaceBefore=6, spaceAfter=6))
            story.append(KeepTogether(bloque))
    doc.build(story)
    logging.info("PDF completo generado: %s", ruta)
    return ruta


def generar_pdf_completo(advisee: str, anonimo: bool = True, idioma: str = "es") -> str:
    """Un solo PDF con TODA la información recibida por la persona (las 4 fuentes)."""
    ca = _ca_de(advisee)
    secciones = [
        (_t(idioma, "s_opiniones"), _entradas_opiniones(advisee, ca, anonimo=anonimo, idioma=idioma)),
        (_t(idioma, "t_mensuales"), _entradas_evals_mensuales(advisee, anonimo, idioma)),
        (_t(idioma, "t_proyecto"), _entradas_evals_proyecto(advisee, anonimo=False, idioma=idioma)),
        (_t(idioma, "t_seguimiento"), _entradas_seguimiento(advisee, anonimo, idioma)),
    ]
    slug = slug_archivo(advisee)
    _construir_pdf_secciones(_t(idioma, "t_completo"), advisee, ca, secciones, f"info_completa_{slug}.pdf", idioma)
    return slug