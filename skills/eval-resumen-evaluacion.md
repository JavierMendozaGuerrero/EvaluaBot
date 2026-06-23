---
name: eval-resumen-evaluacion
description: >
  Usa este skill SIEMPRE que el usuario quiera generar un resumen estructurado por competencias
  de las evaluaciones de un empleado para que el Career Advisor pueda analizarlas y dar feedback.
  El output es texto libre organizado por apartados según el cargo del advisee.
---

# Skill: Resumen de Evaluación por Competencias

## Qué hace este skill

A partir de las evaluaciones de compañeros (texto libre o estructuradas desde Notion),
genera un **resumen estructurado por apartados de competencias** calibrado según el cargo del advisee.
Está diseñado para que el Career Advisor tenga un punto de partida ordenado antes de escribir su opinión.

**Los criterios de evaluación están hardcodeados en el código y no se envían a Claude** —
solo se mandan los nombres de los apartados. Esto mantiene el prompt corto y el foco en las evidencias reales.

---

## Arquitectura del flujo

```
Notion
  └── "Evaluaciones - {nombre}"  → evaluaciones mensuales (opcional — también acepta texto libre)
          ↓
  evaluaciones_a_texto(evaluaciones)     ← convierte lista de dicts a texto
          ↓
  generar_resumen_evaluacion(nombre, cargo, texto)
  ├── Normaliza cargo (Español / Inglés)
  ├── Obtiene los apartados del cargo desde CRITERIOS
  ├── Construye el prompt (solo nombres de apartados + texto evaluaciones)
  └── Llama a Claude API (claude-sonnet-4-6, max_tokens=1200)
          ↓
  Texto estructurado por apartados
```

**Sin caché** — el resumen se genera siempre al llamarse. Si se necesita persistencia,
guardar el resultado en Notion externamente.

---

## Apartados por cargo

Los criterios están definidos en `CRITERIOS` (dict hardcodeado en el módulo).
Solo los **nombres** de los apartados se incluyen en el prompt de Claude.

| Cargo | Apartados |
|-------|-----------|
| Analista | Gestión del proyecto · Calidad técnica · Trabajo en equipo · Comunicación · Relación con el cliente |
| Asociado | Gestión del proyecto · Calidad técnica · Trabajo en equipo · Comunicación · Relación con el cliente |
| Asociado Sr | Gestión del proyecto · Calidad técnica · Trabajo en equipo · Comunicación · Relación con el cliente · **Liderazgo** |
| Manager | Gestión del proyecto · Calidad técnica · Trabajo en equipo · Comunicación · Relación con el cliente · **Liderazgo** |

> `Liderazgo` solo aparece para **Asociado Sr** y **Manager**.

---

## Criterios de evaluación por cargo

Estos criterios están hardcodeados en el módulo como referencia para el equipo.
**No se envían a Claude**.

### Gestión del proyecto

**Analista**
- Priorizar tareas y repartir de forma adecuada los tiempos
- Entregar su trabajo a tiempo
- Responsabilizarse del buen devenir de sus tareas y subtareas sin necesidad de que se lo recuerden
- Es proactivo, detecta necesidades del proyecto y cómo puede aportar valor antes de que alguien se lo diga
- Demuestra un compromiso alto hacia un resultado excelente del proyecto
- Detecta y avisa de cuellos de botella existentes o posibles problemas intentando aportar soluciones
- Muestra disposición y proactividad para encontrar las herramientas que necesita
- Demuestra compromiso con las necesidades del proyecto (puntualidad, carga de trabajo, flexibilidad)
- Demuestra flexibilidad y motivación hacia la materia del proyecto independientemente de preferencias personales

**Asociado**
- Define y ejecuta con autonomía el plan de trabajo de su área de responsabilidad
- Responsabilizarse del proyecto y sus necesidades (desbloquear problemas, establecer reuniones, puntos de seguimiento)
- Responsabilizarse de los tiempos del proyecto y de la calidad de los entregables
- Identificar las piezas y elementos necesarios para la consecución de un proyecto
- Gestiona adecuadamente y vela por la consecución de todos los elementos necesarios internos
- Distribuye adecuadamente las tareas entre los miembros del equipo según cargas de trabajo y perfiles
- Vela por mantener un ritmo de trabajo apropiado anticipándose a cuellos de botella o picos de trabajo
- Identifica y comunica al responsable del proyecto posibles riesgos y bloqueos
- Se focaliza en lo que es más importante (80/20)

**Asociado Sr**
- Define el planning de proyecto en profundidad identificando los puntos más complicados
- Define el alcance y marco de trabajo del proyecto y lo ajusta de forma continua a la realidad
- Identifica nuevas oportunidades para Igeneris que puedan surgir del proyecto (upselling, cross selling)
- Es capaz de gestionar un proyecto (estándar y no estándar) entendiendo las necesidades del cliente y ajustando el marco
- Se anticipa a posibles riesgos del proyecto y lidera sus posibles planes de contingencia

**Manager**
- Todo lo de Asociado Sr, más:
- Ejerce una buena gestión de los tiempos en la organización del proyecto
- Sigue una metodología/sello Igeneris, sumada a una base estratégica
- Prevé la organización y los posibles riesgos del proyecto, propone un plan de priorización de tareas
- Gestiona y resuelve problemas que surgen a lo largo del proyecto

---

### Calidad técnica

**Analista**
- Se esfuerza y preocupa por entregar su trabajo con máxima calidad
- El trabajo que presenta no necesita ser revisado (más de lo necesario) por un tercero
- Adquiere y pone en práctica los conocimientos básicos del proyecto (sector, metodología, digitales)
- Adquiere un criterio propio sobre la materia del proyecto o tarea
- Maneja las herramientas y programas utilizadas en el día a día
- Tiene ojo (auto-)crítico para evaluar que la calidad de un trabajo esté conforme con las necesidades de la tarea
- Demuestra solvencia en la parte numérica del proyecto si aplica

**Asociado**
- Muestra mediante el ejemplo el nivel de calidad que se ha de cuidar en cada fase del proyecto
- Vela por que el trabajo de los analistas/en prácticas tenga la calidad técnica requerida
- Desarrolla la línea de pensamiento y razonamiento numérica necesaria (modelos financieros, magnitudes, economics)
- Reta los conceptos numéricos o cualitativos desarrollados para asegurar su rigor
- Mantiene el orden en el proyecto — gestión externa, interna y documental
- Demuestra madurez en las ideas y tareas en las que trabaja
- Aporta un valor fundamental en hipótesis, conclusiones, recomendaciones y presentaciones finales

**Asociado Sr**
- Asegura una coherencia a nivel de proyecto en el discurso, el racional, los economics
- Identifica de forma rápida las carencias de un proyecto o entregable y sabe subsanarlas eficientemente
- Apuesta por ir "más allá" en los proyectos y traslada al equipo cómo hacerlo
- Propone nuevas formas creativas de solucionar problemas

**Manager**
- Ejerce de referente técnico y estratégico
- Se empapa y aterriza el conocimiento sobre la industria en la que se trabaja
- Utiliza su experiencia previa en otros proyectos
- Garantiza el correcto funcionamiento del equipo y la calidad del entregable a cliente en tiempo y forma

---

### Trabajo en equipo

**Analista**
- Sabe levantar la mano cuando no tiene capacidad para hacer una tarea
- Sus compañeros confían en él/ella porque demuestra un ownership de sus tareas
- Se muestra disponible para ayudar a otros compañeros cuando lo necesitan
- Contribuye proactivamente al buen clima en el equipo
- Acepta las dinámicas de trabajo en equipo y contribuye al buen funcionamiento del equipo
- Apoya a sus compañeros en aquellos ámbitos en los que puedan necesitar ayuda
- Se preocupa de aprender de sus compañeros y de estar al mismo nivel de conocimientos relativos al proyecto

**Asociado**
- Está disponible y accesible para atender a los diferentes miembros de su equipo y guiarlos
- Guía al equipo con el ejemplo
- Se encarga de que el equipo esté al mismo nivel de información y conocimientos, y da apoyo técnico cuando se necesite
- Se asegura que los tiempos dedicados por los analistas/becarios en cada tarea sean los adecuados

**Asociado Sr**
- Es un referente para el equipo en cuanto a forma de trabajar y aspiración dentro de Igeneris
- Ayuda al equipo a sacar lo mejor de ellos y superar su nivel de calidad, ayudándoles a desarrollarse profesionalmente
- Gestiona de forma eficiente la distribución de trabajo del equipo según tiempos y perfiles
- Transmite de forma contundente feedback de mejora a los compañeros asegurando que se desarrollen correctamente
- Inspira y motiva al equipo a todos los niveles de la pirámide
- Está atento a bloqueos y problemas

**Manager**
- Es capaz de repartir un rol a cada miembro del equipo dentro de las responsabilidades y capacidades reales de cada uno
- Es transparente con el equipo
- Ejerce con criterio el reparto de tareas en relación con los recursos individuales de cada miembro y el tiempo de inversión
- Desarrolla un plan de priorización de tareas donde su equipo pueda entender cuáles son los objetivos y cómo organizarse

---

### Comunicación

**Analista**
- Demuestra una comunicación (oral y escrita) efectiva y asertiva
- Muestra una buena comunicación no verbal
- Comunica de forma efectiva su criterio al resto del equipo
- Demuestra capacidad de razonar sobre su criterio y modificarlo si fuese incorrecto o necesario

**Asociado**
- Comunica de forma efectiva las tareas y prioridades a todos los miembros del equipo
- Guía y motiva a los miembros del equipo para sacar lo mejor de ellos y mantener un clima de trabajo positivo
- Transmite de forma certera las necesidades del proyecto, especialmente cuando requiere un esfuerzo especial
- Sabe construir el storytelling y el racional de una idea, explicársela desde cero a un interlocutor y convencerle de que tiene sentido

**Asociado Sr**
- Tiene una alta capacidad de síntesis de los problemas y de exposición tanto internamente como hacia cliente
- Comunica de forma clara a todos los niveles de la organización del cliente adaptando el discurso y contenido a cada auditorio
- Argumenta con seguridad y convincentemente, siendo capaz de reaccionar a argumentaciones del cliente
- Sabe construir el storytelling y el racional de una idea, explicársela desde cero a un interlocutor y convencerle de que tiene sentido

**Manager**
- Transparencia en la comunicación a lo largo del proyecto para que el equipo esté alineado con el cliente/proyecto
- Sabe dar una comunicación asertiva al equipo
- Sabe comunicar al cliente adaptando el discurso dependiendo de las necesidades del proyecto y de las reacciones potenciales del cliente

---

### Relación con el cliente

**Analista**
- Participa en reuniones con clientes
- Entiende las dinámicas con el cliente y el trato que se le debe dar

**Asociado**
- Define y prepara las sesiones de trabajo con el cliente
- Logra confianza y credibilidad con los niveles del cliente con los que le corresponde relacionarse
- Lidera sesiones de trabajo con el cliente de forma asistida por alguien con más seniority
- Lidera sesiones de trabajo con el cliente de forma autónoma

**Asociado Sr**
- Crea un vínculo con el cliente y es capaz de entender sus necesidades para con el proyecto
- Lidera los workshops y sesiones de trabajo con el cliente más complicados
- Es un referente para el cliente en todos los aspectos que abarca el proyecto e incluso más allá del alcance
- Lidera sesiones de trabajo con el cliente de forma autónoma

**Manager**
- Mantiene una buena comunicación con el cliente
- Sabe preguntar al cliente qué necesita y cuáles son sus expectativas para no ir apagando fuegos posteriormente
- Conduce eficazmente las expectativas del cliente, contribuyendo a la satisfacción con el resultado del proyecto

---

### Liderazgo (solo Asociado Sr y Manager)

- Desarrollo de talento: realiza seguimiento/feedback durante y post proyectos. Conoce las fortalezas y debilidades de cada miembro y se las comunica asertivamente. Mantiene relación con los career advisors.
- Motivación: transmite entusiasmo y no se deja amedrentar por energías externas, genera buen rollo durante todo el proyecto y concilia la vida personal y profesional del equipo.
- Inspire Others: ejerce de mentor/apoyo/inspiración para otros compañeros, es aspiracional y el equipo lo tiene como líder en conocimiento y técnica.

---

## Normalización de cargo

El parámetro `cargo` acepta tanto nombres en español como en inglés:

| Entrada (cualquier formato) | Clave interna |
|-----------------------------|---------------|
| `analista`, `analyst` | `Analista` |
| `asociado`, `associate` | `Asociado` |
| `asociado sr`, `associate sr`, `sr associate` | `Asociado Sr` |
| `manager`, `director` | `Manager` |

---

## Estructura de datos de entrada

### Opción A — texto libre

```python
generar_resumen_evaluacion(
    nombre="Alonso Ballesteros",
    cargo="Asociado",
    evaluaciones_texto="Alonso gestionó bien los tiempos en el proyecto X...",
)
```

### Opción B — lista de dicts de Notion

```python
generar_resumen_desde_evaluaciones(
    nombre="Alonso Ballesteros",
    cargo="Asociado",
    evaluaciones=[
        {
            "persona_que_evalua": "Laura Gómez",
            "relacion": "superior",
            "proyecto": "Proyecto X",
            "fecha": "2025-03-15",
            "satisfaccion": 4,
            "mejor_aspecto": "Gestión de tiempos",
            "peor_aspecto": "Comunicación con el cliente",
        },
        ...
    ],
)
```

`evaluaciones_a_texto()` convierte cada dict al formato:

```
[2025-03-15] Laura Gómez (líder) — Proyecto: Proyecto X | Satisfacción: 4/5 | Mejor: Gestión de tiempos | A mejorar: Comunicación con el cliente
```

---

## Claude API: prompt enviado

```
System:
  Eres un asistente de evaluación de Igeneris. A partir de opiniones en texto libre de compañeros,
  genera un resumen estructurado por apartados para que el Career Advisor pueda opinar.
  Reglas:
  - Extrae evidencias concretas del texto para cada apartado
  - Usa lenguaje neutro y profesional en tercera persona
  - Si un apartado no tiene información en las evaluaciones, escribe "Sin datos suficientes"
  - No inventes información que no esté en el texto
  - Sé conciso: 2-4 frases por apartado

User:
  Advisee: {nombre} ({cargo_normalizado})

  Apartados a cubrir:
  - Gestión del proyecto
  - Calidad técnica
  - Trabajo en equipo
  - Comunicación
  - Relación con el cliente
  [- Liderazgo]   ← solo si Asociado Sr o Manager

  Evaluaciones de compañeros:
  {evaluaciones_texto}

  Genera el resumen estructurado.
```

**Parámetros de llamada**: `model="claude-sonnet-4-6"`, `max_tokens=1200`

**Output**: texto libre estructurado — Claude elige el formato (habitualmente encabezados + párrafos). No hay JSON.

---

## Funciones del módulo

| Función | Qué hace |
|---------|----------|
| `generar_resumen_evaluacion(nombre, cargo, evaluaciones_texto)` | Función principal. Llama a Claude y devuelve el resumen como string. |
| `generar_resumen_desde_evaluaciones(nombre, cargo, evaluaciones)` | Versión conveniente. Convierte lista de dicts y llama a la función principal. |
| `evaluaciones_a_texto(evaluaciones)` | Helper. Convierte lista de dicts de Notion a texto línea a línea. |
| `_cargo_clave(cargo)` | Normaliza el cargo a la clave interna (`Analista` / `Asociado` / `Asociado Sr` / `Manager`). |

---

## Dependencias

```python
# Requeridas
anthropic   # generar_resumen_evaluacion()

# Del propio proyecto
from .clients import anthropic_client
```

---

## Errores frecuentes

| Error | Causa | Solución |
|-------|-------|----------|
| `RuntimeError: Falta ANTHROPIC_API_KEY` | API key no configurada | Configurar `ANTHROPIC_API_KEY` en `.env` + `pip install anthropic` |
| `ValueError: Cargo 'X' no reconocido` | El cargo no existe en `_CARGO_ALIAS` | Usar uno de los valores de la tabla de normalización |
| `ValueError: No hay evaluaciones disponibles` | Lista de dicts vacía al usar `generar_resumen_desde_evaluaciones` | Verificar que el empleado tiene evaluaciones en Notion |
