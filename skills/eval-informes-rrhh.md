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
**No requiere ninguna base de Notion adicional** — usa las bases ya existentes.

### Las 5 fuentes (cada una con su prefijo de cita)

| Fuente | Cita | Lectura | Notas |
|--------|------|---------|-------|
| Opiniones del CA | `[O#]` | `obtener_opiniones_ca_por_advisee` | Notas del CA + resúmenes del chatbot (campos `opinion` y `resumen_advisee`) |
| Evaluaciones mensuales | `[E#]` | `obtener_evaluaciones_por_evaluado` | Con jerarquía líder/equipo/sin nivel |
| Evaluaciones de proyecto | `[P#]` | `obtener_evaluaciones_proyecto_por_evaluado` (`project_evals.py`) | Todas las recibidas, de todos los proyectos |
| Seguimiento personal | `[S#]` | `obtener_comentarios_personales` | Comentarios personales con autor y fecha |
| Barbecho | `[B#]` | `obtener_barbecho_por_empleado` (`notion_service.py`) | Labores sin proyecto → **casi siempre `contribution_to_firm`** |

`Objetivos empleados` (`obtener_objetivos_persona`) sigue revelando el nombre del CA y alimenta la sección de objetivos.

### Evolución temporal

El contexto va **en orden cronológico** y cada línea lleva su **mes** (`[E1] [feb 25]`). El prompt
instruye a Claude a describir la **trayectoria, no la media** ("febrero ≠ noviembre"): más peso a lo
reciente, citar ambos momentos cuando algo mejora/empeora, y distinguir entre proyectos. La ponderación
la razona Claude (el código solo ordena y etiqueta), para no imponer un sesgo ciego.

---

## Arquitectura del flujo

```
Notion
  ├── "Evaluaciones - {nombre}"  → evaluaciones mensuales (por proyecto, evaluador, relación jerárquica)
  ├── "Opiniones - {nombre}"     → opiniones del CA (resumen + opinión por fecha)
  └── "Objetivos empleados"      → objetivos + nombre del CA
          ↓
  obtener_datos_empleado_anual()          ← recopila las 5 fuentes
  ├── obtener_evaluaciones_por_evaluado(nombre)          [E#]
  ├── obtener_opiniones_ca_por_advisee(ca, nombre)       [O#]
  ├── obtener_evaluaciones_proyecto_por_evaluado(nombre) [P#]
  ├── obtener_comentarios_personales(nombre)             [S#]
  ├── obtener_barbecho_por_empleado(nombre)              [B#]
  └── obtener_objetivos_persona(nombre)   ← objetivos + nombre del CA
          ↓
  interpretar_evaluaciones_anual()        ← Claude API
  ├── _formatear_contexto() → (texto, fuentes)   ← cada dato lleva un id citable [E3]/[O1]
  ├── Claude devuelve JSON con bullets, cada uno citando su fuente [E3][E7]
  ├── _validar_citas()      ← descarta por código bullets sin cita válida (anti-invención)
  └── _verificar_soporte()  ← 2ª llamada (auditor): marca afirmaciones no respaldadas (avisa)
          ↓
  guardar_informe_anual_word()            → informe_anual_{slug}.docx   (citas = hyperlinks a Notion)
  guardar_informe_anual_html()            → informe_anual_{slug}.html   (citas clicables + panel de revisión)
  _escribir_cache()                       → informe_anual_{slug}_cache.json
```

### Trazabilidad y control (anti-invención)

El sistema garantiza de forma **estructural** (no solo por prompt) que el informe no contenga
afirmaciones inventadas, y deja siempre la última palabra al CA:

1. **IDs citables**: cada evaluación/opinión del contexto se etiqueta `[E3]`/`[O1]` y se mapea a su
   `url` de Notion (`fuentes`). Las evaluaciones y opiniones arrastran ahora `page_id` + `url`
   desde `notion_service`.
2. **Citas obligatorias**: el prompt exige que cada bullet termine con la(s) etiqueta(s) de la(s)
   que proviene. `temperature=0` para imparcialidad/determinismo.
3. **Validación por código** (`_validar_citas`): un bullet sin cita válida **se elimina**; las citas
   a ids inexistentes se borran. Es un `if`, no confianza en el modelo → la invención no puede colar.
   Lo eliminado se registra en `comentarios["_bullets_descartados"]`.
4. **Pasada de verificación** (`_verificar_soporte`): una 2ª llamada audita si cada cita *realmente*
   respalda la afirmación. Política: **avisar, no borrar** → los hallazgos van a
   `comentarios["_avisos_verificacion"]`; el CA decide.
5. **Citas clicables a evidencia interna (sin Notion)**: como el CA/advisee no tiene acceso a Notion,
   cada cita enlaza a un **anexo "Fuentes / Evidencia"** dentro del propio informe que muestra el dato
   en bruto (tipo, proyecto, evaluador, fecha, texto). En HTML es un ancla `#fuente-E3` (con resaltado
   al saltar); en Word es un enlace interno a un **bookmark** del anexo. Todo autocontenido y offline.
6. **Panel de revisión** (solo en el borrador HTML): lista avisos + bullets descartados para que el
   CA los revise antes de publicar. No aparece en el `.docx`.

### Estado borrador → publicado (ya existente)

- `informe_anual_{slug}.*` = **borrador**: solo lo ven el CA y admin ([api_server.py] `servir_archivo_protegido`).
- `informe_final_{slug}_{ts}.*` = **publicado**: el CA lo sube editado vía `/api/subir-informe-final`;
  lo ve el advisee solo cuando el CA activa el acceso. El control del CA es por diseño.

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

> ⚠️ **Los criterios se cargan en vivo desde Notion** (`obtener_criterios_evaluacion(grupo)`), así que
> pueden cambiar según lo que haya en Notion. La lista de abajo y el diccionario `_CRITERIOS_DTI` del
> código son solo **fallback/referencia** (grupo "Negocio") cuando Notion no devuelve criterios.
> Los criterios **solo calibran** el feedback: **no** son fuentes citables — las citas `[E#]/[O#]`
> siempre apuntan a evaluaciones/opiniones, nunca a un criterio.

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

### Evaluaciones mensuales (`obtener_evaluaciones_por_evaluado`)

```python
{
    "proyecto":           "Nombre del proyecto",
    "persona_que_evalua": "Nombre del evaluador",   # o campo "nombre"
    "relacion":           "superior" | "igual" | "inferior" | "",
    "fecha":              "2025-03-15",
    "satisfaccion":       4,                         # sobre 5
    "mejor_aspecto":      "Texto libre",
    "peor_aspecto":       "Texto libre",
    "page_id":            "notion-page-id",          # para trazabilidad
    "url":                "https://notion.so/...",   # destino de la cita clicable
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

Claude recibe el contexto formateado (cada dato precedido de su id `[E3]`/`[O1]`) y devuelve
**JSON puro** (sin bloques markdown). **Cada bullet debe terminar con su(s) cita(s)**:

```json
{
  "gestion_proyecto": {
    "lider":     "entrega su trabajo a tiempo [E3]\nse responsabiliza de sus tareas [E7]",
    "equipo":    "ayuda a sus compañeros [E4][E5]",
    "sin_nivel": "bullet de evaluaciones sin jerarquía [E9]"
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

## Diseño del documento Word — réplica de la plantilla oficial PDF

El `.docx` reproduce la plantilla "EVALUACIÓN ANUAL" de IGENERIS. Los campos que el sistema no
tiene (NOTA por dimensión, CA '26, salarios, % variable, promoción, deadlines) se dejan **en blanco**
para que el CA los rellene, igual que en la plantilla impresa.

- **Márgenes**: 1.76 cm. **Fuente**: Arial (la marca usa otra tipografía; sin el `.ttf` se usa Arial).
- **Año evaluado** = `año_generación − 1` (p. ej. genera en 2026 → evalúa 2025). HTML y Word usan el mismo criterio.
- **Marca + título**: `.Igeneris` (22pt bold, centrado) + `EVALUACIÓN ANUAL` (13pt bold subrayado).
- **Tabla datos (4 col)**: `Empleado | … | Fecha | …` · `CA '25 | … | Posición actual | …` · `CA '26 | (blanco) | Salario actual | (blanco)`.
- **`CALIFICACIÓN {año}`** → tabla 3 col: **`PROYECTOS | NOTA | COMENTARIOS`**.
  - PROYECTOS 1.6" · NOTA 0.6" (en blanco) · COMENTARIOS el resto (bullets de Claude con citas).
  - Dimensiones con la etiqueta del PDF (`_DIMS_PDF`); Liderazgo se añade si el cargo lo requiere.
- **Notas finales / retribución** (tabla 4 col): `Nota final Proyectos / Contrib. To the firm (10%) / Consecución Objetivos corp.` + columna `Variable (60/30%)` + `Total Variable '25 =`.
- **`RESULTADO EVAL '25`** (tabla 5 col): `PROMOCIÓN | _ | POSICIÓN '26 | _ | Nuevo salario fijo =`.
- **`OPORTUNIDADES DE MEJORA / OBJETIVOS '26`**: tabla 2 col con cabecera `Deadline`; filas numeradas 1-2-3 (prerrellena con objetivos de Notion si los hay).

> Las celdas NOTA y de retribución se dejan vacías a propósito (las completa el CA). Las citas `[E#]`
> clicables **sí** se mantienen en el Word (es el borrador de trabajo del CA).

---

## Caché

La caché evita rellamar a Claude y regenerar si los datos no han cambiado.

```python
# Huella SHA-256 de: versión + las 5 fuentes + cargo + criterios (Notion)
huella = hashlib.sha256(
    json.dumps({"v": 4, "opiniones": ..., "evaluaciones": ..., "evals_proyecto": ...,
                "seguimiento": ..., "barbecho": ..., "cargo": ..., "criterios": ...},
               sort_keys=True).encode()
).hexdigest()

# Archivos
informe_anual_{slug}_cache.json   # {"huella": "abc123..."}
```

Si `huella == cache["huella"]` y existen `.docx` y `.html` → se reutilizan.
Si no → se regenera todo y se actualiza la caché.

> **Nota (v4)**: la huella incluye las **5 fuentes** (`opiniones_ca`, `evaluaciones`, `evals_proyecto`,
> `seguimiento`, `barbecho`), **`cargo`** y **`criterios`** (el texto DTI renderizado desde Notion, que
> varía por grupo Negocio/MiddleOffice/Palantir). Por tanto, cambiar cualquier fuente, el cargo o los
> criterios **regenera el informe automáticamente**. El texto de criterios se computa una sola vez en
> `generar_informe_anual` y se reutiliza para la huella y el prompt. Solo cambios en **objetivos** siguen sin invalidar.

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

**Lanza `ValueError`** si no hay ni opiniones del CA ni evaluaciones mensuales para el empleado.

---

## Funciones del módulo

| Función | Qué hace |
|---------|----------|
| `obtener_empleados_evaluacion_anual()` | Lista empleados con base "Evaluaciones - {nombre}" en Notion |
| `obtener_datos_empleado_anual(nombre)` | Recopila las **5 fuentes** + objetivos desde Notion |
| `_mes_tag(fecha)` | `'2025-03-15' → 'mar 25'` para etiquetar la evolución temporal |
| `_formatear_contexto(emp_data)` | Devuelve `(texto, fuentes)`: contexto cronológico con ids `[E/O/P/S/B#]` + mes, y mapa id→{url,label,texto} |
| `_filtrar_bullets_citados(texto, fuentes, descartados)` | Conserva solo bullets con ≥1 cita válida; limpia citas inexistentes |
| `_validar_citas(comentarios, fuentes)` | Aplica el filtro a todas las dimensiones; registra `_bullets_descartados` |
| `_recolectar_afirmaciones(comentarios)` | Extrae cada bullet con sus citas para auditarlo |
| `_verificar_soporte(comentarios, fuentes)` | 2ª llamada (auditor): devuelve avisos de afirmaciones no respaldadas |
| `interpretar_evaluaciones_anual(emp_data, cargo)` | Orquesta: Claude → validación → verificación; adjunta `_fuentes` y avisos |
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
| `_dx_hyperlink(para, url, texto, ...)` | Inserta un hyperlink real de Word (azul, subrayado) |
| `_dxr_con_citas(para, texto, fuentes, ...)` | Renderiza texto convirtiendo los tokens `[E3]` en hyperlinks a Notion |
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
| Bullet sin cita desaparece del informe | `_validar_citas` lo descartó por no citar fuente | Es el comportamiento esperado (anti-invención); aparece en el panel de revisión |
| Caché no se invalida al cambiar objetivos | La huella no incluye objetivos | Borrar manualmente `informe_anual_{slug}_cache.json` (cargo y criterios sí se invalidan en v3) |
