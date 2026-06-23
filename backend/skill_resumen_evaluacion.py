"""
Skill: Resumen de evaluación por competencias
Genera un resumen estructurado por apartados a partir del texto libre de evaluaciones,
calibrado según el cargo del advisee. Los criterios están hardcodeados y NO se envían
a Claude — solo se mandan los nombres de los apartados y el texto de evaluación.
"""

import logging

from .clients import anthropic_client


# ── Criterios por cargo ────────────────────────────────────────────────────────

CRITERIOS: dict[str, dict[str, list[str]]] = {
    "Analista": {
        "Gestión del proyecto": [
            "Priorizar tareas y reparte de forma adecuada los tiempos",
            "Entrega su trabajo a tiempo",
            "Se hace responsable del buen devenir de sus tareas y subtareas sin necesidad de recordárselo",
            "Es proactivo, detecta necesidades del proyecto y cómo puede aportar valor antes de que alguien se lo diga",
            "Demuestra un compromiso alto hacia un resultado excelente del proyecto",
            "Detecta y avisa de cuellos de botella existentes o posibles problemas intentando aportar soluciones",
            "Muestra disposición y proactividad para encontrar las herramientas que necesita",
            "Demuestra compromiso con las necesidades del proyecto (puntualidad, carga de trabajo, flexibilidad)",
            "Demuestra flexibilidad y motivación hacia la materia del proyecto independientemente de preferencias personales",
        ],
        "Calidad técnica": [
            "Se esfuerza y preocupa por entregar su trabajo con máxima calidad",
            "El trabajo que presenta no necesita ser revisado (más de lo necesario) por un tercero",
            "Adquiere y pone en práctica los conocimientos básicos del proyecto (sector, metodología, digitales)",
            "Adquiere un criterio propio sobre la materia del proyecto o tarea",
            "Maneja las herramientas y programas utilizadas en el día a día",
            "Tiene ojo (auto-)crítico para evaluar que la calidad de un trabajo esté conforme con las necesidades de la tarea",
            "Demuestra solvencia en la parte numérica del proyecto si aplica",
        ],
        "Trabajo en equipo": [
            "Sabe levantar la mano cuando no tiene capacidad para hacer una tarea",
            "Sus compañeros confían en él/ella porque demuestra un ownership de sus tareas",
            "Se muestra disponible para ayudar a otros compañeros cuando lo necesitan",
            "Contribuye proactivamente al buen clima en el equipo",
            "Acepta las dinámicas de trabajo en equipo y contribuye al buen funcionamiento del equipo",
            "Apoya a sus compañeros en aquellos ámbitos en los que puedan necesitar ayuda",
            "Se preocupa de aprender de sus compañeros y de estar al mismo nivel de conocimientos relativos al proyecto",
        ],
        "Comunicación": [
            "Demuestra una comunicación (oral y escrita) efectiva y asertiva",
            "Muestra una buena comunicación no verbal",
            "Comunica de forma efectiva su criterio al resto del equipo",
            "Demuestra capacidad de razonar sobre su criterio y modificarlo si fuese incorrecto o necesario",
        ],
        "Relación con el cliente": [
            "Participa en reuniones con clientes",
            "Entiende las dinámicas con el cliente y el trato que se le debe dar",
        ],
    },

    "Asociado": {
        "Gestión del proyecto": [
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
        "Calidad técnica": [
            "Muestra mediante el ejemplo el nivel de calidad que se ha de cuidar en cada fase del proyecto, sirviendo de guía o referencia",
            "Vela por que el trabajo de los analistas/en prácticas tenga la calidad técnica requerida",
            "Desarrolla la línea de pensamiento y razonamiento numérica necesaria (modelos financieros, magnitudes, economics)",
            "Reta los conceptos numéricos o cualitativos desarrollados para asegurar su rigor",
            "Mantiene el orden en el proyecto — gestión externa, interna y documental",
            "Demuestra madurez en las ideas y tareas en las que trabaja",
            "Aporta un valor fundamental en hipótesis, conclusiones, recomendaciones y presentaciones finales",
        ],
        "Trabajo en equipo": [
            "Está disponible y accesible para atender a los diferentes miembros de su equipo y guiarlos",
            "Guía al equipo con el ejemplo",
            "Se encarga de que el equipo esté al mismo nivel de información y conocimientos, y da apoyo técnico cuando se necesite",
            "Se asegura que los tiempos dedicados por los analistas/becarios en cada tarea sean los adecuados",
        ],
        "Comunicación": [
            "Comunica de forma efectiva las tareas y prioridades a todos los miembros del equipo",
            "Guía y motiva a los miembros del equipo para sacar lo mejor de ellos y mantener un clima de trabajo positivo",
            "Transmite de forma certera las necesidades del proyecto, especialmente cuando requiere un esfuerzo especial",
            "Sabe construir el storytelling y el racional de una idea, explicársela desde cero a un interlocutor y convencerle de que tiene sentido",
        ],
        "Relación con el cliente": [
            "Define y prepara las sesiones de trabajo con el cliente",
            "Logra confianza y credibilidad con los niveles del cliente con los que le corresponde relacionarse",
            "Lidera sesiones de trabajo con el cliente de forma asistida por alguien con más seniority",
            "Lidera sesiones de trabajo con el cliente de forma autónoma",
        ],
    },

    "Asociado Sr": {
        "Gestión del proyecto": [
            "Define el planning de proyecto en profundidad identificando los puntos más complicados",
            "Define el alcance y marco de trabajo del proyecto y lo ajusta de forma continua a la realidad",
            "Identifica nuevas oportunidades para Igeneris que puedan surgir del proyecto (upselling, cross selling)",
            "Es capaz de gestionar un proyecto (estándar y no estándar) entendiendo las necesidades del cliente y ajustando el marco",
            "Se anticipa a posibles riesgos del proyecto y lidera sus posibles planes de contingencia",
        ],
        "Calidad técnica": [
            "Asegura una coherencia a nivel de proyecto en el discurso, el racional, los economics",
            "Identifica de forma rápida las carencias de un proyecto o entregable y sabe subsanarlas eficientemente",
            "Apuesta por ir 'más allá' en los proyectos y traslada al equipo cómo hacerlo",
            "Propone nuevas formas creativas de solucionar problemas",
        ],
        "Trabajo en equipo": [
            "Es un referente para el equipo en cuanto a forma de trabajar y aspiración dentro de Igeneris",
            "Ayuda al equipo a sacar lo mejor de ellos y superar su nivel de calidad, ayudándoles a desarrollarse profesionalmente",
            "Gestiona de forma eficiente la distribución de trabajo del equipo según tiempos y perfiles",
            "Transmite de forma contundente feedback de mejora a los compañeros asegurando que se desarrollen correctamente",
            "Inspira y motiva al equipo a todos los niveles de la pirámide",
            "Está atento a bloqueos y problemas",
        ],
        "Comunicación": [
            "Tiene una alta capacidad de síntesis de los problemas y de exposición tanto internamente como hacia cliente",
            "Comunica de forma clara a todos los niveles de la organización del cliente adaptando el discurso y contenido a cada auditorio",
            "Argumenta con seguridad y convincentemente, siendo capaz de reaccionar a argumentaciones del cliente",
            "Sabe construir el storytelling y el racional de una idea, explicársela desde cero a un interlocutor y convencerle de que tiene sentido",
        ],
        "Relación con el cliente": [
            "Crea un vínculo con el cliente y es capaz de entender sus necesidades para con el proyecto",
            "Lidera los workshops y sesiones de trabajo con el cliente más complicados",
            "Es un referente para el cliente en todos los aspectos que abarca el proyecto e incluso más allá del alcance",
            "Lidera sesiones de trabajo con el cliente de forma autónoma",
        ],
        "Liderazgo": [
            "Desarrollo de talento: realiza seguimiento/feedback durante y post proyectos. Conoce las fortalezas y debilidades de cada miembro y se las comunica asertivamente. Mantiene relación con los career advisors.",
            "Motivación: transmite entusiasmo y no se deja amedrentar por energías externas, genera buen rollo durante todo el proyecto y concilia la vida personal y profesional del equipo.",
            "Inspire Others: ejerce de mentor/apoyo/inspiración para otros compañeros, es aspiracional y el equipo lo tiene como líder en conocimiento y técnica.",
        ],
    },

    "Manager": {
        "Gestión del proyecto": [
            "Define el planning de proyecto en profundidad identificando los puntos más complicados",
            "Define el alcance y marco de trabajo del proyecto y lo ajusta de forma continua a la realidad",
            "Identifica nuevas oportunidades para Igeneris que puedan surgir del proyecto (upselling, cross selling)",
            "Es capaz de gestionar un proyecto (estándar y no estándar) entendiendo las necesidades del cliente y ajustando el marco",
            "Se anticipa a posibles riesgos del proyecto y lidera sus posibles planes de contingencia",
            "Ejerce una buena gestión de los tiempos en la organización del proyecto",
            "Sigue una metodología/sello Igeneris, sumada a una base estratégica",
            "Prevé la organización y los posibles riesgos del proyecto, propone un plan de priorización de tareas",
            "Gestiona y resuelve problemas que surgen a lo largo del proyecto",
        ],
        "Calidad técnica": [
            "Ejerce de referente técnico y estratégico",
            "Se empapa y aterriza el conocimiento sobre la industria en la que se trabaja",
            "Utiliza su experiencia previa en otros proyectos",
            "Garantiza el correcto funcionamiento del equipo y la calidad del entregable a cliente en tiempo y forma",
        ],
        "Trabajo en equipo": [
            "Es capaz de repartir un rol a cada miembro del equipo dentro de las responsabilidades y capacidades reales de cada uno",
            "Es transparente con el equipo",
            "Ejerce con criterio el reparto de tareas en relación con los recursos individuales de cada miembro y el tiempo de inversión",
            "Desarrolla un plan de priorización de tareas donde su equipo pueda entender cuáles son los objetivos y cómo organizarse",
        ],
        "Comunicación": [
            "Transparencia en la comunicación a lo largo del proyecto para que el equipo esté alineado con el cliente/proyecto",
            "Sabe dar una comunicación asertiva al equipo",
            "Sabe comunicar al cliente adaptando el discurso dependiendo de las necesidades del proyecto y de las reacciones potenciales del cliente",
        ],
        "Relación con el cliente": [
            "Mantiene una buena comunicación con el cliente",
            "Sabe preguntar al cliente qué necesita y cuáles son sus expectativas para no ir apagando fuegos posteriormente",
            "Conduce eficazmente las expectativas del cliente, contribuyendo a la satisfacción con el resultado del proyecto",
        ],
        "Liderazgo": [
            "Desarrollo de talento: realiza seguimiento/feedback durante y post proyectos. Conoce las fortalezas y debilidades de cada miembro y se las comunica asertivamente. Mantiene relación con los career advisors.",
            "Motivación: transmite entusiasmo y no se deja amedrentar por energías externas, genera buen rollo durante todo el proyecto y concilia la vida personal y profesional del equipo.",
            "Inspire Others: ejerce de mentor/apoyo/inspiración para otros compañeros, es aspiracional y el equipo lo tiene como líder en conocimiento y técnica.",
        ],
    },
}

# ── Normalización de cargo ─────────────────────────────────────────────────────

_CARGO_ALIAS: dict[str, str] = {
    "analista": "Analista",
    "analyst": "Analista",
    "asociado": "Asociado",
    "associate": "Asociado",
    "asociado sr": "Asociado Sr",
    "associate sr": "Asociado Sr",
    "sr associate": "Asociado Sr",
    "manager": "Manager",
    "director": "Manager",
}


def _cargo_clave(cargo: str) -> str | None:
    return _CARGO_ALIAS.get(cargo.strip().lower())


# ── Prompt ────────────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = (
    "Eres un asistente de evaluación de Igeneris. "
    "A partir de opiniones en texto libre de compañeros, genera un resumen estructurado "
    "por apartados para que el Career Advisor pueda opinar.\n\n"
    "Reglas:\n"
    "- Extrae evidencias concretas del texto para cada apartado\n"
    "- Usa lenguaje neutro y profesional en tercera persona\n"
    "- Si un apartado no tiene información en las evaluaciones, escribe 'Sin datos suficientes'\n"
    "- No inventes información que no esté en el texto\n"
    "- Sé conciso: 2-4 frases por apartado"
)


# ── Conversión de evaluaciones estructuradas a texto ─────────────────────────

def evaluaciones_a_texto(evaluaciones: list[dict]) -> str:
    if not evaluaciones:
        return ""
    lineas = []
    for ev in evaluaciones:
        evaluador = ev.get("persona_que_evalua") or ev.get("nombre") or "Desconocido"
        proyecto = ev.get("proyecto") or "Sin proyecto"
        fecha = (ev.get("fecha") or "")[:10]
        sat = ev.get("satisfaccion", "")
        mejor = ev.get("mejor_aspecto", "")
        peor = ev.get("peor_aspecto", "")
        rel = ev.get("relacion", "")
        nivel = {"superior": "líder", "igual": "igual nivel", "inferior": "subordinado"}.get(rel, "sin nivel")
        lineas.append(
            f"[{fecha}] {evaluador} ({nivel}) — Proyecto: {proyecto} | "
            f"Satisfacción: {sat}/5 | Mejor: {mejor} | A mejorar: {peor}"
        )
    return "\n".join(lineas)


# ── Función principal ─────────────────────────────────────────────────────────

def generar_resumen_evaluacion(nombre: str, cargo: str, evaluaciones_texto: str) -> str:
    """
    Genera un resumen estructurado por apartados de competencias a partir de texto libre.
    Los criterios no se envían a Claude — solo los nombres de los apartados.

    Args:
        nombre: Nombre del advisee.
        cargo: Cargo del advisee (Analista / Asociado / Asociado Sr / Manager, o inglés).
        evaluaciones_texto: Texto libre con las opiniones de los compañeros.

    Returns:
        Texto con el resumen estructurado, listo para guardar en Notion.

    Raises:
        RuntimeError: Si falta ANTHROPIC_API_KEY o el paquete anthropic.
        ValueError: Si el cargo no es reconocido.
    """
    if not anthropic_client:
        raise RuntimeError("Falta ANTHROPIC_API_KEY o el paquete anthropic no está instalado.")

    clave = _cargo_clave(cargo) if cargo else None
    if clave:
        apartados_nombres = list(CRITERIOS[clave].keys())
    else:
        if cargo:
            logging.warning("Cargo '%s' no reconocido en resumen, usando secciones base.", cargo)
        apartados_nombres = [
            "Gestión del proyecto", "Calidad técnica", "Trabajo en equipo",
            "Comunicación", "Relación con el cliente",
        ]

    apartados = "\n".join(f"- {ap}" for ap in apartados_nombres)

    user_prompt = (
        f"Advisee: {nombre} ({clave})\n\n"
        f"Apartados a cubrir:\n{apartados}\n\n"
        f"Evaluaciones de compañeros:\n{evaluaciones_texto}\n\n"
        "Genera el resumen estructurado."
    )

    try:
        respuesta = anthropic_client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1200,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )
        return "".join(b.text for b in respuesta.content if b.type == "text").strip()
    except Exception:
        logging.exception("Error generando resumen de evaluación para '%s'", nombre)
        raise


def generar_resumen_desde_evaluaciones(
    nombre: str, cargo: str, evaluaciones: list[dict]
) -> str:
    """
    Versión conveniente: convierte evaluaciones estructuradas de Notion a texto
    y llama a generar_resumen_evaluacion.

    Args:
        nombre: Nombre del advisee.
        cargo: Cargo del advisee.
        evaluaciones: Lista de dicts devuelta por obtener_evaluaciones_por_evaluado().

    Returns:
        Texto con el resumen estructurado.
    """
    texto = evaluaciones_a_texto(evaluaciones)
    if not texto:
        raise ValueError(f"No hay evaluaciones disponibles para '{nombre}'.")
    return generar_resumen_evaluacion(nombre, cargo, texto)
