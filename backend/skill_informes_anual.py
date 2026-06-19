"""
Skill: Informe anual IGENERIS
No requiere ninguna base de Notion adicional. Usa las bases existentes:
  - "Evaluaciones - {nombre}"  → evaluaciones de proyecto
  - "Opiniones - {nombre}"     → opiniones del CA
  - "Objetivos empleados"      → objetivos (y revela el nombre del CA)
"""

import hashlib
import json
import logging
import os

from . import config
from .clients import Document, anthropic_client
from .notion_service import (
    listar_bbdd_evaluados,
    obtener_evaluaciones_por_evaluado,
    obtener_opiniones_ca_por_advisee,
    obtener_objetivos,
)
from .utils import slug_archivo


# ── Constantes ────────────────────────────────────────────────────────────────

_REQUIERE_LIDERAZGO = {"sr associate", "manager", "director"}
_CONTENT_W_IN = 9906 / 1440  # ~6.88 pulgadas (A4 márgenes 1.76 cm)

_DIMS_PROYECTOS = [
    ("gestion_proyecto",  "Gestión del proyecto"),
    ("calidad_tecnica",   "Calidad técnica"),
    ("trabajo_en_equipo", "Trabajo en equipo"),
    ("comunicacion",      "Comunicación"),
    ("relacion_cliente",  "Relación con el cliente"),
]
_DIMS_LIDERAZGO = [
    ("liderazgo_desarrollo_talento", "Desarrollo de Talento"),
    ("liderazgo_motivacion",         "Motivación"),
    ("liderazgo_referente",          "Referente"),
]

_W_DIM  = 3.50
_W_NOTA = 0.60
_W_COM  = _CONTENT_W_IN - _W_DIM - _W_NOTA


# ── Lista de empleados ────────────────────────────────────────────────────────

def obtener_empleados_evaluacion_anual() -> list[str]:
    """
    Devuelve los empleados que tienen base "Evaluaciones - {nombre}" en Notion.
    Son los candidatos válidos para generar el informe anual.
    """
    try:
        bases = listar_bbdd_evaluados()
        return sorted(b["evaluado"] for b in bases if b.get("evaluado"))
    except Exception:
        logging.exception("Error listando empleados para informe anual.")
        return []


# ── Recopilar datos del empleado ──────────────────────────────────────────────

def obtener_datos_empleado_anual(nombre: str) -> dict:
    """
    Recopila toda la información disponible en Notion sobre el empleado:
      - evaluaciones de proyecto (desde "Evaluaciones - {nombre}")
      - opiniones del CA (desde "Opiniones - {nombre}")
      - objetivos (desde "Objetivos empleados"), de donde también se extrae el CA
    """
    # 1. Evaluaciones de proyecto
    evaluaciones = []
    try:
        evaluaciones = obtener_evaluaciones_por_evaluado(nombre)
    except Exception:
        logging.warning("No se encontraron evaluaciones de proyecto para %s.", nombre)

    # 2. Objetivos (también revelan el nombre del CA)
    objetivos = []
    try:
        objetivos = obtener_objetivos(nombre)
    except Exception:
        logging.warning("No se encontraron objetivos para %s.", nombre)

    # Nombre del CA: tomamos el más reciente de los objetivos
    ca_nombre = objetivos[0].get("ca", "") if objetivos else ""

    # 3. Opiniones del CA
    opiniones = []
    try:
        opiniones = obtener_opiniones_ca_por_advisee(ca_nombre, nombre)
    except Exception:
        logging.warning("No se encontraron opiniones del CA para %s.", nombre)

    return {
        "empleado": nombre,
        "ca": ca_nombre,
        "opiniones_ca": opiniones,
        "evaluaciones": evaluaciones,
        "objetivos": objetivos,
    }


# ── Claude: interpretación ────────────────────────────────────────────────────

def _formatear_contexto(emp_data: dict) -> str:
    """Construye el texto que se pasa a Claude como contexto."""
    bloques = []

    opiniones = emp_data.get("opiniones_ca", [])
    if opiniones:
        bloques.append("=== OPINIONES DEL CA ===")
        for op in opiniones:
            fecha = (op.get("fecha") or "")[:10] or "Sin fecha"
            partes = []
            if op.get("resumen_advisee"):
                partes.append(f"Resumen evaluaciones: {op['resumen_advisee']}")
            if op.get("opinion"):
                partes.append(f"Opinión CA: {op['opinion']}")
            if partes:
                bloques.append(f"[{fecha}] " + " | ".join(partes))

    evaluaciones = emp_data.get("evaluaciones", [])
    if evaluaciones:
        bloques.append("\n=== EVALUACIONES DE PROYECTO ===")
        for ev in evaluaciones:
            proyecto  = ev.get("proyecto") or "Sin proyecto"
            evaluador = ev.get("persona_que_evalua") or ev.get("nombre") or "Desconocido"
            fecha     = (ev.get("fecha") or "")[:10]
            sat       = ev.get("satisfaccion", "")
            mejor     = ev.get("mejor_aspecto", "")
            peor      = ev.get("peor_aspecto", "")
            bloques.append(
                f"[{fecha}] Proyecto: {proyecto} | Evaluador: {evaluador} | "
                f"Satisfacción: {sat}/5 | Mejor: {mejor} | Peor: {peor}"
            )

    return "\n".join(bloques) if bloques else "(Sin datos de evaluación disponibles)"


def interpretar_evaluaciones_anual(emp_data: dict, cargo: str = "") -> dict:
    """
    Llama a Claude con el contexto de evaluaciones y opiniones.
    Devuelve un dict con bullets y notas por dimensión.
    """
    if not anthropic_client:
        raise RuntimeError("Falta ANTHROPIC_API_KEY o el paquete anthropic no está instalado.")

    cargo_lower = cargo.strip().lower()
    requiere_liderazgo = any(c in cargo_lower for c in _REQUIERE_LIDERAZGO)

    dims = list(_DIMS_PROYECTOS)
    if requiere_liderazgo:
        dims += list(_DIMS_LIDERAZGO)
    dims_lista = ", ".join(f'"{c}"' for c, _ in dims)

    system = (
        "Eres el director de RRHH de IGENERIS. "
        "A partir de las opiniones del CA y las evaluaciones de proyecto del empleado, "
        "genera el contenido del informe anual de evaluación. "
        "Devuelve ÚNICAMENTE un JSON válido (sin bloques markdown) con esta estructura:\n"
        "{\n"
        '  "<clave_dimension>": "bullet 1\\nbullet 2\\n...",\n'
        "  ...\n"
        '  "contribution_to_firm": "bullets sobre contribución a la empresa...",\n'
        '  "resultado": "valoración global en 2-3 frases"\n'
        "}\n\n"
        f"Dimensiones requeridas: {dims_lista}, contribution_to_firm, resultado.\n"
        "Basa todo en los datos reales proporcionados. "
        "Si no hay información suficiente para una dimensión, escribe 'Sin información suficiente'."
    )

    respuesta = anthropic_client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2400,
        system=system,
        messages=[{
            "role": "user",
            "content": (
                f"Empleado: {emp_data['empleado']}\n"
                f"Cargo: {cargo or 'No especificado'}\n"
                f"CA: {emp_data.get('ca', 'No especificado')}\n\n"
                f"{_formatear_contexto(emp_data)}"
            ),
        }],
    )
    texto = "".join(b.text for b in respuesta.content if b.type == "text").strip()
    if texto.startswith("```"):
        texto = texto.split("```", 2)[1]
        if texto.startswith("json"):
            texto = texto[4:]
        texto = texto.rsplit("```", 1)[0]
    return json.loads(texto.strip())


# ── Word: helpers XML ─────────────────────────────────────────────────────────

def _dxb(cell):
    from docx.oxml.ns import qn
    from docx.oxml import OxmlElement
    tc = cell._tc
    tcPr = tc.get_or_add_tcPr()
    borders = OxmlElement("w:tcBorders")
    for lado in ("top", "left", "bottom", "right"):
        b = OxmlElement(f"w:{lado}")
        b.set(qn("w:val"), "single")
        b.set(qn("w:sz"), "4")
        b.set(qn("w:space"), "0")
        b.set(qn("w:color"), "000000")
        borders.append(b)
    tcPr.append(borders)


def _dxw(cell, inches):
    from docx.oxml.ns import qn
    from docx.oxml import OxmlElement
    tc = cell._tc
    tcPr = tc.get_or_add_tcPr()
    tcW = tcPr.find(qn("w:tcW"))
    if tcW is None:
        tcW = OxmlElement("w:tcW")
        tcPr.append(tcW)
    tcW.set(qn("w:w"), str(int(inches * 1440)))
    tcW.set(qn("w:type"), "dxa")


def _dxr(para, texto, bold=False, size=9, underline=False, center=False):
    from docx.shared import Pt
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    if center:
        para.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = para.add_run(texto)
    run.bold = bold
    run.underline = underline
    run.font.name = "Arial"
    run.font.size = Pt(size)
    return run


def _dxt(doc, texto):
    from docx.shared import Pt
    para = doc.add_paragraph()
    para.paragraph_format.space_before = Pt(10)
    para.paragraph_format.space_after  = Pt(3)
    _dxr(para, texto, bold=True, size=10, underline=True)
    return para


def _dx_bullets(cell, texto):
    lineas = [l.strip(" •-–") for l in (texto or "").strip().splitlines() if l.strip()]
    if not lineas:
        return
    for i, linea in enumerate(lineas):
        p = cell.paragraphs[0] if i == 0 else cell.add_paragraph()
        if i == 0:
            p.clear()
        _dxr(p, f"• {linea}", size=9)


def _tabla_dims(doc, dims, comentarios):
    tabla = doc.add_table(rows=len(dims) + 1, cols=3)
    tabla.style = "Table Grid"
    c0, c1, c2 = tabla.rows[0].cells
    for c, txt, w in ((c0, "Dimensión", _W_DIM), (c1, "Nota", _W_NOTA), (c2, "Comentarios del evaluador", _W_COM)):
        _dxb(c); _dxw(c, w)
        _dxr(c.paragraphs[0], txt, bold=True, size=9, center=(txt == "Nota"))
    for i, (clave, etiqueta) in enumerate(dims):
        c0, c1, c2 = tabla.rows[i + 1].cells
        _dxb(c0); _dxw(c0, _W_DIM)
        _dxb(c1); _dxw(c1, _W_NOTA)
        _dxb(c2); _dxw(c2, _W_COM)
        _dxr(c0.paragraphs[0], etiqueta, size=9)
        _dxr(c1.paragraphs[0], "X", size=9, center=True)
        _dx_bullets(c2, comentarios.get(clave, ""))
    return tabla


# ── Word: generación ─────────────────────────────────────────────────────────

def guardar_informe_anual_word(emp_data: dict, comentarios: dict, cargo: str = "") -> str:
    if Document is None:
        raise RuntimeError("Instala python-docx: pip install python-docx")

    from docx.shared import Cm, Pt
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    doc = Document()
    sec = doc.sections[0]
    for attr in ("left_margin", "right_margin", "top_margin", "bottom_margin"):
        setattr(sec, attr, Cm(1.76))

    # Cabecera
    cab = doc.add_paragraph()
    cab.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _dxr(cab, "IGENERIS  —  EVALUACIÓN ANUAL 2025", bold=True, size=14)
    doc.add_paragraph()

    # Datos del empleado
    info = [
        ("Nombre",         emp_data["empleado"]),
        ("Career Advisor", emp_data.get("ca", "")),
    ]
    if cargo:
        info.insert(1, ("Cargo", cargo))
    t_emp = doc.add_table(rows=len(info), cols=2)
    t_emp.style = "Table Grid"
    for i, (et, val) in enumerate(info):
        c0, c1 = t_emp.rows[i].cells
        _dxb(c0); _dxb(c1)
        _dxw(c0, 1.8); _dxw(c1, _CONTENT_W_IN - 1.8)
        _dxr(c0.paragraphs[0], et, bold=True, size=9)
        _dxr(c1.paragraphs[0], val or "—", size=9)

    doc.add_paragraph()

    # CALIFICACIÓN 2025
    _dxt(doc, "CALIFICACIÓN 2025")
    _tabla_dims(doc, _DIMS_PROYECTOS, comentarios)
    doc.add_paragraph()

    # LIDERAZGO (solo Sr Associate / Manager / Director)
    cargo_lower = cargo.strip().lower()
    if any(c in cargo_lower for c in _REQUIERE_LIDERAZGO):
        _dxt(doc, "LIDERAZGO")
        _tabla_dims(doc, _DIMS_LIDERAZGO, comentarios)
        doc.add_paragraph()

    # CONTRIBUTION TO THE FIRM
    _dxt(doc, "CONTRIBUTION TO THE FIRM")
    p_contrib = doc.add_paragraph()
    for linea in (comentarios.get("contribution_to_firm") or "—").strip().splitlines():
        linea = linea.strip(" •-–")
        if linea:
            run = p_contrib.add_run(f"• {linea}\n")
            run.font.name = "Arial"
            run.font.size = Pt(9)
    doc.add_paragraph()

    # RESULTADO
    _dxt(doc, "RESULTADO")
    t_res = doc.add_table(rows=1, cols=2)
    t_res.style = "Table Grid"
    c0, c1 = t_res.rows[0].cells
    _dxb(c0); _dxb(c1)
    _dxw(c0, 1.4); _dxw(c1, _CONTENT_W_IN - 1.4)
    _dxr(c0.paragraphs[0], "Nota global\nX / 5", bold=True, size=9, center=True)
    _dxr(c1.paragraphs[0], comentarios.get("resultado", "—"), size=9)
    doc.add_paragraph()

    # OBJETIVOS 2026
    _dxt(doc, "OBJETIVOS 2026")
    objetivos = emp_data.get("objetivos", [])
    if objetivos:
        obj_reciente = objetivos[0]
        ca_obj   = obj_reciente.get("ca", "")
        fecha_obj = (obj_reciente.get("fecha") or "")[:10]
        texto_obj = obj_reciente.get("objetivos", "")
        if ca_obj or fecha_obj:
            p_meta = doc.add_paragraph()
            _dxr(p_meta, f"Definidos por {ca_obj} — {fecha_obj}".strip(" —"), size=8)
        p_obj = doc.add_paragraph()
        for linea in texto_obj.strip().splitlines():
            linea = linea.strip()
            if linea:
                run = p_obj.add_run(f"• {linea}\n")
                run.font.name = "Arial"
                run.font.size = Pt(9)
    else:
        _dxr(doc.add_paragraph(), "Sin objetivos registrados.", size=9)

    # Guardar
    os.makedirs(config.CARPETA_WEB, exist_ok=True)
    slug = slug_archivo(emp_data["empleado"])
    ruta = os.path.join(config.CARPETA_WEB, f"informe_anual_{slug}.docx")
    doc.save(ruta)
    logging.info("Informe anual guardado: %s", ruta)
    return slug


# ── Caché ─────────────────────────────────────────────────────────────────────

def _huella_datos(emp_data: dict) -> str:
    datos = {
        "opiniones": emp_data.get("opiniones_ca", []),
        "evaluaciones": emp_data.get("evaluaciones", []),
    }
    return hashlib.sha256(
        json.dumps(datos, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


def _ruta_cache(slug: str) -> str:
    return os.path.join(config.CARPETA_WEB, f"informe_anual_{slug}_cache.json")


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

def generar_informe_anual(evaluado: str, cargo: str = "") -> str:
    """Lee Notion → interpreta con Claude → genera Word. Reutiliza caché si no hay cambios."""
    emp_data = obtener_datos_empleado_anual(evaluado)
    if not emp_data.get("opiniones_ca") and not emp_data.get("evaluaciones"):
        raise ValueError(
            f"No hay opiniones del CA ni evaluaciones de proyecto para '{evaluado}'."
        )

    slug = slug_archivo(evaluado)
    huella = _huella_datos(emp_data)
    ruta_docx = os.path.join(config.CARPETA_WEB, f"informe_anual_{slug}.docx")
    cache = _leer_cache(slug)

    if cache and cache.get("huella") == huella and os.path.exists(ruta_docx):
        logging.info("Informe anual en caché para %s, reutilizando.", evaluado)
        return slug

    comentarios = interpretar_evaluaciones_anual(emp_data, cargo=cargo)
    slug = guardar_informe_anual_word(emp_data, comentarios, cargo=cargo)
    _escribir_cache(slug, huella)
    return slug
