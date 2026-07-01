"""
Skill: Resumen de opiniones del CA por advisee
Genera, para un advisee concreto, un documento (PDF + HTML) con las opiniones que su
Career Advisor ha ido dejando a lo largo del tiempo. No requiere ninguna base de Notion
adicional: usa la base "Opiniones - {advisee}" ya existente (Seguimiento CA).

Cada fila de "Opiniones - {advisee}" se reparte en dos bloques:
  - Filas CON Resumen   → entradas cronológicas (opinión del CA | resumen sobre el que opinó)
  - Filas SIN Resumen   → sección final "Comentarios y notas extra" (notas sueltas desde la web)

El PDF usa reportlab (paleta Igeneris). El HTML usa el IGENERIS_CSS del proyecto.
Ambos se guardan en config.CARPETA_WEB y se sirven por /api/files/<archivo>.
"""

import hashlib
import html as html_lib
import json
import logging
import os
from datetime import datetime

from . import config
from .notion_service import (
    obtener_ca_de_empleado,
    obtener_opiniones_ca_por_advisee,
)
from .utils import slug_archivo

try:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
        PageBreak, HRFlowable, KeepTogether, Image,
    )
    from reportlab.pdfgen import canvas as pdfcanvas
    _REPORTLAB_OK = True
except ImportError:
    _REPORTLAB_OK = False


# ── Constantes ────────────────────────────────────────────────────────────────

# Logo opcional: si existe este PNG se incrusta en la cabecera del PDF; si no, se omite.
_LOGO_PATH = os.path.join(config.BASE_DIR, "assets", "igeneris_logo.png")

# Fuentes Igeneris (Outfit). Si no están, el PDF cae a Helvetica.
_FONTS_DIR = os.path.join(config.BASE_DIR, "assets", "fonts")
_FONTS_REGISTRADAS: dict | None = None

_MESES = [
    "ene", "feb", "mar", "abr", "may", "jun",
    "jul", "ago", "sep", "oct", "nov", "dic",
]


def _registrar_fuentes() -> dict:
    """Registra las fuentes Outfit en reportlab. Devuelve los nombres a usar (Outfit o Helvetica)."""
    global _FONTS_REGISTRADAS
    if _FONTS_REGISTRADAS is not None:
        return _FONTS_REGISTRADAS

    fonts = {"light": "Helvetica", "regular": "Helvetica", "medium": "Helvetica-Bold"}
    try:
        from reportlab.pdfbase.ttfonts import TTFont
        from reportlab.pdfbase import pdfmetrics
        archivos = {
            "Outfit-ExtraLight": "Outfit-ExtraLight.ttf",
            "Outfit":            "Outfit-Regular.ttf",
            "Outfit-Medium":     "Outfit-Medium.ttf",
        }
        rutas = {n: os.path.join(_FONTS_DIR, f) for n, f in archivos.items()}
        if all(os.path.exists(r) for r in rutas.values()):
            for nombre, ruta in rutas.items():
                pdfmetrics.registerFont(TTFont(nombre, ruta))
            fonts = {"light": "Outfit-ExtraLight", "regular": "Outfit", "medium": "Outfit-Medium"}
    except Exception:
        logging.exception("No se pudieron registrar las fuentes Outfit; se usa Helvetica")

    _FONTS_REGISTRADAS = fonts
    return fonts


# ── Lectura y preparación de datos ────────────────────────────────────────────

def _formatear_fecha(iso: str) -> str:
    """'2024-01-12T...' → '12 ene 2024'. Si no parsea, devuelve los primeros 10 chars."""
    if not iso:
        return "—"
    try:
        d = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return f"{d.day} {_MESES[d.month - 1]} {d.year}"
    except Exception:
        return iso[:10]


def obtener_datos_opiniones_ca(advisee: str, ca_nombre: str = "", anonimo: bool = False) -> dict:
    """
    Recopila las opiniones del CA sobre el advisee y las separa en:
      - entries:            filas con Resumen (opinión del CA + resumen sobre el que opinó)
      - comentarios_sueltos: filas sin Resumen (notas sueltas registradas desde la web)

    Si no se pasa ca_nombre se intenta resolver desde "Objetivos" / "Lista CA".
    Si anonimo=True, el resumen_advisee se oculta (pero la opinión del CA sigue visible).
    """
    if not ca_nombre:
        try:
            ca_nombre = obtener_ca_de_empleado(advisee) or ""
        except Exception:
            ca_nombre = ""

    opiniones = obtener_opiniones_ca_por_advisee(ca_nombre, advisee)
    # Más antiguas primero (obtener_* las devuelve descendente)
    opiniones = sorted(opiniones, key=lambda o: o.get("fecha", ""))

    entries = []
    comentarios_sueltos = []
    for op in opiniones:
        opinion = (op.get("opinion") or "").strip()
        resumen = (op.get("resumen_advisee") or "").strip()
        fecha = op.get("fecha") or ""
        tiene_resumen = bool(resumen)
        if anonimo:
            resumen = ""
        if tiene_resumen:
            entries.append({
                "fecha": _formatear_fecha(fecha),
                "fecha_iso": fecha,
                "opinion_ca": opinion or "—",
                "resumen": resumen,
            })
        elif opinion:
            comentarios_sueltos.append(opinion)

    return {
        "advisee": advisee,
        "ca": ca_nombre,
        "entries": entries,
        "comentarios_sueltos": comentarios_sueltos,
    }


# ── PDF (reportlab) ───────────────────────────────────────────────────────────

def _canvas_maker(advisee_name: str, ca_name: str, font: str = "Helvetica"):
    """Devuelve una subclase de Canvas que pinta cabecera/pie en todas las páginas menos la 1ª."""
    BLACK    = colors.HexColor('#000000')
    MUTED    = colors.Color(0, 0, 0, alpha=0.55)
    BORDER   = colors.HexColor('#DBDBDE')

    class IGCanvas(pdfcanvas.Canvas):
        def __init__(self, *args, **kwargs):
            pdfcanvas.Canvas.__init__(self, *args, **kwargs)
            self._pages = []

        def showPage(self):
            self._pages.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            n = len(self._pages)
            for i, state in enumerate(self._pages):
                self.__dict__.update(state)
                if i > 0:
                    self._draw_page(i + 1, n)
                pdfcanvas.Canvas.showPage(self)
            pdfcanvas.Canvas.save(self)

        def _draw_page(self, page, total):
            pw, ph = A4
            self.setStrokeColor(BORDER)
            self.setLineWidth(0.5)
            self.line(2 * cm, ph - 1.3 * cm, pw - 2 * cm, ph - 1.3 * cm)
            self.setFont(font, 7.5)
            self.setFillColor(BLACK)
            self.drawString(2 * cm, ph - 1.05 * cm, advisee_name)
            self.setFillColor(MUTED)
            self.drawRightString(pw - 2 * cm, ph - 1.05 * cm, f"CA · {ca_name}")
            self.setFillColor(MUTED)
            self.drawCentredString(pw / 2, 1 * cm, f"{page} / {total}")

    return IGCanvas


def generar_pdf_opiniones_ca(datos: dict) -> str:
    """Genera el PDF con reportlab y devuelve la ruta. Lanza RuntimeError si falta reportlab."""
    if not _REPORTLAB_OK:
        raise RuntimeError("Instala reportlab: pip install reportlab")

    ORANGE   = colors.HexColor('#F23C14')
    BLACK    = colors.HexColor('#000000')
    MUTED    = colors.Color(0, 0, 0, alpha=0.55)
    BORDER   = colors.HexColor('#DBDBDE')

    fonts = _registrar_fuentes()
    F_LIGHT, F_REG, F_MED = fonts["light"], fonts["regular"], fonts["medium"]

    advisee_name = datos["advisee"]
    ca_name = datos.get("ca") or "—"
    entries = datos["entries"]
    comentarios_sueltos = datos["comentarios_sueltos"]

    def sty(name, **kw):
        return ParagraphStyle(name, **kw)

    s_meta      = sty('meta',   fontSize=8.5, fontName=F_REG,   textColor=MUTED,  leading=13)
    s_adv_cover = sty('advc',   fontSize=26,  fontName=F_MED,   textColor=BLACK,  leading=30, spaceAfter=6)
    s_fecha     = sty('fecha',  fontSize=8,   fontName=F_REG,   textColor=ORANGE, leading=12, spaceAfter=10)
    s_label     = sty('lbl',    fontSize=7,   fontName=F_REG,   textColor=MUTED,  leading=10, spaceAfter=5)
    s_text      = sty('txt',    fontSize=9.5, fontName=F_LIGHT, textColor=BLACK,  leading=15)
    s_ap_texto  = sty('apx',    fontSize=9,   fontName=F_LIGHT, textColor=BLACK,  leading=14, spaceAfter=8)
    s_section_h = sty('sech',   fontSize=14,  fontName=F_MED,   textColor=BLACK,  leading=18, spaceAfter=4)
    s_suelto    = sty('suelto', fontSize=9.5, fontName=F_LIGHT, textColor=BLACK,  leading=15)

    os.makedirs(config.CARPETA_WEB, exist_ok=True)
    slug = slug_archivo(advisee_name)
    output_path = os.path.join(config.CARPETA_WEB, f"opiniones_ca_{slug}.pdf")

    doc = SimpleDocTemplate(
        output_path, pagesize=A4,
        rightMargin=2 * cm, leftMargin=2 * cm,
        topMargin=2 * cm, bottomMargin=2 * cm,
        title=f"Opiniones CA — {advisee_name}",
    )
    story = []
    pw, ph = A4

    def esc(t):
        return html_lib.escape(t or "").replace("\n", "<br/>")

    # Cabecera: nombre advisee + logo (si existe), CA, fecha
    story.append(Spacer(1, 1.5 * cm))
    nombre_par = Paragraph(esc(advisee_name), s_adv_cover)
    if os.path.exists(_LOGO_PATH):
        try:
            from reportlab.lib.utils import ImageReader
            iw, ih = ImageReader(_LOGO_PATH).getSize()
            logo_w = 3.4 * cm
            logo_h = logo_w * ih / iw
            logo_img = Image(_LOGO_PATH, width=logo_w, height=logo_h)
            name_logo_row = Table(
                [[nombre_par, logo_img]],
                colWidths=[pw - 4 * cm - 4 * cm, 4 * cm],
            )
            name_logo_row.setStyle(TableStyle([
                ('VALIGN',        (0, 0), (-1, -1), 'MIDDLE'),
                ('LEFTPADDING',   (0, 0), (-1, -1), 0),
                ('RIGHTPADDING',  (0, 0), (-1, -1), 0),
                ('TOPPADDING',    (0, 0), (-1, -1), 0),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
                ('ALIGN',         (1, 0), (1, 0),   'RIGHT'),
            ]))
            story.append(name_logo_row)
        except Exception:
            logging.exception("No se pudo incrustar el logo en el PDF de opiniones")
            story.append(nombre_par)
    else:
        story.append(nombre_par)

    story.append(Spacer(1, 0.5 * cm))
    story.append(Paragraph(f"CA · {esc(ca_name)}", s_meta))
    story.append(Paragraph(datetime.now(config.ZONA_HORARIA_MADRID).strftime('%d · %m · %Y'), s_meta))
    story.append(Spacer(1, 0.8 * cm))
    story.append(HRFlowable(width='100%', thickness=0.8, color=BORDER))
    story.append(Spacer(1, 0.8 * cm))

    # Entradas cronológicas: opinión CA (izq) | resumen sobre el que opinó (drch)
    for i, entry in enumerate(entries):
        block = [Paragraph(entry['fecha'].upper(), s_fecha)]

        labels_row = Table(
            [[Paragraph("OPINIÓN CA", s_label),
              Paragraph("SOBRE QUÉ HA OPINADO", s_label)]],
            colWidths=[8 * cm, 7.7 * cm],
        )
        labels_row.setStyle(TableStyle([
            ('LEFTPADDING',   (0, 0), (-1, -1), 0),
            ('RIGHTPADDING',  (0, 0), (-1, -1), 0),
            ('TOPPADDING',    (0, 0), (-1, -1), 0),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
        ]))
        block.append(labels_row)
        block.append(Spacer(1, 0.15 * cm))

        content_row = Table(
            [[Paragraph(esc(entry['opinion_ca']), s_text),
              Paragraph(esc(entry['resumen']), s_ap_texto)]],
            colWidths=[8 * cm, 7.7 * cm],
        )
        content_row.setStyle(TableStyle([
            ('VALIGN',        (0, 0), (-1, -1), 'TOP'),
            ('LEFTPADDING',   (0, 0), (0, -1),  0),
            ('RIGHTPADDING',  (0, 0), (0, -1),  20),
            ('LEFTPADDING',   (1, 0), (1, -1),  16),
            ('RIGHTPADDING',  (1, 0), (1, -1),  0),
            ('TOPPADDING',    (0, 0), (-1, -1), 0),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
            ('LINEBEFORE',    (1, 0), (1, -1),  0.5, BORDER),
        ]))
        block.append(content_row)

        story.append(KeepTogether(block))
        story.append(Spacer(1, 0.7 * cm))

        if i < len(entries) - 1:
            story.append(HRFlowable(width='100%', thickness=0.5, color=BORDER))
            story.append(Spacer(1, 0.7 * cm))

    # Sección final: comentarios sueltos (filas sin resumen)
    if comentarios_sueltos:
        if entries:
            story.append(PageBreak())
        story.append(Spacer(1, 0.5 * cm))
        story.append(Paragraph("Comentarios y notas extra", s_section_h))
        story.append(Spacer(1, 0.1 * cm))
        story.append(HRFlowable(width=1.5 * cm, thickness=3, color=ORANGE, spaceAfter=14))
        for comentario in comentarios_sueltos:
            story.append(Paragraph(f"— {esc(comentario)}", s_suelto))
            story.append(Spacer(1, 0.4 * cm))

    if not entries and not comentarios_sueltos:
        story.append(Paragraph("Sin opiniones registradas todavía.", s_text))

    doc.build(story, canvasmaker=_canvas_maker(advisee_name, ca_name, F_REG))
    logging.info("PDF de opiniones CA guardado: %s", output_path)
    return output_path


# ── HTML ──────────────────────────────────────────────────────────────────────

def generar_html_opiniones_ca(datos: dict) -> str:
    """Genera el HTML (estilo Igeneris) y devuelve la ruta."""
    advisee = datos["advisee"]
    ca_name = datos.get("ca") or "—"
    entries = datos["entries"]
    comentarios_sueltos = datos["comentarios_sueltos"]

    def esc(t):
        return html_lib.escape(t or "").replace("\n", "<br>")

    fecha = datetime.now(config.ZONA_HORARIA_MADRID).strftime("%d · %m · %Y")

    bloques = []
    for entry in entries:
        bloques.append(f"""
        <article class="entry">
          <p class="fecha">{esc(entry['fecha']).upper()}</p>
          <div class="cols">
            <div class="col-op">
              <p class="lbl">OPINIÓN CA</p>
              <p class="op">{esc(entry['opinion_ca'])}</p>
            </div>
            <div class="col-res">
              <p class="lbl">SOBRE QUÉ HA OPINADO</p>
              <p class="res">{esc(entry['resumen'])}</p>
            </div>
          </div>
        </article>""")
    entries_html = "\n".join(bloques) if bloques else "<p class='fine'>Sin opiniones con resumen todavía.</p>"

    comentarios_html = ""
    if comentarios_sueltos:
        items = "\n".join(f"<li>{esc(c)}</li>" for c in comentarios_sueltos)
        comentarios_html = f"""
        <section class="extra">
          <h2>Comentarios y notas extra</h2>
          <div class="rule"></div>
          <ul>{items}</ul>
        </section>"""

    contenido = f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Opiniones CA — {esc(advisee)}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Outfit:wght@200;400;500&display=swap">
<style>
{config.IGENERIS_CSS}
/* Tokens y tipografía del sistema de diseño Igeneris (sobreescriben IGENERIS_CSS) */
:root {{ --ink: #000000; --muted: rgba(0,0,0,.55); --line: #DBDBDE; --paper: #FFFFFF; --soft: #F4F4F7; }}
body {{ font-family: 'Outfit', system-ui, sans-serif; font-weight: 200; color: #000000; }}
h1, h2, h3, .brand {{ font-family: 'TT Firs Neue', 'Outfit', system-ui, sans-serif; font-weight: 500; letter-spacing: -0.01em; line-height: 1.1; }}
.shell {{ max-width: 960px; margin: 0 auto; padding-bottom: 60px; }}
.top {{ padding-top: clamp(42px, 8vw, 92px); margin-bottom: 28px; display: flex; align-items: flex-end; justify-content: space-between; gap: 24px; }}
.top h1 {{ font-size: clamp(34px, 6vw, 64px); }}
.top .meta {{ text-align: right; font-size: 13px; color: var(--muted); }}
.entry {{ padding: 26px 0; border-bottom: 1px solid var(--line); }}
.entry:last-of-type {{ border-bottom: 0; }}
.fecha {{ color: #F23C14; font-size: 12px; font-weight: 700; letter-spacing: .04em; margin: 0 0 12px; }}
.cols {{ display: grid; grid-template-columns: 1fr 1fr; gap: 28px; }}
.col-res {{ border-left: 1px solid var(--line); padding-left: 28px; }}
.lbl {{ color: var(--muted); font-size: 11px; letter-spacing: .04em; margin: 0 0 6px; }}
.op, .res {{ margin: 0; color: var(--ink); line-height: 1.5; }}
.res {{ font-size: 14px; }}
.extra {{ margin-top: 48px; }}
.extra h2 {{ font-size: 22px; margin: 0 0 8px; }}
.extra .rule {{ width: 40px; height: 3px; background: #F23C14; margin-bottom: 18px; }}
.extra ul {{ list-style: none; padding: 0; margin: 0; }}
.extra li {{ padding: 10px 0; border-bottom: 1px solid var(--line); color: var(--ink); line-height: 1.5; }}
.extra li::before {{ content: "— "; color: var(--muted); }}
@media (max-width: 720px) {{ .cols {{ grid-template-columns: 1fr; }} .col-res {{ border-left: 0; padding-left: 0; }} }}
</style>
</head>
<body>
<main class="page shell">
<nav class="nav">
  <a class="brand" href="javascript:void(0)" onclick="window.close()">igeneris</a>
  <div class="nav-links"><button class="secondary" onclick="window.close()">Cerrar</button></div>
</nav>
<div class="top">
  <h1>{esc(advisee)}</h1>
  <div class="meta">CA · {esc(ca_name)}<br>{fecha}</div>
</div>
{entries_html}
{comentarios_html}
</main>
</body>
</html>"""

    os.makedirs(config.CARPETA_WEB, exist_ok=True)
    slug = slug_archivo(advisee)
    ruta = os.path.join(config.CARPETA_WEB, f"opiniones_ca_{slug}.html")
    with open(ruta, "w", encoding="utf-8") as f:
        f.write(contenido)
    logging.info("HTML de opiniones CA guardado: %s", ruta)
    return ruta


# ── Caché ─────────────────────────────────────────────────────────────────────

def _huella_datos(datos: dict) -> str:
    payload = {
        "v": 1,
        "ca": datos.get("ca", ""),
        "entries": datos.get("entries", []),
        "comentarios": datos.get("comentarios_sueltos", []),
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


def _ruta_cache(slug: str) -> str:
    return os.path.join(config.CARPETA_WEB, f"opiniones_ca_{slug}_cache.json")


def _leer_cache(slug: str) -> dict | None:
    ruta = _ruta_cache(slug)
    if not os.path.exists(ruta):
        return None
    try:
        with open(ruta, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _escribir_cache(slug: str, huella: str) -> None:
    os.makedirs(config.CARPETA_WEB, exist_ok=True)
    with open(_ruta_cache(slug), "w", encoding="utf-8") as f:
        json.dump({"huella": huella}, f)


# ── Punto de entrada ──────────────────────────────────────────────────────────

def generar_resumen_opiniones_ca(advisee: str, ca_nombre: str = "", anonimo: bool = False) -> str:
    """
    Lee las opiniones del CA en Notion → genera PDF + HTML en CARPETA_WEB.
    Reutiliza caché si los datos no han cambiado.

    Returns:
        slug (str): nombre base de los archivos (opiniones_ca_{slug}.pdf / .html).

    Raises:
        ValueError: si el advisee no tiene ninguna opinión registrada.
        RuntimeError: si reportlab no está instalado (no se puede generar el PDF).
    """
    datos = obtener_datos_opiniones_ca(advisee, ca_nombre, anonimo=anonimo)
    if not datos["entries"] and not datos["comentarios_sueltos"]:
        raise ValueError(f"No hay opiniones del CA registradas para '{advisee}'.")

    slug = slug_archivo(advisee)
    huella = _huella_datos(datos)
    ruta_pdf = os.path.join(config.CARPETA_WEB, f"opiniones_ca_{slug}.pdf")
    ruta_html = os.path.join(config.CARPETA_WEB, f"opiniones_ca_{slug}.html")
    cache = _leer_cache(slug)

    if (
        cache and cache.get("huella") == huella
        and os.path.exists(ruta_html) and os.path.exists(ruta_pdf)
    ):
        logging.info("Opiniones CA en caché para %s, reutilizando.", advisee)
        return slug

    generar_html_opiniones_ca(datos)
    generar_pdf_opiniones_ca(datos)
    _escribir_cache(slug, huella)
    return slug
