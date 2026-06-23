---
name: eval-informes-rrhh
description: >
  Usa este skill SIEMPRE que el usuario quiera generar, crear o automatizar informes de evaluación
  anual de empleados en formato Word (.docx) cogiendo datos de Notion. El output es un .docx y un .html
  por empleado, con caché automática si los datos no han cambiado.
---

# Skill: Generador de Informes de Evaluación Anual (DOCX + HTML)

## Qué hace este skill

Genera automáticamente informes de evaluación anual en `.docx` (Word) y `.html` por empleado.
**No requiere ninguna base de Notion adicional** — usa las bases ya existentes:

| Base de Notion | Qué contiene |
|----------------|-------------|
| `Evaluaciones - {nombre}` | Evaluaciones de proyecto del empleado |
| `Opiniones - {nombre}` | Opiniones del CA sobre el advisee |
| `Objetivos empleados` | Objetivos del empleado (también revela el nombre del CA) |

---

## Arquitectura del flujo

```
Notion
  ├── "Evaluaciones - {nombre}"  → evaluaciones de proyecto (por proyecto, evaluador, relación jerárquica)
  ├── "Opiniones - {nombre}"     → opiniones del CA (resumen + opinión por fecha)
  └── "Objetivos empleados"      → objetivos + nombre del CA
          ↓
  obtener_datos_empleado_anual()
  ├── obtener_evaluaciones_por_evaluado(nombre)
  ├── obtener_objetivos(nombre)           ← también extrae el nombre del CA
  ├── obtener_ca_de_empleado(nombre)      ← fallback si objetivos no lo tiene
  └── obtener_opiniones_ca_por_advisee(ca, nombre)
          ↓
  interpretar_evaluaciones_anual()        ← Claude API
  ├── Formatea contexto: opiniones CA + evaluaciones agrupadas por jerarquía
  └── Devuelve JSON con bullets por dimensión
          ↓
  guardar_informe_anual_word()            → informe_anual_{slug}.docx
  guardar_informe_anual_html()            → informe_anual_{slug}.html
  _escribir_cache()                       → informe_anual_{slug}_cache.json
```

**Caché automática**: si los datos de Notion no han cambiado (misma huella SHA-256),
se reutilizan los archivos ya generados sin llamar a Claude ni regenerar el documento.

---

## KPIs y dimensiones por cargo

```python
_REQUIERE_LIDERAZGO = {"sr associate", "manager", "director"}
```

| Cargo | Secciones en el informe |
|-------|------------------------|
| Analyst / Associate / Associate Sr | Proyectos + Contribution to the Firm |
| Sr Associate / Manager / Director | Proyectos + **Liderazgo** + Contribution to the Firm |

### Dimensiones de Proyectos (siempre)

| Clave JSON | Etiqueta en documento |
|------------|----------------------|
| `gestion_proyecto` | Gestión del proyecto |
| `calidad_tecnica` | Calidad técnica |
| `trabajo_en_equipo` | Trabajo en equipo |
| `comunicacion` | Comunicación |
| `relacion_cliente` | Relación con el cliente |

### Dimensiones de Liderazgo (solo Sr Associate en adelante)

| Clave JSON | Etiqueta en documento |
|------------|----------------------|
| `liderazgo_desarrollo_talento` | Desarrollo de Talento |
| `liderazgo_motivacion` | Motivación |
| `liderazgo_referente` | Referente |

---

## Criterios de evaluación por cargo — DTI

Claude usa estos criterios para contextualizar el feedback según el cargo del empleado.
Lo que es positivo para un Analyst puede ser lo mínimo esperado para un Manager.

### Gestión del proyecto

**Analyst**
- Priorizar tareas y repartir de forma adecuada los tiempos
- Entregar su trabajo a tiempo
- Responsabilizarse del buen devenir de sus tareas y subtareas sin necesidad de que se lo recuerden
- Es proactivo, detecta necesidades del proyecto y cómo puede aportar valor antes de que alguien se lo diga
- Demuestra un compromiso alto hacia un resultado excelente del proyecto
- Detecta y avisa de cuellos de botella o posibles problemas intentando aportar soluciones
- Muestra disposición y proactividad para encontrar las herramientas que necesita
- Demuestra compromiso con las necesidades del proyecto (puntualidad, carga de trabajo, flexibilidad)
- Demuestra flexibilidad y motivación hacia la materia del proyecto independientemente de preferencias personales

**Associate**
- Define y ejecuta con autonomía el plan de trabajo de su área de responsabilidad
- Responsabilizarse del proyecto y sus necesidades (desbloquear problemas, establecer reuniones, puntos de seguimiento)
- Responsabilizarse de los tiempos del proyecto y de la calidad de los entregables
- Identificar las piezas y elementos necesarios para la consecución de un proyecto (herramientas, workshops, sesiones, discusiones internas, con cliente, etc.)
- Gestiona adecuadamente y vela por la consecución de todos los elementos necesarios internos
- Distribuye adecuadamente las tareas entre los miembros del equipo según cargas de trabajo y perfiles
- Vela por mantener un ritmo de trabajo apropiado anticipándose a cuellos de botella o picos de trabajo
- Identifica y comunica al responsable del proyecto posibles riesgos y bloqueos
- Se focaliza en lo que es más importante (80/20)

**Associate Sr**
- Define el planning de proyecto en profundidad identificando los puntos más complicados
- Define el alcance y marco de trabajo del proyecto y lo ajusta de forma continua a la realidad
- Identifica nuevas oportunidades para Igeneris que puedan surgir del proyecto (upselling, cross selling)
- Es capaz de gestionar un proyecto (estándar y no estándar) entendiendo las necesidades del cliente y ajustando el marco
- Se anticipa a posibles riesgos del proyecto y lidera sus posibles planes de contingencia

**Manager**
- Todo lo de Associate Sr, más:
- Ejerce una buena gestión de los tiempos en la organización del proyecto
- Sigue una metodología/sello Igeneris, sumada a una base estratégica
- Prevé la organización y los posibles riesgos del proyecto, propone un plan de priorización de tareas
- Gestiona y resuelve problemas que surgen a lo largo del proyecto

---

### Calidad técnica

**Analyst**
- Se esfuerza y preocupa por entregar su trabajo con máxima calidad
- El trabajo que presenta no necesita ser revisado (más de lo necesario) por un tercero
- Adquiere y pone en práctica los conocimientos básicos del proyecto (sector, metodología, digitales)
- Adquiere un criterio propio sobre la materia del proyecto o tarea
- Maneja las herramientas y programas utilizadas en el día a día
- Tiene ojo (auto-)crítico para evaluar que la calidad de un trabajo esté conforme con las necesidades de la tarea
- Demuestra solvencia en la parte numérica del proyecto si aplica

**Associate**
- Muestra mediante el ejemplo el nivel de calidad que se ha de cuidar en cada fase del proyecto, sirviendo de guía o referencia
- Vela por que el trabajo de los analistas/en prácticas tenga la calidad técnica requerida
- Desarrolla la línea de pensamiento y razonamiento numérica necesaria (modelos financieros, magnitudes, economics)
- Reta los conceptos numéricos o cualitativos desarrollados para asegurar su rigor
- Mantiene el orden en el proyecto — gestión externa, interna y documental, asegurando que la información esté disponible y sea útil
- Demuestra madurez en las ideas y tareas en las que trabaja
- Aporta un valor fundamental en hipótesis, conclusiones, recomendaciones y presentaciones finales

**Associate Sr**
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

**Analyst**
- Sabe levantar la mano cuando no tiene capacidad para hacer una tarea
- Sus compañeros confían en él porque demuestra un ownership de sus tareas
- Se muestra disponible para ayudar a otros compañeros cuando lo necesitan
- Contribuye proactivamente al buen clima en el equipo
- Acepta las dinámicas de trabajo en equipo y contribuye al buen funcionamiento del equipo
- Apoya a sus compañeros en aquellos ámbitos en los que puedan necesitar ayuda
- Se preocupa de aprender de sus compañeros y estar al mismo nivel de conocimientos relativos al proyecto

**Associate**
- Está disponible y accesible para atender a los diferentes miembros de su equipo y guiarlos
- Guía al equipo con el ejemplo
- Se encarga de que el equipo esté al mismo nivel de información y conocimientos, y da apoyo técnico cuando se necesite
- Se asegura que los tiempos dedicados por los analistas/becarios en cada tarea sean los adecuados

**Associate Sr**
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

**Analyst**
- Demuestra una comunicación (oral y escrita) efectiva y asertiva
- Muestra una buena comunicación no verbal
- Comunica de forma efectiva su criterio al resto del equipo
- Demuestra capacidad de razonar sobre su criterio y modificarlo si fuese incorrecto o necesario

**Associate**
- Comunica de forma efectiva las tareas y prioridades a todos los miembros del equipo
- Guía y motiva a los miembros del equipo para sacar lo mejor de ellos y mantener un clima de trabajo positivo
- Transmite de forma certera las necesidades del proyecto, especialmente cuando requiere un esfuerzo especial
- Sabe construir el storytelling y el racional de una idea, explicársela desde cero a un interlocutor y convencerle de que tiene sentido

**Associate Sr**
- Tiene una alta capacidad de síntesis de los problemas y de exposición tanto internamente como hacia cliente
- Comunica de forma clara a todos los niveles de la organización del cliente adaptando el discurso y contenido a cada auditorio
- Argumenta con seguridad y convincentemente, siendo capaz de reaccionar a argumentaciones del cliente

**Manager**
- Transparencia en la comunicación a lo largo del proyecto para que el equipo esté alineado con el cliente/proyecto
- Sabe dar una comunicación asertiva al equipo
- Sabe comunicar al cliente adaptando el discurso dependiendo de las necesidades del proyecto y de las reacciones potenciales del cliente

---

### Relación con el cliente

**Analyst**
- Participa en reuniones con clientes
- Entiende las dinámicas con el cliente y el trato que se le debe dar

**Associate**
- Define y prepara las sesiones de trabajo con el cliente
- Logra confianza y credibilidad con los niveles del cliente con los que le corresponde relacionarse, transmitiendo seguridad y profesionalidad
- Lidera sesiones de trabajo con el cliente de forma asistida por alguien con más seniority
- Lidera sesiones de trabajo con el cliente de forma autónoma

**Associate Sr**
- Crea un vínculo con el cliente y es capaz de entender sus necesidades para con el proyecto
- Lidera los workshops y sesiones de trabajo con el cliente más complicados / coordina y supervisa que las reuniones estén bien pensadas y ejecutadas
- Es un referente para el cliente en todos los aspectos que abarca el proyecto e incluso más allá del alcance del mismo

**Manager**
- Mantiene una buena comunicación con el cliente
- Sabe preguntar al cliente qué necesita y cuáles son sus expectativas para no ir apagando fuegos posteriormente
- Conduce eficazmente las expectativas del cliente, contribuyendo a la satisfacción con el resultado del proyecto

---

### Liderazgo (solo Sr Associate / Manager / Director)

**Desarrollo de Talento**
- Realiza un seguimiento y feedback durante y post proyectos de todos los miembros de su equipo
- Conoce las fortalezas y debilidades de cada miembro del equipo y se las comunica asertivamente para que pueda seguir evolucionando
- Mantiene relación con los Career Advisors de los miembros de su equipo

**Motivación**
- Es capaz de transmitir entusiasmo y no dejarse amedrentar por energías externas
- Genera buen rollo durante todo el proyecto
- Es capaz de conciliar la vida personal y profesional de todos los miembros de su equipo (requiere previsión en proyectos)

**Referente (Inspire Others)**
- Ejerce de mentor, apoyo e inspiración para otros compañeros del equipo
- Es aspiracional y el equipo lo tiene como líder en conocimiento y técnica

---

## Estructura de datos que devuelve Notion

### Evaluaciones de proyecto (`obtener_evaluaciones_por_evaluado`)

```python
{
    "proyecto":           "Nombre del proyecto",
    "persona_que_evalua": "Nombre del evaluador",   # o campo "nombre"
    "relacion":           "superior" | "igual" | "inferior" | "",
    "fecha":              "2025-03-15",
    "satisfaccion":       4,                         # sobre 5
    "mejor_aspecto":      "Texto libre",
    "peor_aspecto":       "Texto libre",
}
```

Las evaluaciones se agrupan por jerarquía al formatear el contexto para Claude:
- `superior` → "Evaluaciones del líder"
- `igual` / `inferior` → "Evaluaciones de miembros del equipo"
- `""` → "Evaluaciones sin nivel especificado" (datos anteriores al sistema)

### Opiniones del CA (`obtener_opiniones_ca_por_advisee`)

```python
{
    "fecha":           "2025-06-01",
    "resumen_advisee": "Resumen de evaluaciones del período",
    "opinion":         "Opinión directa del CA",
}
```

### Objetivos (`obtener_objetivos`)

```python
{
    "ca":        "Nombre del CA",
    "fecha":     "2025-01-10",
    "objetivos": "Texto con los objetivos del empleado",
}
```

---

## Claude API: formato de respuesta esperado

Claude recibe el contexto formateado y devuelve **JSON puro** (sin bloques markdown):

```json
{
  "gestion_proyecto": {
    "lider":     "bullet sobre lo que dice su superior\nbullet 2",
    "equipo":    "bullet sobre lo que dicen iguales/subordinados",
    "sin_nivel": "bullet de evaluaciones sin jerarquía especificada"
  },
  "calidad_tecnica":   { ... },
  "trabajo_en_equipo": { ... },
  "comunicacion":      { ... },
  "relacion_cliente":  { ... },
  "liderazgo_desarrollo_talento": { ... },
  "liderazgo_motivacion":         { ... },
  "liderazgo_referente":          { ... },
  "contribution_to_firm": "bullets planos sobre contribución a la empresa",
  "resultado": "valoración global en 2-3 frases"
}
```

> `contribution_to_firm` y `resultado` son **strings planos**, no objetos con niveles.
> Las claves `lider`, `equipo`, `sin_nivel` son opcionales — Claude las omite si no hay datos.
> Las dimensiones de Liderazgo solo se incluyen si el cargo lo requiere.

### Agrupación por nivel en el documento

Dentro de cada celda de comentarios, los bullets se agrupan con etiqueta de nivel:

```
Líder:
• bullet del superior

Miembros de tu equipo:
• bullet de igual o subordinado

Sin nivel especificado:
• bullet sin jerarquía
```

---

## Diseño del documento Word

- **Márgenes**: 1.76 cm todos los lados
- **Fuente**: Arial, 9pt por defecto
- **Cabecera**: `IGENERIS — EVALUACIÓN ANUAL {año}` centrado, 14pt bold
- **Tabla datos empleado**: 2 columnas (etiqueta | valor), bordes simples
- **Tablas de dimensiones**: 3 columnas:
  - Dimensión: 3.50 pulgadas
  - Nota: 0.60 pulgadas (centrada, siempre `X`)
  - Comentarios: resto del ancho (~2.78 pulgadas)
- **Tabla Resultado**: 2 columnas — nota global (`X / 5`) | texto de valoración
- **Sección Objetivos**: texto con bullets, con metadatos (CA que los definió + fecha)

### Anchos de columna

```python
_CONTENT_W_IN = 9906 / 1440   # ~6.88 pulgadas (A4 con márgenes 1.76 cm)
_W_DIM  = 3.50
_W_NOTA = 0.60
_W_COM  = _CONTENT_W_IN - _W_DIM - _W_NOTA   # ~2.78
```

---

## Caché

La caché evita rellamar a Claude y regenerar si los datos no han cambiado.

```python
# Huella SHA-256 de: versión + opiniones_ca + evaluaciones
huella = hashlib.sha256(
    json.dumps({"v": 2, "opiniones": ..., "evaluaciones": ...}, sort_keys=True).encode()
).hexdigest()

# Archivos
informe_anual_{slug}_cache.json   # {"huella": "abc123..."}
```

Si `huella == cache["huella"]` y existen `.docx` y `.html` → se reutilizan.
Si no → se regenera todo y se actualiza la caché.

> **Nota**: la caché solo cubre `opiniones_ca` y `evaluaciones`. Cambios en objetivos o cargo
> no la invalidan — hay que borrar el `.json` de caché manualmente si se cambian esos campos.

---

## Punto de entrada principal

```python
slug = generar_informe_anual(evaluado="Alonso Ballesteros", cargo="Analyst")
```

**Parámetros:**
- `evaluado` (str): nombre del empleado, debe coincidir con las bases de Notion
- `cargo` (str): cargo actual. Determina si incluye Liderazgo y qué criterios aplican. Puede estar vacío.

**Devuelve:** `slug` (str) — nombre de archivo sin extensión:
- `informe_anual_{slug}.docx`
- `informe_anual_{slug}.html`

**Lanza `ValueError`** si no hay ni opiniones del CA ni evaluaciones de proyecto para el empleado.

---

## Funciones del módulo

| Función | Qué hace |
|---------|----------|
| `obtener_empleados_evaluacion_anual()` | Lista empleados con base "Evaluaciones - {nombre}" en Notion |
| `obtener_datos_empleado_anual(nombre)` | Recopila evaluaciones, opiniones CA y objetivos desde Notion |
| `_formatear_contexto(emp_data)` | Formatea los datos en texto estructurado para el prompt de Claude, agrupando por jerarquía |
| `interpretar_evaluaciones_anual(emp_data, cargo)` | Llama a Claude API y parsea el JSON de respuesta |
| `guardar_informe_anual_word(emp_data, comentarios, cargo)` | Genera el `.docx` con python-docx |
| `guardar_informe_anual_html(emp_data, comentarios, cargo)` | Genera el `.html` con estilos IGENERIS |
| `generar_informe_anual(evaluado, cargo)` | Punto de entrada. Orquesta todo el flujo con caché |

---

## Helpers internos Word

| Helper | Uso |
|--------|-----|
| `_dxb(cell)` | Aplica bordes simples negros a una celda |
| `_dxw(cell, inches)` | Establece el ancho de una celda en pulgadas |
| `_dxr(para, texto, ...)` | Añade un run de texto con estilo a un párrafo |
| `_dxt(doc, texto)` | Añade un título de sección (bold, underline, 10pt) |
| `_dx_bullets(cell, texto)` | Renderiza bullets planos en una celda |
| `_dx_bullets_por_nivel(cell, contenido)` | Renderiza bullets agrupados por nivel jerárquico |
| `_tabla_dims(doc, dims, comentarios)` | Genera tabla de 3 columnas para un grupo de dimensiones |

---

## Dependencias

```python
# Requeridas
python-docx      # guardar_informe_anual_word()
anthropic        # interpretar_evaluaciones_anual()

# Del propio proyecto
from .clients import Document, anthropic_client
from .notion_service import (
    listar_bbdd_evaluados,
    obtener_ca_de_empleado,
    obtener_evaluaciones_por_evaluado,
    obtener_opiniones_ca_por_advisee,
    obtener_objetivos,
)
from .utils import slug_archivo
from . import config                 # config.CARPETA_WEB, config.IGENERIS_CSS
```

---

## Errores frecuentes

| Error | Causa | Solución |
|-------|-------|----------|
| `ValueError: No hay opiniones...` | El empleado no tiene datos en Notion | Verificar que existen "Evaluaciones - {nombre}" y "Opiniones - {nombre}" |
| `RuntimeError: Falta ANTHROPIC_API_KEY` | API key no configurada o paquete no instalado | `pip install anthropic` + configurar `ANTHROPIC_API_KEY` |
| `RuntimeError: Instala python-docx` | python-docx no instalado | `pip install python-docx` |
| Claude devuelve bloques markdown | El modelo ignoró la instrucción | El código ya limpia ` ```json ` antes de parsear |
| Caché no se invalida al cambiar cargo | La huella no incluye el cargo | Borrar manualmente `informe_anual_{slug}_cache.json` |
