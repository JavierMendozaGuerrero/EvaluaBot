"""
Skill: Informe anual IGENERIS
No requiere ninguna base de Notion adicional. Usa las bases existentes:
  - "Evaluaciones - {nombre}"  → evaluaciones mensuales
  - "Opiniones - {nombre}"     → opiniones del CA
  - "Objetivos - {nombre}"     → objetivos por persona (y revela el nombre del CA)
"""

import hashlib
import html as html_lib
import json
import logging
import os
from datetime import datetime, timezone

from . import config
from .clients import Document, anthropic_client
from .notion_service import (
    listar_bbdd_evaluados,
    obtener_ca_de_empleado,
    obtener_evaluaciones_por_evaluado,
    obtener_opiniones_ca_por_advisee,
    obtener_objetivos_persona,
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

_LABELS_NIVEL = [
    ("lider",    "Líder"),
    ("equipo",   "Miembros de tu equipo"),
    ("sin_nivel","Sin nivel especificado"),
]


# ── Criterios DTI por cargo ───────────────────────────────────────────────────

_CRITERIOS_DTI: dict[str, dict[str, list[str]]] = {
    "gestion_proyecto": {
        "analyst": [
            "Priorizar tareas y repartir de forma adecuada los tiempos",
            "Entregar su trabajo a tiempo",
            "Responsabilizarse del buen devenir de sus tareas y subtareas sin necesidad de que se lo recuerden",
            "Es proactivo, detecta necesidades del proyecto y cómo puede aportar valor antes de que alguien se lo diga",
            "Demuestra un compromiso alto hacia un resultado excelente del proyecto",
            "Detecta y avisa de cuellos de botella o posibles problemas intentando aportar soluciones",
            "Muestra disposición y proactividad para encontrar las herramientas que necesita",
            "Demuestra compromiso con las necesidades del proyecto (puntualidad, carga de trabajo, flexibilidad)",
            "Demuestra flexibilidad y motivación hacia la materia del proyecto independientemente de preferencias personales",
        ],
        "associate": [
            "Define y ejecuta con autonomía el plan de trabajo de su área de responsabilidad",
            "Responsabilizarse del proyecto y sus necesidades (desbloquear problemas, establecer reuniones, puntos de seguimiento)",
            "Responsabilizarse de los tiempos del proyecto y de la calidad de los entregables",
            "Identificar las piezas y elementos necesarios para la consecución de un proyecto (herramientas, workshops, sesiones, discusiones internas, con cliente, etc.)",
            "Gestiona adecuadamente y vela por la consecución de todos los elementos necesarios internos",
            "Distribuye adecuadamente las tareas entre los miembros del equipo según cargas de trabajo y perfiles",
            "Vela por mantener un ritmo de trabajo apropiado anticipándose a cuellos de botella o picos de trabajo",
            "Identifica y comunica al responsable del proyecto posibles riesgos y bloqueos",
            "Se focaliza en lo que es más importante (80/20)",
        ],
        "associate sr": [
            "Define el planning de proyecto en profundidad identificando los puntos más complicados",
            "Define el alcance y marco de trabajo del proyecto y lo ajusta de forma continua a la realidad",
            "Identifica nuevas oportunidades para Igeneris que puedan surgir del proyecto (upselling, cross selling)",
            "Es capaz de gestionar un proyecto (estándar y no estándar) entendiendo las necesidades del cliente y ajustando el marco",
            "Se anticipa a posibles riesgos del proyecto y lidera sus posibles planes de contingencia",
        ],
        "manager": [
            "Todo lo de Associate Sr, más:",
            "Ejerce una buena gestión de los tiempos en la organización del proyecto",
            "Sigue una metodología/sello Igeneris, sumada a una base estratégica",
            "Prevé la organización y los posibles riesgos del proyecto, propone un plan de priorización de tareas",
            "Gestiona y resuelve problemas que surgen a lo largo del proyecto",
        ],
    },
    "calidad_tecnica": {
        "analyst": [
            "Se esfuerza y preocupa por entregar su trabajo con máxima calidad",
            "El trabajo que presenta no necesita ser revisado (más de lo necesario) por un tercero",
            "Adquiere y pone en práctica los conocimientos básicos del proyecto (sector, metodología, digitales)",
            "Adquiere un criterio propio sobre la materia del proyecto o tarea",
            "Maneja las herramientas y programas utilizadas en el día a día",
            "Tiene ojo (auto-)crítico para evaluar que la calidad de un trabajo esté conforme con las necesidades de la tarea",
            "Demuestra solvencia en la parte numérica del proyecto si aplica",
        ],
        "associate": [
            "Muestra mediante el ejemplo el nivel de calidad que se ha de cuidar en cada fase del proyecto, sirviendo de guía o referencia",
            "Vela por que el trabajo de los analistas/en prácticas tenga la calidad técnica requerida",
            "Desarrolla la línea de pensamiento y razonamiento numérica necesaria (modelos financieros, magnitudes, economics)",
            "Reta los conceptos numéricos o cualitativos desarrollados para asegurar su rigor",
            "Mantiene el orden en el proyecto — gestión externa, interna y documental, asegurando que la información esté disponible y sea útil",
            "Demuestra madurez en las ideas y tareas en las que trabaja",
            "Aporta un valor fundamental en hipótesis, conclusiones, recomendaciones y presentaciones finales",
        ],
        "associate sr": [
            "Asegura una coherencia a nivel de proyecto en el discurso, el racional, los economics",
            "Identifica de forma rápida las carencias de un proyecto o entregable y sabe subsanarlas eficientemente",
            "Apuesta por ir 'más allá' en los proyectos y traslada al equipo cómo hacerlo",
            "Propone nuevas formas creativas de solucionar problemas",
        ],
        "manager": [
            "Ejerce de referente técnico y estratégico",
            "Se empapa y aterriza el conocimiento sobre la industria en la que se trabaja",
            "Utiliza su experiencia previa en otros proyectos",
            "Garantiza el correcto funcionamiento del equipo y la calidad del entregable a cliente en tiempo y forma",
        ],
    },
    "trabajo_en_equipo": {
        "analyst": [
            "Sabe levantar la mano cuando no tiene capacidad para hacer una tarea",
            "Sus compañeros confían en él porque demuestra un ownership de sus tareas",
            "Se muestra disponible para ayudar a otros compañeros cuando lo necesitan",
            "Contribuye proactivamente al buen clima en el equipo",
            "Acepta las dinámicas de trabajo en equipo y contribuye al buen funcionamiento del equipo",
            "Apoya a sus compañeros en aquellos ámbitos en los que puedan necesitar ayuda",
            "Se preocupa de aprender de sus compañeros y estar al mismo nivel de conocimientos relativos al proyecto",
        ],
        "associate": [
            "Está disponible y accesible para atender a los diferentes miembros de su equipo y guiarlos",
            "Guía al equipo con el ejemplo",
            "Se encarga de que el equipo esté al mismo nivel de información y conocimientos, y da apoyo técnico cuando se necesite",
            "Se asegura que los tiempos dedicados por los analistas/becarios en cada tarea sean los adecuados",
        ],
        "associate sr": [
            "Es un referente para el equipo en cuanto a forma de trabajar y aspiración dentro de Igeneris",
            "Ayuda al equipo a sacar lo mejor de ellos y superar su nivel de calidad, ayudándoles a desarrollarse profesionalmente",
            "Gestiona de forma eficiente la distribución de trabajo del equipo según tiempos y perfiles",
            "Transmite de forma contundente feedback de mejora a los compañeros asegurando que se desarrollen correctamente",
            "Inspira y motiva al equipo a todos los niveles de la pirámide",
            "Está atento a bloqueos y problemas",
        ],
        "manager": [
            "Es capaz de repartir un rol a cada miembro del equipo dentro de las responsabilidades y capacidades reales de cada uno",
            "Es transparente con el equipo",
            "Ejerce con criterio el reparto de tareas en relación con los recursos individuales de cada miembro y el tiempo de inversión",
            "Desarrolla un plan de priorización de tareas donde su equipo pueda entender cuáles son los objetivos y cómo organizarse",
        ],
    },
    "comunicacion": {
        "analyst": [
            "Demuestra una comunicación (oral y escrita) efectiva y asertiva",
            "Muestra una buena comunicación no verbal",
            "Comunica de forma efectiva su criterio al resto del equipo",
            "Demuestra capacidad de razonar sobre su criterio y modificarlo si fuese incorrecto o necesario",
        ],
        "associate": [
            "Comunica de forma efectiva las tareas y prioridades a todos los miembros del equipo",
            "Guía y motiva a los miembros del equipo para sacar lo mejor de ellos y mantener un clima de trabajo positivo",
            "Transmite de forma certera las necesidades del proyecto, especialmente cuando requiere un esfuerzo especial",
            "Sabe construir el storytelling y el racional de una idea, explicársela desde cero a un interlocutor y convencerle de que tiene sentido",
        ],
        "associate sr": [
            "Tiene una alta capacidad de síntesis de los problemas y de exposición tanto internamente como hacia cliente",
            "Comunica de forma clara a todos los niveles de la organización del cliente adaptando el discurso y contenido a cada auditorio",
            "Argumenta con seguridad y convincentemente, siendo capaz de reaccionar a argumentaciones del cliente",
        ],
        "manager": [
            "Transparencia en la comunicación a lo largo del proyecto para que el equipo esté alineado con el cliente/proyecto",
            "Sabe dar una comunicación asertiva al equipo",
            "Sabe comunicar al cliente adaptando el discurso dependiendo de las necesidades del proyecto y de las reacciones potenciales del cliente",
        ],
    },
    "relacion_cliente": {
        "analyst": [
            "Participa en reuniones con clientes",
            "Entiende las dinámicas con el cliente y el trato que se le debe dar",
        ],
        "associate": [
            "Define y prepara las sesiones de trabajo con el cliente",
            "Logra confianza y credibilidad con los niveles del cliente con los que le corresponde relacionarse, transmitiendo seguridad y profesionalidad",
            "Lidera sesiones de trabajo con el cliente de forma asistida por alguien con más seniority",
            "Lidera sesiones de trabajo con el cliente de forma autónoma",
        ],
        "associate sr": [
            "Crea un vínculo con el cliente y es capaz de entender sus necesidades para con el proyecto",
            "Lidera los workshops y sesiones de trabajo con el cliente más complicados / coordina y supervisa que las reuniones estén bien pensadas y ejecutadas",
            "Es un referente para el cliente en todos los aspectos que abarca el proyecto e incluso más allá del alcance del mismo",
        ],
        "manager": [
            "Mantiene una buena comunicación con el cliente",
            "Sabe preguntar al cliente qué necesita y cuáles son sus expectativas para no ir apagando fuegos posteriormente",
            "Conduce eficazmente las expectativas del cliente, contribuyendo a la satisfacción con el resultado del proyecto",
        ],
    },
}

_ETIQUETAS_DIM = {
    "gestion_proyecto": "Gestión del proyecto",
    "calidad_tecnica": "Calidad técnica",
    "trabajo_en_equipo": "Trabajo en equipo",
    "comunicacion": "Comunicación",
    "relacion_cliente": "Relación con el cliente",
}

_ORDEN_CARGO = ["analyst", "associate", "associate sr", "manager"]


def _nivel_cargo(cargo: str) -> str | None:
    c = cargo.strip().lower()
    if c == "analyst":
        return "analyst"
    if c in ("sr associate", "associate sr"):
        return "associate sr"
    if c == "associate":
        return "associate"
    if c in ("manager", "director"):
        return "manager"
    return None


def _criterios_para_prompt(cargo: str) -> str:
    nivel = _nivel_cargo(cargo)
    if not nivel:
        return ""
    idx = _ORDEN_CARGO.index(nivel)
    bloques = [
        f"Lo que se espera de un {cargo} (nivel {nivel}) y niveles superiores como referencia:"
    ]
    for dim_key, dim_label in _ETIQUETAS_DIM.items():
        dim_criterios = _CRITERIOS_DTI.get(dim_key, {})
        lineas = []
        for lvl in _ORDEN_CARGO[max(0, idx - 1):]:
            criterios = dim_criterios.get(lvl, [])
            if criterios:
                lineas.append(f"  [{lvl.title()}]: " + " / ".join(criterios))
        if lineas:
            bloques.append(f"\n{dim_label}:\n" + "\n".join(lineas))
    return "\n".join(bloques)


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
      - evaluaciones mensuales (desde "Evaluaciones - {nombre}")
      - opiniones del CA (desde "Opiniones - {nombre}")
      - objetivos (desde "Objetivos empleados"), de donde también se extrae el CA
    """
    # 1. Evaluaciones mensuales
    evaluaciones = []
    try:
        evaluaciones = obtener_evaluaciones_por_evaluado(nombre)
    except Exception:
        logging.warning("No se encontraron evaluaciones mensuales para %s.", nombre)

    # 2. Objetivos (también revelan el nombre del CA)
    objetivos = []
    try:
        objetivos = obtener_objetivos_persona(nombre)
    except Exception:
        logging.warning("No se encontraron objetivos para %s.", nombre)

    # Nombre del CA: primero desde objetivos, si no desde Lista CA
    ca_nombre = objetivos[0].get("ca", "") if objetivos else ""
    if not ca_nombre:
        try:
            ca_nombre = obtener_ca_de_empleado(nombre) or ""
        except Exception:
            pass

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
        _GRUPOS = [
            ("lider",     "EVALUACIONES DEL LÍDER"),
            ("equipo",    "EVALUACIONES DE MIEMBROS DEL EQUIPO (iguales y subordinados)"),
            ("sin_nivel", "EVALUACIONES SIN NIVEL ESPECIFICADO (datos anteriores al sistema de jerarquía)"),
        ]
        por_rel: dict = {"lider": [], "equipo": [], "sin_nivel": []}
        for ev in evaluaciones:
            rel = ev.get("relacion", "")
            if rel == "superior":
                por_rel["lider"].append(ev)
            elif rel in ("igual", "inferior"):
                por_rel["equipo"].append(ev)
            else:
                por_rel["sin_nivel"].append(ev)
        for rel_key, encabezado in _GRUPOS:
            evs = por_rel.get(rel_key, [])
            if not evs:
                continue
            bloques.append(f"\n=== {encabezado} ===")
            for ev in evs:
                proyecto  = ev.get("proyecto") or "Sin proyecto"
                evaluador = ev.get("persona_que_evalua") or ev.get("nombre") or "Desconocido"
                fecha     = (ev.get("fecha") or "")[:10]
                q1        = ev.get("q1", "")
                q2        = ev.get("q2", "")
                bloques.append(
                    f"[{fecha}] Proyecto: {proyecto} | Evaluador: {evaluador} | "
                    f"Valoración: {q1} | Ejemplo: {q2}"
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

    criterios_bloque = _criterios_para_prompt(cargo)
    criterios_section = (
        f"\n\nCRITERIOS DTI DE EVALUACIÓN (úsalos para calibrar el feedback según el cargo):\n{criterios_bloque}"
        if criterios_bloque else ""
    )

    system = (
        "Eres el director de RRHH de IGENERIS. "
        "A partir de las opiniones del CA y las evaluaciones mensuales del empleado, "
        "genera el contenido del informe anual de evaluación. "
        "Ten en cuenta los criterios DTI: lo que es suficiente para un Analyst puede ser lo mínimo esperado para un Manager. "
        "Devuelve ÚNICAMENTE un JSON válido (sin bloques markdown) con esta estructura:\n"
        "{\n"
        '  "<clave_dimension>": {\n'
        '    "lider": "bullet 1\\nbullet 2 (lo que dice su líder/superior)",\n'
        '    "equipo": "bullet 1\\nbullet 2 (lo que dicen iguales y subordinados)",\n'
        '    "sin_nivel": "bullet 1\\nbullet 2 (evaluaciones sin nivel especificado)"\n'
        "  },\n"
        "  ...\n"
        '  "contribution_to_firm": "bullets sobre contribución a la empresa...",\n'
        '  "resultado": "valoración global en 2-3 frases"\n'
        "}\n\n"
        f"Dimensiones requeridas: {dims_lista}, contribution_to_firm, resultado.\n"
        "Para cada dimensión agrupa: 'lider' = evaluadores superiores, "
        "'equipo' = evaluadores del mismo nivel o subordinados, "
        "'sin_nivel' = sin jerarquía especificada. "
        "Omite las claves que no tengan datos. "
        "contribution_to_firm y resultado son cadenas planas, no objetos. "
        "Basa todo en los datos reales. "
        "Si no hay información para una dimensión, escribe 'Sin información suficiente' en la clave correspondiente."
    )

    respuesta = anthropic_client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4000,
        system=system,
        messages=[{
            "role": "user",
            "content": (
                f"Empleado: {emp_data['empleado']}\n"
                f"Cargo: {cargo or 'No especificado'}\n"
                f"CA: {emp_data.get('ca', 'No especificado')}\n"
                f"{criterios_section}\n\n"
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
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    lineas = [l.strip(" •-–") for l in (texto or "").strip().splitlines() if l.strip()]
    if not lineas:
        return
    for i, linea in enumerate(lineas):
        p = cell.paragraphs[0] if i == 0 else cell.add_paragraph()
        if i == 0:
            p.clear()
        p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        _dxr(p, f"• {linea}", size=9)


def _dx_bullets_por_nivel(cell, contenido):
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    if not isinstance(contenido, dict):
        _dx_bullets(cell, contenido)
        return
    primer = True
    for nivel_key, label in _LABELS_NIVEL:
        texto = (contenido.get(nivel_key) or "").strip()
        if not texto:
            continue
        p = cell.paragraphs[0] if primer else cell.add_paragraph()
        if primer:
            p.clear()
        primer = False
        _dxr(p, label + ":", bold=True, size=8)
        for linea in texto.splitlines():
            linea = linea.strip(" •-–")
            if linea:
                pb = cell.add_paragraph()
                pb.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
                _dxr(pb, f"• {linea}", size=9)


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
        _dx_bullets_por_nivel(c2, comentarios.get(clave, ""))
    return tabla


# ── HTML: generación ─────────────────────────────────────────────────────────

def guardar_informe_anual_html(emp_data: dict, comentarios: dict, cargo: str = "") -> str:
    def esc(v):
        return html_lib.escape(str(v or ""))

    def bullets_html(texto):
        lineas = [ln.strip(" •-–") for ln in (texto or "").strip().splitlines() if ln.strip()]
        return "<br>".join(f"• {esc(ln)}" for ln in lineas) if lineas else "—"

    def bullets_html_por_nivel(contenido):
        if not isinstance(contenido, dict):
            return bullets_html(contenido)
        partes = []
        for nivel_key, label in _LABELS_NIVEL:
            texto = (contenido.get(nivel_key) or "").strip()
            if not texto:
                continue
            lineas = [ln.strip(" •-–") for ln in texto.splitlines() if ln.strip()]
            buls = "<br>".join(f"• {esc(ln)}" for ln in lineas)
            partes.append(f"<span style='font-size:11px;font-weight:700'>{esc(label)}:</span><br>{buls}")
        return "<br><br>".join(partes) if partes else "—"

    def filas_dims(dims):
        filas = ""
        for clave, etiqueta in dims:
            filas += f"<tr><td>{esc(etiqueta)}</td><td class='nc'>X</td><td>{bullets_html_por_nivel(comentarios.get(clave,''))}</td></tr>"
        return filas

    cargo_lower = cargo.strip().lower()
    requiere_liderazgo = any(c in cargo_lower for c in _REQUIERE_LIDERAZGO)

    cargo_row = f"<tr><td><strong>Cargo</strong></td><td>{esc(cargo)}</td></tr>" if cargo else ""

    liderazgo_bloque = ""
    if requiere_liderazgo:
        liderazgo_bloque = f"""
        <h2 class="sec">LIDERAZGO</h2>
        <table class="et"><thead><tr><th>Dimensión</th><th class="nc">Nota</th><th>Comentarios del evaluador</th></tr></thead>
        <tbody>{filas_dims(_DIMS_LIDERAZGO)}</tbody></table>"""

    objetivos_html = ""
    objetivos = emp_data.get("objetivos", [])
    if objetivos:
        items_html = ""
        for obj in objetivos:
            titulo_o = esc(obj.get("titulo", ""))
            tipo_o = esc(obj.get("tipo", ""))
            kpis_o = esc(obj.get("kpis", ""))
            desc_o = esc(obj.get("descripcion", ""))
            ca_o = esc(obj.get("ca", ""))
            fecha_o = esc((obj.get("fecha") or "")[:10])
            header = f"<strong>{titulo_o}</strong>"
            if tipo_o:
                header += f" <span class='fine'>({tipo_o})</span>"
            meta = f"<span class='fine'>{ca_o} — {fecha_o}</span>" if (ca_o or fecha_o) else ""
            kpis_block = f"<p><em>KPIs:</em> {kpis_o}</p>" if kpis_o else ""
            desc_block = f"<p>{desc_o}</p>" if desc_o else ""
            items_html += f"<div style='margin-bottom:12px'><p>{header}</p>{meta}{kpis_block}{desc_block}</div>"
        objetivos_html = items_html
    else:
        objetivos_html = "<p>Sin objetivos registrados.</p>"

    fecha = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    año = datetime.now(timezone.utc).year

    contenido = f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Informe anual — {esc(emp_data['empleado'])}</title>
<style>
{config.IGENERIS_CSS}
.shell {{ max-width: 960px; margin: 0 auto; padding-bottom: 60px; }}
.top {{ padding-top: clamp(42px, 8vw, 92px); margin-bottom: 36px; }}
.it {{ width: 100%; border-collapse: collapse; margin-bottom: 24px; font-size: 14px; }}
.it td {{ border: 1px solid var(--ink); padding: 8px 14px; }}
.et {{ width: 100%; border-collapse: collapse; margin-bottom: 24px; font-size: 14px; }}
.et th, .et td {{ border: 1px solid var(--ink); padding: 8px 12px; vertical-align: top; text-align: justify; }}
.et th {{ background: var(--soft); font-weight: 700; font-size: 12px; text-transform: uppercase; letter-spacing: .05em; }}
.et td:first-child {{ width: 200px; font-weight: 500; }}
.nc {{ width: 60px; text-align: center; }}
.sec {{ font-size: 13px; text-transform: uppercase; letter-spacing: .08em; border-bottom: 2px solid var(--ink); padding-bottom: 6px; margin: 32px 0 14px; color: var(--ink); }}
.rg {{ display: grid; grid-template-columns: 130px 1fr; border: 1px solid var(--ink); font-size: 14px; }}
.rg > div {{ padding: 14px 16px; }}
.rg > div:first-child {{ border-right: 1px solid var(--ink); text-align: center; font-weight: 700; }}
</style>
</head>
<body>
<main class="page shell">
<nav class="nav">
  <a class="brand" href="javascript:void(0)" onclick="window.close()">igeneris</a>
  <div class="nav-links"><button class="secondary" onclick="window.close()">Cerrar</button></div>
</nav>
<div class="top">
  <p class="kicker">Evaluación anual {año}</p>
  <h1>{esc(emp_data['empleado'])}</h1>
  <p>Generado el {fecha}</p>
</div>

<table class="it">
  <tr><td><strong>Nombre</strong></td><td>{esc(emp_data['empleado'])}</td></tr>
  {cargo_row}
  <tr><td><strong>Career Advisor</strong></td><td>{esc(emp_data.get('ca') or '—')}</td></tr>
</table>

<h2 class="sec">CALIFICACIÓN {año}</h2>
<table class="et">
  <thead><tr><th>Dimensión</th><th class="nc">Nota</th><th>Comentarios del evaluador</th></tr></thead>
  <tbody>{filas_dims(_DIMS_PROYECTOS)}</tbody>
</table>

{liderazgo_bloque}

<h2 class="sec">CONTRIBUTION TO THE FIRM</h2>
<p>{bullets_html(comentarios.get('contribution_to_firm',''))}</p>

<h2 class="sec">RESULTADO</h2>
<div class="rg">
  <div>Nota global<br><strong>X / 5</strong></div>
  <div>{esc(comentarios.get('resultado','—'))}</div>
</div>

<h2 class="sec">OBJETIVOS {año + 1}</h2>
{objetivos_html}
</main>
</body>
</html>"""

    os.makedirs(config.CARPETA_WEB, exist_ok=True)
    slug = slug_archivo(emp_data["empleado"])
    ruta = os.path.join(config.CARPETA_WEB, f"informe_anual_{slug}.html")
    with open(ruta, "w", encoding="utf-8") as f:
        f.write(contenido)
    logging.info("Informe anual HTML guardado: %s", ruta)
    return slug


# ── Word: generación ─────────────────────────────────────────────────────────

def guardar_informe_anual_word(emp_data: dict, comentarios: dict, cargo: str = "") -> str:
    if Document is None:
        raise RuntimeError("Instala python-docx: pip install python-docx")

    from docx.shared import Cm, Pt
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    año = datetime.now(timezone.utc).year
    doc = Document()
    sec = doc.sections[0]
    for attr in ("left_margin", "right_margin", "top_margin", "bottom_margin"):
        setattr(sec, attr, Cm(1.76))

    # Cabecera
    cab = doc.add_paragraph()
    cab.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _dxr(cab, f"IGENERIS  —  EVALUACIÓN ANUAL {año}", bold=True, size=14)
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
    _dxt(doc, f"CALIFICACIÓN {año}")
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
    p_contrib.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
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
    c1.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    _dxr(c1.paragraphs[0], comentarios.get("resultado", "—"), size=9)
    doc.add_paragraph()

    # OBJETIVOS 2026
    _dxt(doc, f"OBJETIVOS {año + 1}")
    objetivos = emp_data.get("objetivos", [])
    if objetivos:
        for obj in objetivos:
            titulo_o = obj.get("titulo", "")
            tipo_o = obj.get("tipo", "")
            kpis_o = obj.get("kpis", "")
            desc_o = obj.get("descripcion", "")
            ca_o = obj.get("ca", "")
            fecha_o = (obj.get("fecha") or "")[:10]
            p_titulo = doc.add_paragraph()
            r = p_titulo.add_run(titulo_o + (f" ({tipo_o})" if tipo_o else ""))
            r.bold = True
            r.font.name = "Arial"
            r.font.size = Pt(9)
            if ca_o or fecha_o:
                _dxr(doc.add_paragraph(), f"{ca_o} — {fecha_o}".strip(" —"), size=8)
            if kpis_o:
                _dxr(doc.add_paragraph(), f"KPIs: {kpis_o}", size=9)
            if desc_o:
                _dxr(doc.add_paragraph(), desc_o, size=9)
            doc.add_paragraph()
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
        "v": 2,
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
            f"No hay opiniones del CA ni evaluaciones mensuales para '{evaluado}'."
        )

    slug = slug_archivo(evaluado)
    huella = _huella_datos(emp_data)
    ruta_docx = os.path.join(config.CARPETA_WEB, f"informe_anual_{slug}.docx")
    ruta_html = os.path.join(config.CARPETA_WEB, f"informe_anual_{slug}.html")
    cache = _leer_cache(slug)

    if cache and cache.get("huella") == huella and os.path.exists(ruta_docx) and os.path.exists(ruta_html):
        logging.info("Informe anual en caché para %s, reutilizando.", evaluado)
        return slug

    comentarios = interpretar_evaluaciones_anual(emp_data, cargo=cargo)
    slug = guardar_informe_anual_word(emp_data, comentarios, cargo=cargo)
    guardar_informe_anual_html(emp_data, comentarios, cargo=cargo)
    _escribir_cache(slug, huella)
    return slug
