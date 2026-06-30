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
import re
from datetime import datetime, timezone

from . import config
from .clients import Document, anthropic_client
from .notion_service import (
    listar_bbdd_evaluados,
    obtener_ca_de_empleado,
    obtener_evaluaciones_por_evaluado,
    obtener_opiniones_ca_por_advisee,
    obtener_objetivos_persona,
    obtener_criterios_evaluacion,
    obtener_comentarios_personales,
    obtener_barbecho_por_empleado,
)
from .project_evals import obtener_evaluaciones_proyecto_por_evaluado
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

# Claves de comentarios que contienen bullets agrupados por nivel (dict lider/equipo/sin_nivel)
_CLAVES_POR_NIVEL = {c for c, _ in (*_DIMS_PROYECTOS, *_DIMS_LIDERAZGO)}
# Claves de comentarios que son texto plano con bullets
_CLAVES_PLANAS = {"contribution_to_firm"}

# Token de cita por tipo de fuente:
#   E = evaluación mensual · O = opinión CA · P = evaluación de proyecto
#   S = seguimiento personal · B = barbecho
_CITE_RE = re.compile(r"\[([EOPSB]\d+)\]")

_MESES_ES = [
    "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
    "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre",
]

# Dimensiones de Proyectos con la etiqueta tal y como aparece en la plantilla PDF
_DIMS_PDF = [
    ("gestion_proyecto",  "Gestión proyecto"),
    ("calidad_tecnica",   "Calidad técnica"),
    ("trabajo_en_equipo", "Trabajo en equipo"),
    ("comunicacion",      "Comunicación"),
    ("relacion_cliente",  "Relación cliente"),
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


def _grupo_por_cargo(cargo: str) -> str:
    c = cargo.lower()
    if "palantir" in c:
        return "Palantir"
    if "head" in c:
        return "MiddleOffice"
    return "Negocio"


def _criterios_para_prompt(cargo: str) -> str:
    nivel = _nivel_cargo(cargo)
    grupo = _grupo_por_cargo(cargo)

    # Intentar leer desde Notion
    try:
        criterios_notion = obtener_criterios_evaluacion(grupo)
    except Exception:
        criterios_notion = {}

    if criterios_notion:
        idx = _ORDEN_CARGO.index(nivel) if nivel and nivel in _ORDEN_CARGO else -1
        bloques = [f"Lo que se espera de un {cargo} en {grupo} y niveles superiores como referencia:"]
        for dim_label, niveles_dict in criterios_notion.items():
            lineas = []
            niveles_orden = [lvl for lvl in _ORDEN_CARGO if lvl in niveles_dict]
            for lvl in (niveles_orden[max(0, idx - 1):] if idx >= 0 else niveles_orden):
                criterios = niveles_dict.get(lvl, [])
                if criterios:
                    lineas.append(f"  [{lvl.title()}]: " + " / ".join(criterios))
            if lineas:
                bloques.append(f"\n{dim_label}:\n" + "\n".join(lineas))
        return "\n".join(bloques)

    # Fallback al diccionario hardcodeado (solo Negocio)
    if not nivel:
        return ""
    idx = _ORDEN_CARGO.index(nivel)
    bloques = [f"Lo que se espera de un {cargo} (nivel {nivel}) y niveles superiores como referencia:"]
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

    # 3. Opiniones del CA (contienen las notas del CA y los resúmenes del chatbot)
    opiniones = []
    try:
        opiniones = obtener_opiniones_ca_por_advisee(ca_nombre, nombre)
    except Exception:
        logging.warning("No se encontraron opiniones del CA para %s.", nombre)

    # 4. Evaluaciones de proyecto (todas las recibidas, de todos los proyectos)
    evals_proyecto = []
    try:
        evals_proyecto = obtener_evaluaciones_proyecto_por_evaluado(nombre)
    except Exception:
        logging.warning("No se encontraron evaluaciones de proyecto para %s.", nombre)

    # 5. Seguimiento personal (comentarios personales)
    seguimiento = []
    try:
        seguimiento = obtener_comentarios_personales(nombre)
    except Exception:
        logging.warning("No se encontró seguimiento personal para %s.", nombre)

    # 6. Barbecho (labores en periodo sin proyecto → contribución a la firma)
    barbecho = []
    try:
        barbecho = obtener_barbecho_por_empleado(nombre)
    except Exception:
        logging.warning("No se encontraron registros de barbecho para %s.", nombre)

    return {
        "empleado": nombre,
        "ca": ca_nombre,
        "opiniones_ca": opiniones,
        "evaluaciones": evaluaciones,
        "evals_proyecto": evals_proyecto,
        "seguimiento": seguimiento,
        "barbecho": barbecho,
        "objetivos": objetivos,
    }


# ── Claude: interpretación ────────────────────────────────────────────────────

_MES_ABBR = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]


def _mes_tag(fecha: str) -> str:
    """'2025-03-15' -> 'mar 25'. Para que Claude valore la evolución temporal."""
    try:
        return f"{_MES_ABBR[int(fecha[5:7]) - 1]} {fecha[2:4]}"
    except Exception:
        return "s/f"


def _formatear_contexto(emp_data: dict) -> tuple[str, dict]:
    """Construye el texto que se pasa a Claude y el mapa de fuentes citables.

    Devuelve (texto, fuentes) donde fuentes mapea cada id de cita
    (``E3``, ``O1``, ``P2``, ``S1``, ``B1``) a ``{"url", "tipo", "label", "texto"}``.
    Cada línea va prefijada con su id y con la etiqueta de mes para que Claude
    cite y, además, valore la evolución a lo largo del año.
    """
    bloques = []
    fuentes: dict[str, dict] = {}

    # ── Opiniones del CA (notas del CA + resúmenes del chatbot) ── [O#] ──────
    opiniones = sorted(emp_data.get("opiniones_ca", []), key=lambda x: (x.get("fecha") or ""))
    if opiniones:
        bloques.append("=== OPINIONES DEL CA (orden cronológico) ===")
        for i, op in enumerate(opiniones, 1):
            fecha = (op.get("fecha") or "")[:10] or "Sin fecha"
            partes = []
            if op.get("resumen_advisee"):
                partes.append(f"Resumen (chatbot): {op['resumen_advisee']}")
            if op.get("opinion"):
                partes.append(f"Nota del CA: {op['opinion']}")
            if not partes:
                continue
            cid = f"O{i}"
            fuentes[cid] = {
                "url": op.get("url", ""), "tipo": "opinion", "fecha": (op.get("fecha") or "")[:10],
                "label": f"Opinión CA · {fecha}", "texto": " | ".join(partes),
            }
            bloques.append(f"[{cid}] [{_mes_tag(fecha)}] " + " | ".join(partes))

    # ── Evaluaciones mensuales, agrupadas por jerarquía y cronológicas ── [E#] ─
    evaluaciones = emp_data.get("evaluaciones", [])
    if evaluaciones:
        _GRUPOS = [
            ("lider",     "EVALUACIONES MENSUALES DEL LÍDER"),
            ("equipo",    "EVALUACIONES MENSUALES DE MIEMBROS DEL EQUIPO (iguales y subordinados)"),
            ("sin_nivel", "EVALUACIONES MENSUALES SIN NIVEL ESPECIFICADO"),
        ]
        _ETIQUETA_REL = {"lider": "líder", "equipo": "equipo", "sin_nivel": "sin nivel"}
        por_rel: dict = {"lider": [], "equipo": [], "sin_nivel": []}
        for ev in evaluaciones:
            rel = ev.get("relacion", "")
            if rel == "superior":
                por_rel["lider"].append(ev)
            elif rel in ("igual", "inferior"):
                por_rel["equipo"].append(ev)
            else:
                por_rel["sin_nivel"].append(ev)
        n = 0
        for rel_key, encabezado in _GRUPOS:
            evs = sorted(por_rel.get(rel_key, []), key=lambda x: (x.get("fecha") or ""))
            if not evs:
                continue
            bloques.append(f"\n=== {encabezado} (orden cronológico) ===")
            for ev in evs:
                n += 1
                cid = f"E{n}"
                proyecto  = ev.get("proyecto") or "Sin proyecto"
                evaluador = ev.get("persona_que_evalua") or ev.get("nombre") or "Desconocido"
                fecha     = (ev.get("fecha") or "")[:10]
                q1, q2 = ev.get("q1", ""), ev.get("q2", "")
                fuentes[cid] = {
                    "url": ev.get("url", ""), "tipo": "evaluacion", "fecha": fecha,
                    "label": f"{proyecto} · {_ETIQUETA_REL[rel_key]} · {fecha}".strip(" ·"),
                    "evaluador": evaluador,
                    "texto": f"Valoración: {q1} | Ejemplo: {q2}",
                }
                bloques.append(
                    f"[{cid}] [{_mes_tag(fecha)}] Proyecto: {proyecto} | Evaluador: {evaluador} | "
                    f"Valoración: {q1} | Ejemplo: {q2}"
                )

    # ── Evaluaciones de proyecto (por proyecto y cronológicas) ── [P#] ───────
    evals_proy = sorted(emp_data.get("evals_proyecto", []), key=lambda x: (x.get("fecha") or ""))
    if evals_proy:
        bloques.append("\n=== EVALUACIONES DE PROYECTO (orden cronológico) ===")
        for i, pe in enumerate(evals_proy, 1):
            cid = f"P{i}"
            proyecto  = pe.get("proyecto") or "Sin proyecto"
            evaluador = pe.get("evaluador") or "Desconocido"
            tipo      = pe.get("tipo") or ""
            fecha     = (pe.get("fecha") or "")[:10]
            respuestas = pe.get("respuestas") or ""
            fuentes[cid] = {
                "url": pe.get("url", ""), "tipo": "proyecto", "fecha": fecha,
                "label": f"{proyecto} · {tipo} · {fecha}".strip(" ·"),
                "evaluador": evaluador,
                "texto": respuestas,
            }
            bloques.append(
                f"[{cid}] [{_mes_tag(fecha)}] Proyecto: {proyecto} | Evaluador: {evaluador} | "
                f"Tipo: {tipo} | Respuestas: {respuestas}"
            )

    # ── Seguimiento personal ── [S#] ─────────────────────────────────────────
    seguimiento = sorted(emp_data.get("seguimiento", []), key=lambda x: (x.get("fecha") or ""))
    if seguimiento:
        bloques.append("\n=== SEGUIMIENTO PERSONAL (orden cronológico) ===")
        for i, sg in enumerate(seguimiento, 1):
            cid = f"S{i}"
            fecha = (sg.get("fecha") or "")[:10]
            autor = sg.get("autor") or ""
            comentario = sg.get("comentario") or ""
            fuentes[cid] = {
                "url": sg.get("url", ""), "tipo": "seguimiento", "fecha": fecha,
                "label": f"Seguimiento personal · {fecha}", "evaluador": autor,
                "texto": comentario,
            }
            bloques.append(f"[{cid}] [{_mes_tag(fecha)}] Seguimiento ({autor}): {comentario}")

    # ── Barbecho (labores sin proyecto → contribución a la firma) ── [B#] ────
    barbecho = sorted(emp_data.get("barbecho", []), key=lambda x: (x.get("fecha") or ""))
    if barbecho:
        bloques.append("\n=== BARBECHO — labores en periodo sin proyecto (orden cronológico) ===")
        for i, bb in enumerate(barbecho, 1):
            cid = f"B{i}"
            fecha = (bb.get("fecha") or "")[:10]
            area = bb.get("area") or ""
            labores = bb.get("labores") or ""
            fuentes[cid] = {
                "url": bb.get("url", ""), "tipo": "barbecho", "fecha": fecha,
                "label": f"Barbecho{f' ({area})' if area else ''} · {fecha}", "texto": labores,
            }
            bloques.append(f"[{cid}] [{_mes_tag(fecha)}] Barbecho ({area}): {labores}")

    texto = "\n".join(bloques) if bloques else "(Sin datos de evaluación disponibles)"
    return texto, fuentes


def _filtrar_bullets_citados(texto: str, fuentes: dict, descartados: list) -> str:
    """Devuelve solo los bullets que tienen al menos una cita válida.

    - Bullet sin ninguna cita -> se descarta (no hay forma de comprobar su origen).
    - Citas a ids inexistentes -> se eliminan del texto.
    Esto convierte cualquier invención en algo estructural: si Claude no puede
    señalar de qué evaluación sale una afirmación, la afirmación no aparece.
    """
    lineas_ok = []
    for linea in (texto or "").splitlines():
        bruta = linea.strip(" •-–\t")
        if not bruta:
            continue
        ids = _CITE_RE.findall(bruta)
        validos = [i for i in ids if i in fuentes]
        if not validos:
            descartados.append(bruta)
            continue
        # Elimina citas a ids inexistentes, conserva las válidas
        invalidos = set(ids) - set(validos)
        for inv in invalidos:
            bruta = bruta.replace(f"[{inv}]", "")
        lineas_ok.append(re.sub(r"\s{2,}", " ", bruta).strip())
    return "\n".join(lineas_ok)


def _validar_citas(comentarios: dict, fuentes: dict) -> dict:
    """Aplica el filtro de citas a todas las dimensiones y registra lo descartado."""
    descartados: list[str] = []
    for clave, valor in list(comentarios.items()):
        if clave in _CLAVES_POR_NIVEL and isinstance(valor, dict):
            comentarios[clave] = {
                nivel: _filtrar_bullets_citados(txt, fuentes, descartados)
                for nivel, txt in valor.items()
            }
        elif clave in _CLAVES_PLANAS and isinstance(valor, str):
            comentarios[clave] = _filtrar_bullets_citados(valor, fuentes, descartados)
    if descartados:
        logging.warning(
            "[informe] %d bullet(s) descartados por no citar ninguna fuente válida: %s",
            len(descartados), descartados,
        )
    comentarios["_bullets_descartados"] = descartados
    return comentarios


def _recolectar_afirmaciones(comentarios: dict) -> list[dict]:
    """Extrae cada bullet con sus citas para auditarlo. No incluye 'resultado' (síntesis)."""
    afirmaciones = []
    for clave, valor in comentarios.items():
        if clave.startswith("_"):
            continue
        if clave in _CLAVES_POR_NIVEL and isinstance(valor, dict):
            textos = [(nivel, t) for nivel, t in valor.items()]
        elif clave in _CLAVES_PLANAS and isinstance(valor, str):
            textos = [("", valor)]
        else:
            continue
        for _nivel, bloque in textos:
            for linea in (bloque or "").splitlines():
                bruta = linea.strip(" •-–\t")
                if not bruta:
                    continue
                ids = _CITE_RE.findall(bruta)
                if ids:
                    afirmaciones.append({"clave": clave, "texto": bruta, "citas": ids})
    return afirmaciones


def _verificar_soporte(comentarios: dict, fuentes: dict) -> list[dict]:
    """Segunda pasada (auditor): marca afirmaciones NO respaldadas por su cita.

    Política: avisar, no borrar. Devuelve una lista de avisos; el CA decide.
    Si la llamada falla, devuelve [] (el informe sigue siendo válido).
    """
    if not anthropic_client:
        return []
    afirmaciones = _recolectar_afirmaciones(comentarios)
    if not afirmaciones:
        return []

    # Solo las fuentes realmente citadas, para acotar el contexto
    citadas = sorted({cid for a in afirmaciones for cid in a["citas"]})
    bloque_fuentes = "\n".join(
        f"[{cid}] {fuentes[cid].get('texto', '')}" for cid in citadas if cid in fuentes
    )
    bloque_afirmaciones = "\n".join(
        f"{i}. \"{a['texto']}\"" for i, a in enumerate(afirmaciones)
    )

    system = (
        "Eres un auditor de calidad de informes de RRHH. Recibes (A) el texto literal de las "
        "fuentes y (B) una lista numerada de afirmaciones, cada una con las citas [E#]/[O#] de "
        "las que dice provenir. Tu ÚNICA tarea es marcar las afirmaciones que NO estén "
        "respaldadas por el texto literal de su(s) cita(s): inventadas, exageradas, o que "
        "afirman más de lo que la fuente dice. Sé estricto pero justo: una reformulación fiel "
        "SÍ está respaldada. Devuelve ÚNICAMENTE un JSON válido: "
        '{"no_soportadas": [{"i": <numero de la afirmacion>, "motivo": "<breve>"}]}. '
        "Si todas están respaldadas, devuelve {\"no_soportadas\": []}."
    )
    try:
        respuesta = anthropic_client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1500,
            temperature=0,
            system=system,
            messages=[{
                "role": "user",
                "content": f"=== FUENTES ===\n{bloque_fuentes}\n\n=== AFIRMACIONES ===\n{bloque_afirmaciones}",
            }],
        )
        texto = "".join(b.text for b in respuesta.content if b.type == "text").strip()
        if texto.startswith("```"):
            texto = texto.split("```", 2)[1]
            if texto.startswith("json"):
                texto = texto[4:]
            texto = texto.rsplit("```", 1)[0]
        data = json.loads(texto.strip())
    except Exception:
        logging.exception("[informe] Falló la pasada de verificación; se omite.")
        return []

    avisos = []
    for item in data.get("no_soportadas", []):
        try:
            idx = int(item.get("i"))
        except (TypeError, ValueError):
            continue
        if 0 <= idx < len(afirmaciones):
            a = afirmaciones[idx]
            avisos.append({
                "afirmacion": a["texto"],
                "clave": a["clave"],
                "citas": a["citas"],
                "motivo": (item.get("motivo") or "").strip(),
            })
    if avisos:
        logging.warning("[informe] Verificador marcó %d afirmación(es) no respaldadas.", len(avisos))
    return avisos


def interpretar_evaluaciones_anual(emp_data: dict, cargo: str = "", criterios: str | None = None) -> dict:
    """
    Llama a Claude con el contexto de evaluaciones y opiniones.
    Devuelve un dict con bullets y notas por dimensión.

    `criterios`: texto de criterios ya renderizado. Si es None se obtiene de Notion.
    Pasarlo evita una segunda lectura de Notion cuando ya se computó para la huella de caché.
    """
    if not anthropic_client:
        raise RuntimeError("Falta ANTHROPIC_API_KEY o el paquete anthropic no está instalado.")

    cargo_lower = cargo.strip().lower()
    requiere_liderazgo = any(c in cargo_lower for c in _REQUIERE_LIDERAZGO)

    dims = list(_DIMS_PROYECTOS)
    if requiere_liderazgo:
        dims += list(_DIMS_LIDERAZGO)
    dims_lista = ", ".join(f'"{c}"' for c, _ in dims)

    criterios_bloque = _criterios_para_prompt(cargo) if criterios is None else criterios
    criterios_section = (
        f"\n\nCRITERIOS DTI DE EVALUACIÓN (úsalos para calibrar el feedback según el cargo):\n{criterios_bloque}"
        if criterios_bloque else ""
    )

    contexto, fuentes = _formatear_contexto(emp_data)

    system = (
        "Eres el director de RRHH de IGENERIS. A partir de TODAS las fuentes del empleado, "
        "genera el contenido del informe anual de evaluación. "
        "Ten en cuenta los criterios DTI: lo que es suficiente para un Analyst puede ser lo mínimo esperado para un Manager.\n\n"
        "FUENTES (cada línea lleva su etiqueta y el mes entre corchetes):\n"
        "  [O#] opinión del CA (sus notas + resúmenes del chatbot)\n"
        "  [E#] evaluación mensual (con jerarquía líder/equipo)\n"
        "  [P#] evaluación de proyecto\n"
        "  [S#] seguimiento personal\n"
        "  [B#] barbecho (labores en periodos sin proyecto)\n\n"
        "REGLA DE TRAZABILIDAD (obligatoria, sin excepciones):\n"
        "TODA afirmación que escribas debe terminar con la etiqueta o etiquetas de las que proviene, "
        "p. ej. 'Entrega su trabajo a tiempo [E3][P2]'. "
        "PROHIBIDO escribir cualquier afirmación que no esté literalmente respaldada por una línea citada. "
        "Si no hay evidencia para una dimensión, escribe exactamente 'Sin información suficiente' (sin cita). "
        "No inventes, no infieras, no generalices más allá del texto citado. No uses etiquetas inexistentes.\n\n"
        "EVOLUCIÓN TEMPORAL (importante):\n"
        "Los datos están en orden cronológico con su mes. NO promedies ni des una foto plana: describe la "
        "TRAYECTORIA a lo largo del año. No es lo mismo febrero que noviembre. Da MÁS PESO a lo más reciente, "
        "y cuando algo mejore o empeore, dilo citando ambos momentos (p. ej. 'empezó con poca autonomía en mar "
        "[E2] y cerró con mentalidad senior en nov [E9]'). Distingue también entre proyectos: un proyecto duro "
        "no es comparable a uno cómodo.\n\n"
        "BARBECHO: las labores de barbecho [B#] son, casi siempre, evidencia de 'contribution_to_firm' "
        "(contribución a la firma), no de las dimensiones de proyecto.\n\n"
        "Devuelve ÚNICAMENTE un JSON válido (sin bloques markdown) con esta estructura:\n"
        "{\n"
        '  "<clave_dimension>": {\n'
        '    "lider": "afirmación basada en evaluadores superiores [E1][P3]\\notra [E2]",\n'
        '    "equipo": "afirmación de iguales/subordinados [E4]",\n'
        '    "sin_nivel": "afirmación sin jerarquía clara [S1]"\n'
        "  },\n"
        "  ...\n"
        '  "contribution_to_firm": "bullets de contribución a la firma, cada uno con su cita [B1][O1]",\n'
        '  "resultado": "valoración global en 2-3 frases que resuma la evolución del año"\n'
        "}\n\n"
        f"Dimensiones requeridas: {dims_lista}, contribution_to_firm, resultado.\n"
        "Para cada dimensión agrupa: 'lider' = evaluadores superiores, "
        "'equipo' = mismo nivel o subordinados, 'sin_nivel' = sin jerarquía clara "
        "(coloca seguimiento personal y proyecto en el nivel que corresponda según quién evalúa). "
        "Omite las claves que no tengan datos. "
        "contribution_to_firm y resultado son cadenas planas, no objetos. "
        "resultado es una síntesis de lo ya afirmado; puede no llevar citas."
    )

    respuesta = anthropic_client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4000,
        temperature=0,
        system=system,
        messages=[{
            "role": "user",
            "content": (
                f"Empleado: {emp_data['empleado']}\n"
                f"Cargo: {cargo or 'No especificado'}\n"
                f"CA: {emp_data.get('ca', 'No especificado')}\n"
                f"{criterios_section}\n\n"
                f"{contexto}"
            ),
        }],
    )
    texto = "".join(b.text for b in respuesta.content if b.type == "text").strip()
    if texto.startswith("```"):
        texto = texto.split("```", 2)[1]
        if texto.startswith("json"):
            texto = texto[4:]
        texto = texto.rsplit("```", 1)[0]
    comentarios = json.loads(texto.strip())
    comentarios = _validar_citas(comentarios, fuentes)
    comentarios["_avisos_verificacion"] = _verificar_soporte(comentarios, fuentes)
    comentarios["_fuentes"] = fuentes
    return comentarios


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


def _dx_hyperlink(para, url, texto, size=9):
    """Inserta un hyperlink real de Word (azul, subrayado) al final del párrafo."""
    from docx.oxml.ns import qn
    from docx.oxml import OxmlElement
    r_id = para.part.relate_to(
        url,
        "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink",
        is_external=True,
    )
    hyperlink = OxmlElement("w:hyperlink")
    hyperlink.set(qn("r:id"), r_id)
    run = OxmlElement("w:r")
    rPr = OxmlElement("w:rPr")
    rFonts = OxmlElement("w:rFonts")
    rFonts.set(qn("w:ascii"), "Arial"); rFonts.set(qn("w:hAnsi"), "Arial")
    rPr.append(rFonts)
    sz = OxmlElement("w:sz"); sz.set(qn("w:val"), str(int(size * 2))); rPr.append(sz)
    color = OxmlElement("w:color"); color.set(qn("w:val"), "0563C1"); rPr.append(color)
    u = OxmlElement("w:u"); u.set(qn("w:val"), "single"); rPr.append(u)
    run.append(rPr)
    t = OxmlElement("w:t")
    t.set(qn("xml:space"), "preserve")
    t.text = texto
    run.append(t)
    hyperlink.append(run)
    para._p.append(hyperlink)
    return hyperlink


def _dx_internal_link(para, anchor, texto, size=9):
    """Inserta un enlace interno de Word a un marcador (bookmark) del mismo documento."""
    from docx.oxml.ns import qn
    from docx.oxml import OxmlElement
    hyperlink = OxmlElement("w:hyperlink")
    hyperlink.set(qn("w:anchor"), anchor)
    run = OxmlElement("w:r")
    rPr = OxmlElement("w:rPr")
    rFonts = OxmlElement("w:rFonts")
    rFonts.set(qn("w:ascii"), "Arial"); rFonts.set(qn("w:hAnsi"), "Arial")
    rPr.append(rFonts)
    sz = OxmlElement("w:sz"); sz.set(qn("w:val"), str(int(size * 2))); rPr.append(sz)
    color = OxmlElement("w:color"); color.set(qn("w:val"), "0563C1"); rPr.append(color)
    u = OxmlElement("w:u"); u.set(qn("w:val"), "single"); rPr.append(u)
    run.append(rPr)
    t = OxmlElement("w:t")
    t.set(qn("xml:space"), "preserve")
    t.text = texto
    run.append(t)
    hyperlink.append(run)
    para._p.append(hyperlink)
    return hyperlink


def _dx_bookmark(para, name, bm_id):
    """Marca el párrafo con un bookmark para que los enlaces internos puedan saltar a él."""
    from docx.oxml.ns import qn
    from docx.oxml import OxmlElement
    start = OxmlElement("w:bookmarkStart")
    start.set(qn("w:id"), str(bm_id))
    start.set(qn("w:name"), name)
    end = OxmlElement("w:bookmarkEnd")
    end.set(qn("w:id"), str(bm_id))
    para._p.insert(0, start)
    para._p.append(end)


def _dxr_con_citas(para, texto, fuentes=None, size=9):
    """Como _dxr pero convierte los tokens [E3]/[O1] en enlaces internos al anexo de Fuentes."""
    fuentes = fuentes or {}
    pos = 0
    for m in _CITE_RE.finditer(texto):
        if m.start() > pos:
            _dxr(para, texto[pos:m.start()], size=size)
        cid = m.group(1)
        if cid in fuentes:
            _dx_internal_link(para, f"fuente_{cid}", f"[{cid}]", size=size)
        else:
            _dxr(para, f"[{cid}]", size=size)
        pos = m.end()
    if pos < len(texto):
        _dxr(para, texto[pos:], size=size)


def _dx_bullets(cell, texto, fuentes=None):
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    lineas = [l.strip(" •-–") for l in (texto or "").strip().splitlines() if l.strip()]
    if not lineas:
        return
    for i, linea in enumerate(lineas):
        p = cell.paragraphs[0] if i == 0 else cell.add_paragraph()
        if i == 0:
            p.clear()
        p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        _dxr(p, "• ", size=9)
        _dxr_con_citas(p, linea, fuentes, size=9)


def _dx_bullets_por_nivel(cell, contenido, fuentes=None):
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    if not isinstance(contenido, dict):
        _dx_bullets(cell, contenido, fuentes)
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
                _dxr(pb, "• ", size=9)
                _dxr_con_citas(pb, linea, fuentes, size=9)


def _tabla_dims(doc, dims, comentarios, fuentes=None):
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
        _dx_bullets_por_nivel(c2, comentarios.get(clave, ""), fuentes)
    return tabla


# ── HTML: generación ─────────────────────────────────────────────────────────

def guardar_informe_anual_html(emp_data: dict, comentarios: dict, cargo: str = "") -> str:
    fuentes = comentarios.get("_fuentes", {})

    def esc(v):
        return html_lib.escape(str(v or ""))

    def linkify(texto_html):
        """Convierte los tokens [E3]/[O1] (ya escapados) en anclas internas al anexo de Fuentes."""
        def repl(m):
            cid = m.group(1)
            src = fuentes.get(cid)
            if src:
                return (f"<a class='cite' href='#fuente-{cid}' "
                        f"title='{esc(src.get('label', ''))}'>[{cid}]</a>")
            return f"<span class='cite cite-off'>[{cid}]</span>"
        return _CITE_RE.sub(repl, texto_html)

    def bullets_html(texto):
        lineas = [ln.strip(" •-–") for ln in (texto or "").strip().splitlines() if ln.strip()]
        return "<br>".join(f"• {linkify(esc(ln))}" for ln in lineas) if lineas else "—"

    def bullets_html_por_nivel(contenido):
        if not isinstance(contenido, dict):
            return bullets_html(contenido)
        partes = []
        for nivel_key, label in _LABELS_NIVEL:
            texto = (contenido.get(nivel_key) or "").strip()
            if not texto:
                continue
            lineas = [ln.strip(" •-–") for ln in texto.splitlines() if ln.strip()]
            buls = "<br>".join(f"• {linkify(esc(ln))}" for ln in lineas)
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

    # Panel de revisión (solo borrador): avisos del verificador + bullets descartados.
    # El CA decide qué hacer antes de publicar el informe final.
    avisos = comentarios.get("_avisos_verificacion", [])
    descartados = comentarios.get("_bullets_descartados", [])
    panel_revision = ""
    if avisos or descartados:
        partes_panel = []
        if avisos:
            items = ""
            for a in avisos:
                motivo = esc(a.get("motivo", ""))
                items += (f"<li>{linkify(esc(a.get('afirmacion', '')))}"
                          f"{f' — <em>{motivo}</em>' if motivo else ''}</li>")
            partes_panel.append(
                "<p class='rev-h'>⚠ Afirmaciones a revisar (posiblemente no respaldadas por su cita)</p>"
                f"<ul>{items}</ul>"
            )
        if descartados:
            items = "".join(f"<li>{esc(d)}</li>" for d in descartados)
            partes_panel.append(
                "<p class='rev-h'>🗑 Bullets descartados automáticamente (no citaban ninguna fuente)</p>"
                f"<ul>{items}</ul>"
            )
        panel_revision = (
            "<div class='revision'>"
            "<p class='rev-title'>Revisión del Career Advisor</p>"
            "<p class='rev-sub'>Este bloque solo aparece en el borrador. Revísalo y edítalo antes de publicar el informe final.</p>"
            + "".join(partes_panel) +
            "</div>"
        )

    # Anexo "Fuentes / Evidencia": cada cita del informe enlaza aquí (sin depender de Notion).
    _TIPO_LABEL = {
        "opinion": "Opinión CA", "evaluacion": "Evaluación mensual",
        "proyecto": "Evaluación de proyecto", "seguimiento": "Seguimiento personal",
        "barbecho": "Barbecho",
    }
    _ORDEN_TIPO = {"O": 0, "E": 1, "P": 2, "S": 3, "B": 4}

    def _sort_fuente(cid):
        return (_ORDEN_TIPO.get(cid[:1], 9), int(cid[1:]) if cid[1:].isdigit() else 0)

    fuentes_items = ""
    for cid in sorted(fuentes.keys(), key=_sort_fuente):
        src = fuentes[cid]
        evaluador = src.get("evaluador", "")
        meta = f" · <span class='f-eval'>{esc(evaluador)}</span>" if evaluador else ""
        fuentes_items += (
            f"<div class='fuente' id='fuente-{cid}'>"
            f"<span class='f-id'>[{cid}]</span> "
            f"<span class='f-tipo'>{esc(_TIPO_LABEL.get(src.get('tipo'), ''))}</span> · "
            f"<span class='f-label'>{esc(src.get('label', ''))}</span>{meta}"
            f"<p class='f-texto'>{esc(src.get('texto', '')) or '—'}</p>"
            f"</div>"
        )
    fuentes_html = (
        "<h2 class='sec'>FUENTES / EVIDENCIA</h2>"
        "<p class='f-intro'>Cada cita [X#] del informe enlaza aquí. Esta es la evidencia en bruto "
        "(proyecto, evaluador, fecha y texto) para que puedas contrastar cada afirmación.</p>"
        f"{fuentes_items}"
    ) if fuentes else ""

    fecha = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    # Mismo criterio que el Word: se evalúa el año anterior al de generación.
    año = datetime.now(timezone.utc).year - 1

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
.cite {{ font-size: 10px; font-weight: 600; color: #0563C1; text-decoration: none; vertical-align: super; padding: 0 1px; }}
.cite:hover {{ text-decoration: underline; }}
.cite-off {{ color: #999; cursor: help; }}
.revision {{ border: 1px solid #d9a300; background: #fff8e6; border-radius: 8px; padding: 16px 20px; margin-bottom: 28px; font-size: 13px; }}
.revision .rev-title {{ font-weight: 700; font-size: 14px; margin: 0 0 2px; color: #8a6d00; }}
.revision .rev-sub {{ margin: 0 0 12px; color: #8a6d00; font-size: 12px; }}
.revision .rev-h {{ font-weight: 700; margin: 12px 0 4px; }}
.revision ul {{ margin: 0 0 4px; padding-left: 20px; }}
.revision li {{ margin-bottom: 4px; }}
.f-intro {{ font-size: 13px; color: #555; margin-bottom: 16px; }}
.fuente {{ border-left: 3px solid var(--ink); padding: 8px 14px; margin-bottom: 12px; font-size: 13px; scroll-margin-top: 80px; }}
.fuente:target {{ background: #fff3cd; border-left-color: #d9a300; }}
.f-id {{ font-weight: 700; color: #0563C1; }}
.f-tipo {{ font-weight: 600; }}
.f-eval {{ font-style: italic; }}
.f-texto {{ margin: 6px 0 0; white-space: pre-line; }}
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

{panel_revision}

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

{fuentes_html}
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

def _celda_emp(cell, label, valor, w):
    """Rellena una celda de la cabecera: etiqueta bold o valor normal."""
    _dxb(cell); _dxw(cell, w)
    if label:
        _dxr(cell.paragraphs[0], label, bold=True, size=9)
    else:
        _dxr(cell.paragraphs[0], valor or "", size=9)


def guardar_informe_anual_word(emp_data: dict, comentarios: dict, cargo: str = "") -> str:
    """Genera el .docx replicando la plantilla oficial de EVALUACIÓN ANUAL de IGENERIS.

    Campos que el sistema no posee (CA '26, salarios, % variable, promoción, deadlines,
    y la NOTA por dimensión) se dejan en blanco para que el CA los rellene, igual que en
    la plantilla. Los comentarios por dimensión sí los redacta Claude, con sus citas a Notion.
    """
    if Document is None:
        raise RuntimeError("Instala python-docx: pip install python-docx")

    from docx.shared import Cm, Pt
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    fuentes = comentarios.get("_fuentes", {})
    ahora = datetime.now(timezone.utc)
    anio_eval = ahora.year - 1          # se evalúa el año anterior (p. ej. 2025 en marzo 2026)
    anio_sig = ahora.year               # año siguiente al evaluado
    fecha_txt = f"{_MESES_ES[ahora.month - 1]} {ahora.year}"
    yy = f"'{str(anio_eval)[-2:]}"      # "'25"
    yy_sig = f"'{str(anio_sig)[-2:]}"   # "'26"

    doc = Document()
    sec = doc.sections[0]
    for attr in ("left_margin", "right_margin", "top_margin", "bottom_margin"):
        setattr(sec, attr, Cm(1.76))

    # ── Marca + título ────────────────────────────────────────────────────────
    marca = doc.add_paragraph()
    marca.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _dxr(marca, ".Igeneris", bold=True, size=22)
    titulo = doc.add_paragraph()
    titulo.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _dxr(titulo, "EVALUACIÓN ANUAL", bold=True, size=13, underline=True)
    doc.add_paragraph()

    # ── Tabla de datos del empleado (4 columnas) ──────────────────────────────
    w1, w2, w3, w4 = 1.2, 2.24, 1.3, _CONTENT_W_IN - 1.2 - 2.24 - 1.3
    filas_emp = [
        ("Empleado", emp_data["empleado"], "Fecha", fecha_txt),
        (f"CA {yy}", emp_data.get("ca", ""), "Posición actual", cargo or ""),
        (f"CA {yy_sig}", "", "Salario actual", ""),
    ]
    t_emp = doc.add_table(rows=len(filas_emp), cols=4)
    t_emp.style = "Table Grid"
    for i, (l1, v1, l2, v2) in enumerate(filas_emp):
        c0, c1, c2, c3 = t_emp.rows[i].cells
        _celda_emp(c0, l1, None, w1)
        _celda_emp(c1, None, v1, w2)
        _celda_emp(c2, l2, None, w3)
        _celda_emp(c3, None, v2, w4)
    doc.add_paragraph()

    # ── CALIFICACIÓN {año} ────────────────────────────────────────────────────
    _dxt(doc, f"CALIFICACIÓN {anio_eval}")

    cargo_lower = cargo.strip().lower()
    requiere_liderazgo = any(c in cargo_lower for c in _REQUIERE_LIDERAZGO)
    dims = list(_DIMS_PDF)
    if requiere_liderazgo:
        dims += list(_DIMS_LIDERAZGO)

    w_dim, w_nota = 1.6, 0.6
    w_com = _CONTENT_W_IN - w_dim - w_nota
    t_cal = doc.add_table(rows=len(dims) + 1, cols=3)
    t_cal.style = "Table Grid"
    h0, h1, h2 = t_cal.rows[0].cells
    for c, txt, w, ctr in ((h0, "PROYECTOS", w_dim, False), (h1, "NOTA", w_nota, True), (h2, "COMENTARIOS", w_com, False)):
        _dxb(c); _dxw(c, w)
        _dxr(c.paragraphs[0], txt, bold=True, size=9, center=ctr)
    for i, (clave, etiqueta) in enumerate(dims):
        c0, c1, c2 = t_cal.rows[i + 1].cells
        _dxb(c0); _dxw(c0, w_dim)
        _dxb(c1); _dxw(c1, w_nota)
        _dxb(c2); _dxw(c2, w_com)
        _dxr(c0.paragraphs[0], etiqueta, size=9)
        # NOTA: en blanco, la rellena el CA
        _dx_bullets_por_nivel(c2, comentarios.get(clave, ""), fuentes)
    doc.add_paragraph()

    # ── Notas finales / retribución ───────────────────────────────────────────
    wc1, wc2, wc3, wc4 = 2.9, 0.7, 1.5, _CONTENT_W_IN - 2.9 - 0.7 - 1.5
    filas_ret = [
        ("Nota final Proyectos", "", "Variable (60%)", ""),
        ("Nota final Contrib. To the firm (10%)", "", "Variable", ""),
        ("Consecución Objetivos corp.", "", "Variable (30%)", "Total Variable " + yy + " ="),
    ]
    t_ret = doc.add_table(rows=len(filas_ret), cols=4)
    t_ret.style = "Table Grid"
    for i, (l1, v1, l2, v2) in enumerate(filas_ret):
        c0, c1, c2, c3 = t_ret.rows[i].cells
        for c, w in ((c0, wc1), (c1, wc2), (c2, wc3), (c3, wc4)):
            _dxb(c); _dxw(c, w)
        _dxr(c0.paragraphs[0], l1, bold=True, size=9)
        _dxr(c1.paragraphs[0], v1, size=9, center=True)
        _dxr(c2.paragraphs[0], l2, size=9)
        _dxr(c3.paragraphs[0], v2, bold=bool(v2), size=9)
    doc.add_paragraph()

    # ── RESULTADO EVAL {año} ──────────────────────────────────────────────────
    _dxt(doc, f"RESULTADO EVAL {yy}")
    wr = [1.2, 0.9, 1.4, 1.3, _CONTENT_W_IN - 1.2 - 0.9 - 1.4 - 1.3]
    t_res = doc.add_table(rows=1, cols=5)
    t_res.style = "Table Grid"
    cells = t_res.rows[0].cells
    textos_res = [("PROMOCIÓN", True, False), ("", False, True), (f"POSICIÓN {yy_sig}", True, False),
                  ("", False, True), ("Nuevo salario fijo =", True, False)]
    for c, w, (txt, bold, ctr) in zip(cells, wr, textos_res):
        _dxb(c); _dxw(c, w)
        _dxr(c.paragraphs[0], txt, bold=bold, size=9, center=ctr)
    doc.add_paragraph()

    # ── OPORTUNIDADES DE MEJORA / OBJETIVOS {año+1} ───────────────────────────
    _dxt(doc, f"OPORTUNIDADES DE MEJORA / OBJETIVOS {yy_sig}")
    objetivos = emp_data.get("objetivos", [])
    n_filas = max(3, len(objetivos))
    w_obj, w_dl = _CONTENT_W_IN - 0.9, 0.9
    t_obj = doc.add_table(rows=n_filas + 1, cols=2)
    t_obj.style = "Table Grid"
    ch0, ch1 = t_obj.rows[0].cells
    _dxb(ch0); _dxw(ch0, w_obj)
    _dxb(ch1); _dxw(ch1, w_dl)
    _dxr(ch0.paragraphs[0], "", size=9)
    _dxr(ch1.paragraphs[0], "Deadline", bold=True, size=9, center=True)
    for i in range(n_filas):
        c0, c1 = t_obj.rows[i + 1].cells
        _dxb(c0); _dxw(c0, w_obj)
        _dxb(c1); _dxw(c1, w_dl)
        texto_obj = ""
        if i < len(objetivos):
            o = objetivos[i]
            texto_obj = o.get("titulo") or o.get("descripcion") or ""
        _dxr(c0.paragraphs[0], f"{i + 1}.  {texto_obj}".rstrip(), size=9)

    # ── FUENTES / EVIDENCIA (anexo, cada cita salta aquí) ─────────────────────
    if fuentes:
        _ORDEN = {"O": 0, "E": 1, "P": 2, "S": 3, "B": 4}
        _TIPO = {
            "opinion": "Opinión CA", "evaluacion": "Evaluación mensual",
            "proyecto": "Evaluación de proyecto", "seguimiento": "Seguimiento personal",
            "barbecho": "Barbecho",
        }
        doc.add_paragraph()
        _dxt(doc, "FUENTES / EVIDENCIA")
        _dxr(doc.add_paragraph(),
             "Cada cita [X#] del informe enlaza a su ficha aquí: la evidencia en bruto para contrastar.",
             size=8)
        ordenadas = sorted(fuentes, key=lambda c: (_ORDEN.get(c[:1], 9), int(c[1:]) if c[1:].isdigit() else 0))
        for n, cid in enumerate(ordenadas, 1):
            src = fuentes[cid]
            p = doc.add_paragraph()
            _dx_bookmark(p, f"fuente_{cid}", n)
            _dxr(p, f"[{cid}] ", bold=True, size=9)
            _dxr(p, f"{_TIPO.get(src.get('tipo'), '')} · {src.get('label', '')}", bold=True, size=9)
            if src.get("evaluador"):
                _dxr(p, f" · {src['evaluador']}", size=9)
            _dxr(doc.add_paragraph(), src.get("texto", "") or "—", size=9)

    # ── Guardar ───────────────────────────────────────────────────────────────
    os.makedirs(config.CARPETA_WEB, exist_ok=True)
    slug = slug_archivo(emp_data["empleado"])
    ruta = os.path.join(config.CARPETA_WEB, f"informe_anual_{slug}.docx")
    doc.save(ruta)
    logging.info("Informe anual guardado: %s", ruta)
    return slug


# ── Caché ─────────────────────────────────────────────────────────────────────

def _huella_datos(emp_data: dict, cargo: str = "", criterios: str = "") -> str:
    datos = {
        "v": 4,
        "opiniones": emp_data.get("opiniones_ca", []),
        "evaluaciones": emp_data.get("evaluaciones", []),
        "evals_proyecto": emp_data.get("evals_proyecto", []),
        "seguimiento": emp_data.get("seguimiento", []),
        "barbecho": emp_data.get("barbecho", []),
        "cargo": (cargo or "").strip().lower(),
        # Criterios DTI de Notion (varían por grupo: Negocio/MiddleOffice/Palantir).
        # Si cambian en Notion, la huella cambia y el informe se regenera.
        "criterios": criterios or "",
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
    # Se computa una sola vez: alimenta la huella de caché y el prompt (evita doble lectura de Notion).
    criterios = _criterios_para_prompt(cargo)
    huella = _huella_datos(emp_data, cargo=cargo, criterios=criterios)
    ruta_docx = os.path.join(config.CARPETA_WEB, f"informe_anual_{slug}.docx")
    ruta_html = os.path.join(config.CARPETA_WEB, f"informe_anual_{slug}.html")
    cache = _leer_cache(slug)

    if cache and cache.get("huella") == huella and os.path.exists(ruta_docx) and os.path.exists(ruta_html):
        logging.info("Informe anual en caché para %s, reutilizando.", evaluado)
        return slug

    comentarios = interpretar_evaluaciones_anual(emp_data, cargo=cargo, criterios=criterios)
    slug = guardar_informe_anual_word(emp_data, comentarios, cargo=cargo)
    guardar_informe_anual_html(emp_data, comentarios, cargo=cargo)
    _escribir_cache(slug, huella)
    return slug
