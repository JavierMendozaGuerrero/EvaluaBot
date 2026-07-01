# Informes auxiliares y sesiones

Documentación función por función de los módulos del backend encargados de generar informes,
sesiones de evaluación anual asistida y las skills auxiliares (opiniones del CA, resumen de
evaluación y PDFs de fuentes en bruto).

---

## `backend/reports.py` — Informe mensual con Claude + caché (HTML/Word) y trayectoria

**Propósito:** Generar el informe mensual de una persona evaluada a partir de sus evaluaciones y
comentarios personales de Notion, redactado por Claude, cacheando el resultado para no volver a
llamar a Claude si los datos no han cambiado. También genera una "trayectoria" interactiva (SPA en
un único HTML autónomo) navegable por fecha/proyecto/satisfacción.

**Entradas/Salidas (archivos generados, formatos):**
- Entrada: evaluaciones y comentarios personales leídos de Notion vía `notion_service`.
- Salidas en `config.CARPETA_WEB`:
  - `informe_{slug}.html` — informe con estilo Igeneris.
  - `informe_{slug}.docx` — informe en Word (requiere `python-docx`).
  - `informe_{slug}_cache.json` — caché con huella SHA-256, total y fecha de generación.
  - `trayectoria_{slug}.html` — HTML autónomo con JS embebido para navegar las evaluaciones.
- Todos se sirven por la web/API.

### `_evaluaciones_para_prompt(evaluaciones)`
- **Qué hace:** Aplana la lista de evaluaciones a texto en líneas (una por evaluación) para incluirlo en el prompt de Claude. [reports.py:19](../backend/reports.py#L19)
- **Parámetros:** `evaluaciones` — list[dict] — evaluaciones con claves `evaluado`, `persona_que_evalua`/`nombre`, `relacion`, `proyecto`, `q1`, `q2`, `fecha`.
- **Devuelve:** str — líneas unidas por saltos de línea.
- **Efectos (Claude/disco/Notion):** Ninguno; solo formatea texto. Usa `_NIVEL_LABEL` para traducir la relación (superior/igual/inferior).
- **Se llama desde:** `generar_informe_claude` [reports.py:59](../backend/reports.py#L59).
- **Notas:** Función interna (prefijo `_`).

### `_comentarios_para_prompt(comentarios, nombre)`
- **Qué hace:** Aplana los comentarios personales a texto en líneas para el prompt. [reports.py:34](../backend/reports.py#L34)
- **Parámetros:** `comentarios` — list[dict] — con claves `autor`, `fecha`, `comentario`; `nombre` — str — nombre del evaluado (no se usa dentro salvo firma).
- **Devuelve:** str — líneas unidas, o `""` si no hay comentarios.
- **Efectos:** Ninguno; solo formatea.
- **Se llama desde:** `generar_informe_claude` [reports.py:52](../backend/reports.py#L52).
- **Notas:** Función interna. El parámetro `nombre` se recibe pero no se emplea en el cuerpo.

### `generar_informe_claude(evaluaciones, comentarios_personales=None, idioma="es")`
- **Qué hace:** Construye el prompt (plantilla i18n `report.prompt` + evaluaciones + sección opcional de comentarios personales) y llama a Claude para redactar el informe. [reports.py:43](../backend/reports.py#L43)
- **Parámetros:** `evaluaciones` — list[dict] — evaluaciones; `comentarios_personales` — list[dict]|None — reflexiones/menciones; `idioma` — str — idioma para la plantilla del prompt.
- **Devuelve:** str — texto del informe redactado por Claude.
- **Efectos (Claude/disco/Notion):** **Llama a Claude** (`anthropic_client.messages.create`, modelo `claude-sonnet-4-6`, `max_tokens=2200`). No escribe disco ni Notion.
- **Se llama desde:** `generar_archivos_informe` [reports.py:161](../backend/reports.py#L161).
- **Notas:** Lanza `RuntimeError` si falta `ANTHROPIC_API_KEY`/paquete o si no hay evaluaciones.

### `_ruta_cache_informe(slug)`
- **Qué hace:** Devuelve la ruta del JSON de caché del informe. [reports.py:70](../backend/reports.py#L70)
- **Parámetros:** `slug` — str — nombre base de archivo.
- **Devuelve:** str — `{CARPETA_WEB}/informe_{slug}_cache.json`.
- **Efectos:** Ninguno.
- **Se llama desde:** `cargar_cache_informe` [reports.py:80](../backend/reports.py#L80), `guardar_cache_informe` [reports.py:89](../backend/reports.py#L89).
- **Notas:** Función interna.

### `_huella_evaluaciones(evaluaciones)`
- **Qué hace:** Calcula la huella SHA-256 de las evaluaciones serializadas (JSON con claves ordenadas) para detectar cambios. [reports.py:74](../backend/reports.py#L74)
- **Parámetros:** `evaluaciones` — list — datos a hashear (en `generar_archivos_informe` incluye evaluaciones + comentarios + marcador de idioma).
- **Devuelve:** str — hash hexadecimal.
- **Efectos:** Ninguno.
- **Se llama desde:** `generar_archivos_informe` [reports.py:154](../backend/reports.py#L154).
- **Notas:** Función interna.

### `cargar_cache_informe(slug)`
- **Qué hace:** Lee el JSON de caché del informe si existe. [reports.py:79](../backend/reports.py#L79)
- **Parámetros:** `slug` — str — nombre base.
- **Devuelve:** dict|None — contenido del caché, o `None` si no existe el archivo.
- **Efectos (disco):** Lee del disco.
- **Se llama desde:** `generar_archivos_informe` [reports.py:155](../backend/reports.py#L155).
- **Notas:** —

### `guardar_cache_informe(slug, huella, total)`
- **Qué hace:** Escribe el JSON de caché con la huella, el total de evaluaciones y la fecha de generación UTC. [reports.py:87](../backend/reports.py#L87)
- **Parámetros:** `slug` — str — nombre base; `huella` — str — hash; `total` — int — nº de evaluaciones.
- **Devuelve:** None.
- **Efectos (disco):** Crea `CARPETA_WEB` si falta y escribe el JSON.
- **Se llama desde:** `generar_archivos_informe` [reports.py:164](../backend/reports.py#L164).
- **Notas:** —

### `guardar_informe_html(informe, evaluaciones, evaluado, idioma="es")`
- **Qué hace:** Renderiza el informe a un HTML con estilo Igeneris (cabecera, métricas de resumen y cuerpo escapado). [reports.py:93](../backend/reports.py#L93)
- **Parámetros:** `informe` — str — texto de Claude; `evaluaciones` — list — para contar (`len`); `evaluado` — str — nombre; `idioma` — str — para plantillas i18n y `lang`.
- **Devuelve:** str — ruta del HTML generado.
- **Efectos (disco):** Escribe `informe_{slug}.html` en `CARPETA_WEB`.
- **Se llama desde:** `generar_archivos_informe` [reports.py:162](../backend/reports.py#L162).
- **Notas:** Escapa cada línea con `html.escape` y usa `<br>` para separar. Usa `config.IGENERIS_CSS`. La variable `app_url` se asigna pero no se usa en la plantilla.

### `guardar_informe_word(informe, evaluaciones, evaluado, idioma="es")`
- **Qué hace:** Genera el informe en Word (`.docx`): título, metadatos i18n y párrafos; las líneas que empiezan por `N.` se convierten en encabezados de nivel 2. [reports.py:123](../backend/reports.py#L123)
- **Parámetros:** `informe` — str — texto; `evaluaciones` — list — para contar; `evaluado` — str — nombre; `idioma` — str — plantillas.
- **Devuelve:** str — ruta del `.docx`.
- **Efectos (disco):** Escribe `informe_{slug}.docx` en `CARPETA_WEB`.
- **Se llama desde:** `generar_archivos_informe` [reports.py:163](../backend/reports.py#L163).
- **Notas:** Lanza `RuntimeError` si `Document` (python-docx) no está disponible. Divide por `\n\n` en bloques.

### `generar_archivos_informe(evaluado="")`
- **Qué hace:** Orquesta la generación del informe: lee datos de Notion, calcula huella, reutiliza caché si es válida (HTML+DOCX presentes) o llama a Claude y regenera HTML/DOCX + caché. [reports.py:146](../backend/reports.py#L146)
- **Parámetros:** `evaluado` — str — nombre de la persona evaluada (obligatorio).
- **Devuelve:** tuple `(len(evaluaciones): int, slug: str, desde_cache: bool)`.
- **Efectos (Claude/disco/Notion):** **Lee Notion** (`obtener_evaluaciones_por_evaluado`, `obtener_comentarios_personales`, `idioma_de_persona`). Si no hay caché válida, **llama a Claude** y escribe HTML/DOCX/caché.
- **Se llama desde:** `web_server.py` [web_server.py:169](../backend/web_server.py#L169) y `api_server.py` [api_server.py:626](../backend/api_server.py#L626).
- **Notas:** Lanza `RuntimeError` si `evaluado` está vacío. La huella incluye un marcador `{"__idioma__": idioma}` para invalidar la caché si cambia el idioma. Loguea cuando reutiliza caché.

### `guardar_trayectoria_react(evaluaciones, evaluado)`
- **Qué hace:** Genera un HTML autónomo (con JS embebido, sin React real pese al nombre) que agrupa las evaluaciones por persona y permite navegarlas por fecha con controles anterior/siguiente y "pills" por persona. [reports.py:168](../backend/reports.py#L168)
- **Parámetros:** `evaluaciones` — list[dict] — evaluaciones a incrustar; `evaluado` — str — nombre (para el slug del archivo).
- **Devuelve:** str — ruta del HTML.
- **Efectos (disco):** Escribe `trayectoria_{slug}.html` en `CARPETA_WEB`.
- **Se llama desde:** `generar_archivo_trayectoria` [reports.py:315](../backend/reports.py#L315).
- **Notas:** Serializa las evaluaciones a JSON e inyecta escapando `&`, `<`, `>` como secuencias unicode. El JS del cliente agrupa, ordena por fecha y renderiza. Lanza `RuntimeError` si no hay evaluaciones. `app_url` se asigna pero no se usa.

### `generar_archivo_trayectoria(evaluado="")`
- **Qué hace:** Punto de entrada de la trayectoria: lee evaluaciones de Notion y genera el HTML. [reports.py:310](../backend/reports.py#L310)
- **Parámetros:** `evaluado` — str — nombre (obligatorio).
- **Devuelve:** tuple `(len(evaluaciones): int, slug: str)`.
- **Efectos (Notion/disco):** **Lee Notion** (`obtener_evaluaciones_por_evaluado`) y escribe el HTML.
- **Se llama desde:** `web_server.py` [web_server.py:174](../backend/web_server.py#L174) y `api_server.py` [api_server.py:689](../backend/api_server.py#L689).
- **Notas:** Lanza `RuntimeError` si `evaluado` está vacío. No usa Claude ni caché.

---

## `backend/eval_anual_sesion.py` — Sesión interactiva de evaluación anual asistida

**Propósito:** Gestionar un flujo conversacional por áreas para co-redactar la evaluación anual entre
el Career Advisor (CA) y Claude. Por cada área se muestra la evidencia que Claude citó, el CA aporta
sus puntos, Claude reacciona y propone bullets, y el CA confirma el texto final. Al finalizar se genera
el borrador con lo acordado y se registra un log de auditoría en Notion.

**Entradas/Salidas (archivos generados, formatos):**
- Persistencia: JSON local `sesion_anual_{slug}.json` en `config.CARPETA_WEB` (estado completo de la sesión: identidad, áreas, conversaciones, propuestas, textos finales, comentarios cacheados de Claude).
- Salidas finales (vía `skill_informes_anual`): informe anual en Word y HTML.
- Log de auditoría en Notion (best-effort) al finalizar.
- Depende de `skill_informes_anual` (importado como `sk`) para dimensiones, formateo de contexto, generación de comentarios de Claude y guardado de los informes anuales.

### `_secciones(cargo)`
- **Qué hace:** Devuelve la lista ordenada de áreas por las que pasa el CA: dimensiones de proyecto + (liderazgo si el cargo lo requiere) + `contribution_to_firm` + `resultado`. [eval_anual_sesion.py:31](../backend/eval_anual_sesion.py#L31)
- **Parámetros:** `cargo` — str — cargo del advisee; se compara en minúsculas contra `sk._REQUIERE_LIDERAZGO`.
- **Devuelve:** list[tuple[str, str]] — pares `(clave, etiqueta)`.
- **Efectos:** Ninguno.
- **Se llama desde:** `_resumen_estado`, `obtener_area`, `responder_area`, `finalizar_sesion` (dentro del módulo).
- **Notas:** Reutiliza `sk._DIMS_PROYECTOS` y `sk._DIMS_LIDERAZGO`.

### `_ruta_sesion(slug)`
- **Qué hace:** Ruta del JSON de sesión. [eval_anual_sesion.py:43](../backend/eval_anual_sesion.py#L43)
- **Parámetros:** `slug` — str — nombre base.
- **Devuelve:** str — `{CARPETA_WEB}/sesion_anual_{slug}.json`.
- **Efectos:** Ninguno.
- **Se llama desde:** `_leer`, `_guardar`.
- **Notas:** Función interna.

### `_leer(slug)`
- **Qué hace:** Carga la sesión desde el JSON local. [eval_anual_sesion.py:47](../backend/eval_anual_sesion.py#L47)
- **Parámetros:** `slug` — str — nombre base.
- **Devuelve:** dict|None — sesión, o `None` si no existe o falla la lectura.
- **Efectos (disco):** Lee del disco; captura excepciones y las loguea.
- **Se llama desde:** Casi todas las funciones públicas del módulo.
- **Notas:** —

### `_guardar(slug, data)`
- **Qué hace:** Persiste la sesión, actualizando `actualizada_en`. [eval_anual_sesion.py:59](../backend/eval_anual_sesion.py#L59)
- **Parámetros:** `slug` — str — nombre base; `data` — dict — sesión completa.
- **Devuelve:** None.
- **Efectos (disco):** Crea `CARPETA_WEB` y escribe el JSON (sin indentación, `ensure_ascii=False`).
- **Se llama desde:** Todas las funciones que mutan la sesión.
- **Notas:** —

### `_ahora()`
- **Qué hace:** Timestamp ISO en UTC. [eval_anual_sesion.py:66](../backend/eval_anual_sesion.py#L66)
- **Parámetros:** ninguno.
- **Devuelve:** str — ISO 8601 UTC.
- **Efectos:** Ninguno.
- **Se llama desde:** `_guardar`, `iniciar_sesion`, `finalizar_sesion`, `_entradas_log`.
- **Notas:** —

### `_proyectos_de(emp_data)`
- **Qué hace:** Extrae la lista de proyectos únicos (preservando el orden y sin duplicar por minúsculas) de las evaluaciones y evals de proyecto. [eval_anual_sesion.py:72](../backend/eval_anual_sesion.py#L72)
- **Parámetros:** `emp_data` — dict — datos del empleado con `evaluaciones` y `evals_proyecto`.
- **Devuelve:** list[str] — nombres de proyecto.
- **Efectos:** Ninguno.
- **Se llama desde:** `_resumen_estado` [eval_anual_sesion.py:179](../backend/eval_anual_sesion.py#L179).
- **Notas:** Función interna.

### `_claude_texto(comentarios, clave)`
- **Qué hace:** Aplana lo que redactó Claude para una sección a texto legible; si el valor es un dict por niveles, concatena etiquetas + texto usando `sk._LABELS_NIVEL`. [eval_anual_sesion.py:81](../backend/eval_anual_sesion.py#L81)
- **Parámetros:** `comentarios` — dict — comentarios de Claude por área; `clave` — str — clave del área.
- **Devuelve:** str — texto plano de la valoración de Claude para esa área.
- **Efectos:** Ninguno.
- **Se llama desde:** `responder_area` [eval_anual_sesion.py:274](../backend/eval_anual_sesion.py#L274).
- **Notas:** Función interna.

### `_emp_y_fuentes(sesion)`
- **Qué hace:** Devuelve `emp_data` y el diccionario de `fuentes` (evidencia citable) formateados por `sk._formatear_contexto`. [eval_anual_sesion.py:94](../backend/eval_anual_sesion.py#L94)
- **Parámetros:** `sesion` — dict — sesión con `emp_data`.
- **Devuelve:** tuple `(emp_data: dict, fuentes: dict)`.
- **Efectos:** Ninguno (delega en `sk`).
- **Se llama desde:** `obtener_area`, `responder_area`, `finalizar_sesion`.
- **Notas:** Función interna.

### `_asegurar_comentarios(slug, sesion)`
- **Qué hace:** Genera (una sola vez) la valoración de Claude para todas las áreas y la cachea en la sesión. [eval_anual_sesion.py:100](../backend/eval_anual_sesion.py#L100)
- **Parámetros:** `slug` — str — nombre base; `sesion` — dict — sesión.
- **Devuelve:** dict — comentarios de Claude por área.
- **Efectos (Claude/disco):** Si no hay caché, **llama a Claude** vía `sk.interpretar_evaluaciones_anual` y **guarda** la sesión.
- **Se llama desde:** `obtener_area` [eval_anual_sesion.py:206](../backend/eval_anual_sesion.py#L206), `responder_area` [eval_anual_sesion.py:272](../backend/eval_anual_sesion.py#L272).
- **Notas:** Función interna; garantiza que Claude solo se invoque una vez por sesión para la interpretación inicial.

### `_evidencia_de_area(comentarios, fuentes, clave)`
- **Qué hace:** Determina la evidencia que Claude consideró para un área = las fuentes citadas en sus bullets (extrae los IDs con `sk._CITE_RE` y los mapea a `fuentes`), ordenadas por fecha. [eval_anual_sesion.py:110](../backend/eval_anual_sesion.py#L110)
- **Parámetros:** `comentarios` — dict — comentarios de Claude; `fuentes` — dict — fuentes por id (`cid`); `clave` — str — área.
- **Devuelve:** list[dict] — items con `cid`, `tipo`, `label`, `evaluador`, `texto`, `fecha`.
- **Efectos:** Ninguno.
- **Se llama desde:** `obtener_area` [eval_anual_sesion.py:212](../backend/eval_anual_sesion.py#L212), `responder_area` [eval_anual_sesion.py:275](../backend/eval_anual_sesion.py#L275).
- **Notas:** Deduplica por `cid`.

### `_pregunta_area(etiqueta)`
- **Qué hace:** Construye la pregunta abierta que se muestra al CA para un área. [eval_anual_sesion.py:131](../backend/eval_anual_sesion.py#L131)
- **Parámetros:** `etiqueta` — str — etiqueta del área.
- **Devuelve:** str — texto de la pregunta.
- **Efectos:** Ninguno.
- **Se llama desde:** `obtener_area` [eval_anual_sesion.py:214](../backend/eval_anual_sesion.py#L214).
- **Notas:** Función interna.

### `iniciar_sesion(advisee, cargo="")`
- **Qué hace:** Crea o recupera la sesión del advisee. Si no existe, obtiene los datos del empleado, valida que haya datos y guarda el estado inicial. NO invoca a Claude todavía. [eval_anual_sesion.py:138](../backend/eval_anual_sesion.py#L138)
- **Parámetros:** `advisee` — str — nombre; `cargo` — str — cargo (opcional; se actualiza si cambia).
- **Devuelve:** dict — resumen de estado (`_resumen_estado`): identidad, secciones, progreso, proyectos.
- **Efectos (Notion/disco):** **Lee Notion** vía `sk.obtener_datos_empleado_anual`. Escribe el JSON de sesión.
- **Se llama desde:** `api_server.py` [api_server.py:726](../backend/api_server.py#L726).
- **Notas:** `anio` se fija al año actual menos 1. Lanza `ValueError` si no hay ningún dato de evaluación (opiniones, evaluaciones, evals de proyecto, seguimiento, barbecho).

### `_resumen_estado(sesion)`
- **Qué hace:** Construye el resumen de estado de la sesión para el frontend (identidad, secciones con su etiqueta y estado confirmado, totales y contadores). [eval_anual_sesion.py:168](../backend/eval_anual_sesion.py#L168)
- **Parámetros:** `sesion` — dict — sesión.
- **Devuelve:** dict — con `advisee`, `ca`, `cargo`, `anio`, `estado`, `identidadConfirmada`, `proyectos`, `secciones`, `totalSecciones`, `seccionesConfirmadas`.
- **Efectos:** Ninguno.
- **Se llama desde:** `iniciar_sesion`, `confirmar_area`, `estado_sesion`.
- **Notas:** Función interna; usa camelCase en las claves de salida (contrato con el frontend).

### `confirmar_identidad(advisee)`
- **Qué hace:** Marca la identidad como confirmada (el CA confirma que evalúa a esa persona). [eval_anual_sesion.py:187](../backend/eval_anual_sesion.py#L187)
- **Parámetros:** `advisee` — str — nombre.
- **Devuelve:** dict — `{"ok": True}`.
- **Efectos (disco):** Guarda la sesión.
- **Se llama desde:** `api_server.py` [api_server.py:729](../backend/api_server.py#L729).
- **Notas:** Lanza `ValueError` si no hay sesión iniciada.

### `obtener_area(advisee, clave)`
- **Qué hace:** Devuelve los datos de un área: evidencia que Claude consideró, pregunta abierta, y el estado de la conversación/propuesta/confirmación. [eval_anual_sesion.py:197](../backend/eval_anual_sesion.py#L197)
- **Parámetros:** `advisee` — str — nombre; `clave` — str — clave del área.
- **Devuelve:** dict — `clave`, `etiqueta`, `evidencia`, `pregunta`, `conversacion`, `propuesta`, `confirmada`.
- **Efectos (Claude/disco):** Puede **llamar a Claude** la primera vez (vía `_asegurar_comentarios`) y guardar la sesión.
- **Se llama desde:** `api_server.py` [api_server.py:431](../backend/api_server.py#L431).
- **Notas:** Lanza `ValueError` si no hay sesión o si la sección es desconocida.

### `_claude_conversa_area(etiqueta, evidencia, claude_bullets, conversacion)`
- **Qué hace:** Llama a Claude para que reaccione conversacionalmente a los puntos del CA y proponga los bullets del área, exigiendo que cada afirmación lleve su cita. Devuelve `mensaje` + `propuesta`. [eval_anual_sesion.py:220](../backend/eval_anual_sesion.py#L220)
- **Parámetros:** `etiqueta` — str — área; `evidencia` — list — fuentes citables; `claude_bullets` — str — valoración inicial de Claude; `conversacion` — list — historial de mensajes CA/IA.
- **Devuelve:** dict — `{"mensaje": str, "propuesta": str}`.
- **Efectos (Claude):** **Llama a Claude** (`claude-sonnet-4-6`, `max_tokens=1500`, `temperature=0`, con system prompt de director de RRHH). Espera un JSON de respuesta; limpia fences ```` ```json ````.
- **Se llama desde:** `responder_area` [eval_anual_sesion.py:281](../backend/eval_anual_sesion.py#L281).
- **Notas:** Si no hay cliente Anthropic, devuelve un mensaje de fallback con `claude_bullets` como propuesta. Ante error de parseo/red, loguea y devuelve un mensaje de disculpa.

### `responder_area(advisee, clave, texto)`
- **Qué hace:** Añade los puntos del CA a la conversación del área, llama a Claude para responder y guarda la conversación + propuesta actualizada. [eval_anual_sesion.py:260](../backend/eval_anual_sesion.py#L260)
- **Parámetros:** `advisee` — str — nombre; `clave` — str — área; `texto` — str — puntos del CA (obligatorio).
- **Devuelve:** dict — `mensaje`, `propuesta`, `conversacion`.
- **Efectos (Claude/disco):** Puede **llamar a Claude** (interpretación inicial + conversación) y guarda la sesión.
- **Se llama desde:** `api_server.py` [api_server.py:732](../backend/api_server.py#L732).
- **Notas:** Lanza `ValueError` si no hay sesión, sección desconocida, o texto vacío.

### `confirmar_area(advisee, clave)`
- **Qué hace:** Cierra un área fijando `texto_final = propuesta` acordada y marcándola como confirmada. [eval_anual_sesion.py:288](../backend/eval_anual_sesion.py#L288)
- **Parámetros:** `advisee` — str — nombre; `clave` — str — área.
- **Devuelve:** dict — resumen de estado actualizado.
- **Efectos (disco):** Guarda la sesión.
- **Se llama desde:** `api_server.py` [api_server.py:736](../backend/api_server.py#L736).
- **Notas:** Lanza `ValueError` si no hay sesión o si el área aún no tiene conversación (hay que conversar antes de confirmar).

### `estado_sesion(advisee)`
- **Qué hace:** Devuelve el resumen de estado actual de la sesión. [eval_anual_sesion.py:303](../backend/eval_anual_sesion.py#L303)
- **Parámetros:** `advisee` — str — nombre.
- **Devuelve:** dict — resumen de estado.
- **Efectos:** Solo lectura de disco.
- **Se llama desde:** `api_server.py` [api_server.py:427](../backend/api_server.py#L427).
- **Notas:** Lanza `ValueError` si no hay sesión.

### `finalizar_sesion(advisee)`
- **Qué hace:** Exige que todas las áreas estén confirmadas; genera el informe anual (Word + HTML) con los textos finales acordados y marca la sesión como completada; registra el log de auditoría en Notion. [eval_anual_sesion.py:311](../backend/eval_anual_sesion.py#L311)
- **Parámetros:** `advisee` — str — nombre.
- **Devuelve:** dict — `{"ok": True, "estado": "completada"}`.
- **Efectos (disco/Notion):** Genera informes vía `sk.guardar_informe_anual_word` y `sk.guardar_informe_anual_html`; guarda la sesión; **escribe log en Notion** (`guardar_log_evaluacion_anual`, best-effort con try/except).
- **Se llama desde:** `api_server.py` [api_server.py:740](../backend/api_server.py#L740).
- **Notas:** Lanza `ValueError` si faltan áreas por confirmar (listándolas). Construye `comentarios_final` con `_fuentes`, `_avisos_verificacion` y `_bullets_descartados` vacíos + los textos finales por clave.

### `_entradas_log(sesion, secciones)`
- **Qué hace:** Construye las entradas del log de auditoría por área (mensajes del CA, propuesta de Claude, texto final, elección y flags). [eval_anual_sesion.py:347](../backend/eval_anual_sesion.py#L347)
- **Parámetros:** `sesion` — dict — sesión; `secciones` — list[tuple] — áreas.
- **Devuelve:** list[dict] — entradas con `clave`, `etiqueta`, `caTexto`, `claudeTexto`, `eleccion`, `textoFinal`, `divergencia`, `en`.
- **Efectos:** Ninguno.
- **Se llama desde:** `finalizar_sesion` [eval_anual_sesion.py:340](../backend/eval_anual_sesion.py#L340).
- **Notas:** Función interna. `eleccion` siempre `"acordado"` y `divergencia` siempre `False` en la implementación actual.

---

## `backend/skill_opiniones_ca.py` — Genera PDF/HTML de opiniones del CA

**Propósito:** Para un advisee concreto, generar un documento (PDF con reportlab + HTML con
IGENERIS_CSS) con las opiniones que su Career Advisor ha ido dejando a lo largo del tiempo. No usa
Claude. Usa la base "Opiniones - {advisee}" existente (Seguimiento CA).

**Entradas/Salidas (archivos generados, formatos):**
- Entrada: opiniones del CA leídas de Notion (`obtener_opiniones_ca_por_advisee`, `obtener_ca_de_empleado`).
- Salidas en `config.CARPETA_WEB`, servidas por `/api/files/<archivo>`:
  - `opiniones_ca_{slug}.pdf` — PDF de marca Igeneris (reportlab).
  - `opiniones_ca_{slug}.html` — HTML con estilo Igeneris.
  - `opiniones_ca_{slug}_cache.json` — caché con huella SHA-256.
- Cada opinión se reparte en dos bloques: filas CON Resumen (entradas cronológicas: opinión CA + resumen sobre el que opinó) y filas SIN Resumen (sección final "Comentarios y notas extra").

### `_registrar_fuentes()`
- **Qué hace:** Registra las fuentes Outfit (ExtraLight/Regular/Medium) en reportlab si los TTF existen; si no, cae a Helvetica. Cachea el resultado. [skill_opiniones_ca.py:59](../backend/skill_opiniones_ca.py#L59)
- **Parámetros:** ninguno.
- **Devuelve:** dict — `{"light", "regular", "medium"}` con los nombres de fuente a usar.
- **Efectos:** Registra fuentes en reportlab (estado global de la librería). Loguea si falla.
- **Se llama desde:** `generar_pdf_opiniones_ca` [skill_opiniones_ca.py:193](../backend/skill_opiniones_ca.py#L193); también reutilizada por `skill_pdfs_fuentes.py` [skill_pdfs_fuentes.py:29](../backend/skill_pdfs_fuentes.py#L29).
- **Notas:** Usa la variable global `_FONTS_REGISTRADAS` como memoización.

### `_formatear_fecha(iso)`
- **Qué hace:** Convierte una fecha ISO a formato español corto (`'12 ene 2024'`). Si no parsea, devuelve los primeros 10 chars. [skill_opiniones_ca.py:88](../backend/skill_opiniones_ca.py#L88)
- **Parámetros:** `iso` — str — fecha ISO (puede terminar en `Z`).
- **Devuelve:** str — fecha formateada, o `"—"` si vacía.
- **Efectos:** Ninguno.
- **Se llama desde:** `obtener_datos_opiniones_ca` [skill_opiniones_ca.py:125](../backend/skill_opiniones_ca.py#L125).
- **Notas:** Usa la lista `_MESES`.

### `obtener_datos_opiniones_ca(advisee, ca_nombre="")`
- **Qué hace:** Recopila las opiniones del CA sobre el advisee, las ordena de más antigua a más nueva y las separa en `entries` (con resumen) y `comentarios_sueltos` (sin resumen). [skill_opiniones_ca.py:99](../backend/skill_opiniones_ca.py#L99)
- **Parámetros:** `advisee` — str — nombre; `ca_nombre` — str — CA (opcional; si falta se resuelve desde Notion).
- **Devuelve:** dict — `{"advisee", "ca", "entries", "comentarios_sueltos"}`.
- **Efectos (Notion):** **Lee Notion** (`obtener_ca_de_empleado` si hace falta, `obtener_opiniones_ca_por_advisee`).
- **Se llama desde:** `generar_resumen_opiniones_ca` [skill_opiniones_ca.py:479](../backend/skill_opiniones_ca.py#L479).
- **Notas:** —

### `_canvas_maker(advisee_name, ca_name, font="Helvetica")`
- **Qué hace:** Devuelve una subclase de `Canvas` (`IGCanvas`) que pinta cabecera (nombre advisee + `CA · nombre`) y pie (numeración `p / total`) en todas las páginas salvo la primera. [skill_opiniones_ca.py:143](../backend/skill_opiniones_ca.py#L143)
- **Parámetros:** `advisee_name` — str; `ca_name` — str; `font` — str — nombre de fuente para la cabecera.
- **Devuelve:** class — subclase de `pdfcanvas.Canvas`.
- **Efectos:** Ninguno directo (la clase se usa al construir el PDF).
- **Se llama desde:** `generar_pdf_opiniones_ca` [skill_opiniones_ca.py:322](../backend/skill_opiniones_ca.py#L322) como `canvasmaker`.
- **Notas:** Acumula el estado de las páginas para poder pintar el total en `save`.

### `generar_pdf_opiniones_ca(datos)`
- **Qué hace:** Construye el PDF (reportlab) con portada (nombre + logo opcional + CA + fecha), entradas cronológicas en dos columnas (opinión CA | sobre qué opinó) y la sección final de comentarios sueltos. [skill_opiniones_ca.py:183](../backend/skill_opiniones_ca.py#L183)
- **Parámetros:** `datos` — dict — salida de `obtener_datos_opiniones_ca`.
- **Devuelve:** str — ruta del PDF (`opiniones_ca_{slug}.pdf`).
- **Efectos (disco):** Escribe el PDF en `CARPETA_WEB`. Incrusta el logo si `_LOGO_PATH` existe.
- **Se llama desde:** `generar_resumen_opiniones_ca` [skill_opiniones_ca.py:497](../backend/skill_opiniones_ca.py#L497).
- **Notas:** Lanza `RuntimeError` si falta reportlab. Usa `KeepTogether` para no partir bloques y `PageBreak` antes de los comentarios sueltos. Si no hay ni entradas ni comentarios, escribe un placeholder.

### `generar_html_opiniones_ca(datos)`
- **Qué hace:** Genera el HTML (estilo Igeneris) con la misma estructura: cabecera, entradas en dos columnas y sección de comentarios extra. [skill_opiniones_ca.py:329](../backend/skill_opiniones_ca.py#L329)
- **Parámetros:** `datos` — dict — salida de `obtener_datos_opiniones_ca`.
- **Devuelve:** str — ruta del HTML (`opiniones_ca_{slug}.html`).
- **Efectos (disco):** Escribe el HTML en `CARPETA_WEB`.
- **Se llama desde:** `generar_resumen_opiniones_ca` [skill_opiniones_ca.py:496](../backend/skill_opiniones_ca.py#L496).
- **Notas:** Carga las fuentes Outfit desde Google Fonts y sobreescribe tokens de `IGENERIS_CSS`.

### `_huella_datos(datos)`
- **Qué hace:** Calcula la huella SHA-256 de los datos (versión, ca, entries, comentarios) para la caché. [skill_opiniones_ca.py:432](../backend/skill_opiniones_ca.py#L432)
- **Parámetros:** `datos` — dict — datos de opiniones.
- **Devuelve:** str — hash hexadecimal.
- **Efectos:** Ninguno.
- **Se llama desde:** `generar_resumen_opiniones_ca` [skill_opiniones_ca.py:484](../backend/skill_opiniones_ca.py#L484).
- **Notas:** Incluye `"v": 1` como versión del formato.

### `_ruta_cache(slug)`
- **Qué hace:** Ruta del JSON de caché de opiniones. [skill_opiniones_ca.py:444](../backend/skill_opiniones_ca.py#L444)
- **Parámetros:** `slug` — str — nombre base.
- **Devuelve:** str — `opiniones_ca_{slug}_cache.json`.
- **Efectos:** Ninguno.
- **Se llama desde:** `_leer_cache`, `_escribir_cache`.
- **Notas:** Función interna.

### `_leer_cache(slug)`
- **Qué hace:** Lee el JSON de caché si existe. [skill_opiniones_ca.py:448](../backend/skill_opiniones_ca.py#L448)
- **Parámetros:** `slug` — str — nombre base.
- **Devuelve:** dict|None.
- **Efectos (disco):** Lee del disco; captura excepciones devolviendo `None`.
- **Se llama desde:** `generar_resumen_opiniones_ca` [skill_opiniones_ca.py:487](../backend/skill_opiniones_ca.py#L487).
- **Notas:** —

### `_escribir_cache(slug, huella)`
- **Qué hace:** Escribe el JSON de caché con la huella. [skill_opiniones_ca.py:459](../backend/skill_opiniones_ca.py#L459)
- **Parámetros:** `slug` — str — nombre base; `huella` — str — hash.
- **Devuelve:** None.
- **Efectos (disco):** Crea `CARPETA_WEB` y escribe el JSON.
- **Se llama desde:** `generar_resumen_opiniones_ca` [skill_opiniones_ca.py:498](../backend/skill_opiniones_ca.py#L498).
- **Notas:** —

### `generar_resumen_opiniones_ca(advisee, ca_nombre="")`
- **Qué hace:** Punto de entrada: lee las opiniones del CA en Notion → genera PDF + HTML en `CARPETA_WEB`, reutilizando la caché si los datos no han cambiado. [skill_opiniones_ca.py:467](../backend/skill_opiniones_ca.py#L467)
- **Parámetros:** `advisee` — str — nombre; `ca_nombre` — str — CA (opcional).
- **Devuelve:** str — `slug` (nombre base de los archivos).
- **Efectos (Notion/disco):** **Lee Notion** y escribe PDF/HTML/caché (salvo hit de caché).
- **Se llama desde:** `api_server.py` [api_server.py:653](../backend/api_server.py#L653).
- **Notas:** Lanza `ValueError` si no hay ninguna opinión; `RuntimeError` si reportlab no está instalado.

---

## `backend/skill_resumen_evaluacion.py` — Resumen de evaluación por competencias

**Propósito:** Generar un resumen estructurado por apartados de competencias a partir del texto libre
de las evaluaciones, calibrado según el cargo del advisee. Los criterios detallados están hardcodeados
y NO se envían a Claude: solo se mandan a Claude los nombres de los apartados y el texto de evaluación.

**Entradas/Salidas (archivos generados, formatos):**
- Entrada: cargo + texto libre de evaluaciones (o lista de evaluaciones estructuradas).
- Salida: string con el resumen estructurado (no escribe archivos ni Notion). El llamador decide qué hacer con él (p.ej. guardarlo en Notion).
- Contiene el diccionario `CRITERIOS` (por cargo: Analista, Asociado, Asociado Sr, Manager) y `_CARGO_ALIAS` para normalizar cargos (incluye variantes en inglés).

### `_cargo_clave(cargo)`
- **Qué hace:** Normaliza el cargo (minúsculas, sin espacios) a la clave canónica de `CRITERIOS` vía `_CARGO_ALIAS`. [skill_resumen_evaluacion.py:197](../backend/skill_resumen_evaluacion.py#L197)
- **Parámetros:** `cargo` — str — cargo en cualquier variante conocida.
- **Devuelve:** str|None — clave canónica o `None` si no se reconoce.
- **Efectos:** Ninguno.
- **Se llama desde:** `generar_resumen_evaluacion` [skill_resumen_evaluacion.py:259](../backend/skill_resumen_evaluacion.py#L259).
- **Notas:** Función interna.

### `evaluaciones_a_texto(evaluaciones)`
- **Qué hace:** Convierte una lista de evaluaciones estructuradas de Notion a texto en líneas (fecha, evaluador, nivel, proyecto, valoración, ejemplo). [skill_resumen_evaluacion.py:218](../backend/skill_resumen_evaluacion.py#L218)
- **Parámetros:** `evaluaciones` — list[dict] — evaluaciones.
- **Devuelve:** str — texto formateado, o `""` si vacía.
- **Efectos:** Ninguno.
- **Se llama desde:** `generar_resumen_desde_evaluaciones` [skill_resumen_evaluacion.py:307](../backend/skill_resumen_evaluacion.py#L307).
- **Notas:** Traduce `relacion` a líder/igual nivel/subordinado/sin nivel.

### `generar_resumen_evaluacion(nombre, cargo, evaluaciones_texto)`
- **Qué hace:** Genera el resumen estructurado por apartados. Elige los apartados según el cargo (o unos por defecto si no se reconoce) y llama a Claude con esos nombres de apartado y el texto. [skill_resumen_evaluacion.py:239](../backend/skill_resumen_evaluacion.py#L239)
- **Parámetros:** `nombre` — str — advisee; `cargo` — str — cargo; `evaluaciones_texto` — str — texto libre de opiniones.
- **Devuelve:** str — resumen estructurado.
- **Efectos (Claude):** **Llama a Claude** (`claude-sonnet-4-6`, `max_tokens=1200`, con `_SYSTEM_PROMPT`). No escribe disco ni Notion.
- **Se llama desde:** `ca_reviews.py` [ca_reviews.py:900](../backend/ca_reviews.py#L900) (importado en [ca_reviews.py:41](../backend/ca_reviews.py#L41)); y desde `generar_resumen_desde_evaluaciones`.
- **Notas:** Lanza `RuntimeError` si falta el cliente Anthropic. Si el cargo no se reconoce, loguea aviso y usa las 5 secciones base. Los criterios detallados de `CRITERIOS` NO se envían a Claude, solo los nombres de apartado.

### `generar_resumen_desde_evaluaciones(nombre, cargo, evaluaciones)`
- **Qué hace:** Versión de conveniencia: convierte evaluaciones estructuradas a texto y llama a `generar_resumen_evaluacion`. [skill_resumen_evaluacion.py:292](../backend/skill_resumen_evaluacion.py#L292)
- **Parámetros:** `nombre` — str — advisee; `cargo` — str — cargo; `evaluaciones` — list[dict] — evaluaciones de `obtener_evaluaciones_por_evaluado()`.
- **Devuelve:** str — resumen estructurado.
- **Efectos (Claude):** **Llama a Claude** indirectamente vía `generar_resumen_evaluacion`.
- **Se llama desde:** No se encontraron llamadas dentro del backend (función de conveniencia expuesta por el módulo).
- **Notas:** Lanza `ValueError` si no hay evaluaciones disponibles.

---

## `backend/skill_pdfs_fuentes.py` — PDFs de fuentes/evidencia en bruto

**Propósito:** Generar PDFs por fuente con el DATO EN BRUTO ordenado cronológicamente (para elaborar
el informe final manualmente), con el estilo de marca Igeneris. Reutiliza fuentes/logo del PDF de
opiniones. No usa Claude.

**Entradas/Salidas (archivos generados, formatos):**
- Entrada: datos leídos de Notion / project_evals (evaluaciones de proyecto, seguimiento personal, evaluaciones mensuales, opiniones del CA).
- Salidas en `config.CARPETA_WEB`, servidas por `/api/files/<archivo>`:
  - `evals_proyecto_{slug}.pdf` — evaluaciones de proyecto.
  - `seguimiento_personal_{slug}.pdf` — seguimiento personal.
  - `evals_mensuales_{slug}.pdf` — evaluaciones mensuales.
  - `info_completa_{slug}.pdf` — PDF combinado con las 4 fuentes.
- Reutiliza `_registrar_fuentes`, `_LOGO_PATH`, `_REPORTLAB_OK`, `_MESES` de `skill_opiniones_ca`.

### `_fecha_es(fecha)`
- **Qué hace:** Convierte `'2025-03-15'` a `'15 mar 2025'`. [skill_pdfs_fuentes.py:45](../backend/skill_pdfs_fuentes.py#L45)
- **Parámetros:** `fecha` — str — fecha en formato `YYYY-MM-DD`.
- **Devuelve:** str — fecha en español, o `"Sin fecha"`/valor original si falla.
- **Efectos:** Ninguno.
- **Se llama desde:** Todas las funciones que construyen entradas de este módulo.
- **Notas:** Usa `_MESES` importado de `skill_opiniones_ca`.

### `_esc(t)`
- **Qué hace:** Escapa HTML y convierte saltos de línea a `<br/>` (para el markup de reportlab Paragraph). [skill_pdfs_fuentes.py:53](../backend/skill_pdfs_fuentes.py#L53)
- **Parámetros:** `t` — cualquier valor (se convierte a str).
- **Devuelve:** str — texto escapado.
- **Efectos:** Ninguno.
- **Se llama desde:** `_construir_pdf`, `_construir_pdf_secciones`.
- **Notas:** Función interna.

### `_construir_pdf(titulo, advisee, ca, entradas, nombre_archivo)`
- **Qué hace:** Construye un PDF de marca con una lista plana de entradas `{header, meta, cuerpo}`: portada (nombre + logo opcional + título + CA + fecha) y las entradas separadas por líneas. [skill_pdfs_fuentes.py:57](../backend/skill_pdfs_fuentes.py#L57)
- **Parámetros:** `titulo` — str — título del documento; `advisee` — str — nombre; `ca` — str — CA; `entradas` — list[dict] — con `header`/`meta`/`cuerpo`; `nombre_archivo` — str — nombre del PDF.
- **Devuelve:** str — ruta del PDF.
- **Efectos (disco):** Escribe el PDF en `CARPETA_WEB`.
- **Se llama desde:** `generar_pdf_evals_proyecto`, `generar_pdf_seguimiento_personal`, `generar_pdf_evals_mensuales`.
- **Notas:** Lanza `RuntimeError` si falta reportlab. Usa `KeepTogether` por bloque. Si no hay entradas, escribe placeholder.

### `_ca_de(advisee)`
- **Qué hace:** Resuelve el CA del advisee desde Notion, tolerando errores. [skill_pdfs_fuentes.py:123](../backend/skill_pdfs_fuentes.py#L123)
- **Parámetros:** `advisee` — str — nombre.
- **Devuelve:** str — nombre del CA, o `""` si falla.
- **Efectos (Notion):** **Lee Notion** (`obtener_ca_de_empleado`).
- **Se llama desde:** `generar_pdf_evals_proyecto`, `generar_pdf_seguimiento_personal`, `generar_pdf_evals_mensuales`, `generar_pdf_completo`.
- **Notas:** Función interna.

### `generar_pdf_evals_proyecto(advisee)`
- **Qué hace:** Genera el PDF de evaluaciones de proyecto ordenadas por fecha. [skill_pdfs_fuentes.py:130](../backend/skill_pdfs_fuentes.py#L130)
- **Parámetros:** `advisee` — str — nombre.
- **Devuelve:** str — `slug`.
- **Efectos (Notion/disco):** **Lee** `obtener_evaluaciones_proyecto_por_evaluado` (de `project_evals`) y `_ca_de`; escribe `evals_proyecto_{slug}.pdf`.
- **Se llama desde:** `api_server.py` [api_server.py:666](../backend/api_server.py#L666).
- **Notas:** `meta` = evaluador · tipo · fecha; `cuerpo` = respuestas.

### `generar_pdf_seguimiento_personal(advisee)`
- **Qué hace:** Genera el PDF de seguimiento personal ordenado por fecha. [skill_pdfs_fuentes.py:143](../backend/skill_pdfs_fuentes.py#L143)
- **Parámetros:** `advisee` — str — nombre.
- **Devuelve:** str — `slug`.
- **Efectos (Notion/disco):** **Lee** `obtener_comentarios_personales` y `_ca_de`; escribe `seguimiento_personal_{slug}.pdf`.
- **Se llama desde:** `api_server.py` [api_server.py:667](../backend/api_server.py#L667).
- **Notas:** `header` = fecha; `meta` = autor; `cuerpo` = comentario.

### `generar_pdf_evals_mensuales(advisee)`
- **Qué hace:** Genera el PDF de evaluaciones mensuales ordenadas por fecha, con valoración y ejemplo. [skill_pdfs_fuentes.py:156](../backend/skill_pdfs_fuentes.py#L156)
- **Parámetros:** `advisee` — str — nombre.
- **Devuelve:** str — `slug`.
- **Efectos (Notion/disco):** **Lee** `obtener_evaluaciones_por_evaluado` y `_ca_de`; escribe `evals_mensuales_{slug}.pdf`.
- **Se llama desde:** `api_server.py` [api_server.py:668](../backend/api_server.py#L668).
- **Notas:** Traduce `relacion` a líder/igual/subordinado/sin nivel.

### `_entradas_evals_proyecto(advisee)`
- **Qué hace:** Devuelve las entradas de evaluaciones de proyecto (para el PDF combinado). [skill_pdfs_fuentes.py:183](../backend/skill_pdfs_fuentes.py#L183)
- **Parámetros:** `advisee` — str — nombre.
- **Devuelve:** list[dict] — entradas `{header, meta, cuerpo}`.
- **Efectos (Notion):** **Lee** `obtener_evaluaciones_proyecto_por_evaluado`.
- **Se llama desde:** `generar_pdf_completo` [skill_pdfs_fuentes.py:290](../backend/skill_pdfs_fuentes.py#L290).
- **Notas:** Función interna; misma lógica que `generar_pdf_evals_proyecto` pero solo construye entradas.

### `_entradas_seguimiento(advisee)`
- **Qué hace:** Devuelve las entradas de seguimiento personal (para el PDF combinado). [skill_pdfs_fuentes.py:192](../backend/skill_pdfs_fuentes.py#L192)
- **Parámetros:** `advisee` — str — nombre.
- **Devuelve:** list[dict] — entradas.
- **Efectos (Notion):** **Lee** `obtener_comentarios_personales`.
- **Se llama desde:** `generar_pdf_completo` [skill_pdfs_fuentes.py:291](../backend/skill_pdfs_fuentes.py#L291).
- **Notas:** Función interna.

### `_entradas_evals_mensuales(advisee)`
- **Qué hace:** Devuelve las entradas de evaluaciones mensuales (para el PDF combinado). [skill_pdfs_fuentes.py:198](../backend/skill_pdfs_fuentes.py#L198)
- **Parámetros:** `advisee` — str — nombre.
- **Devuelve:** list[dict] — entradas.
- **Efectos (Notion):** **Lee** `obtener_evaluaciones_por_evaluado`.
- **Se llama desde:** `generar_pdf_completo` [skill_pdfs_fuentes.py:289](../backend/skill_pdfs_fuentes.py#L289).
- **Notas:** Función interna.

### `_entradas_opiniones(advisee, ca)`
- **Qué hace:** Devuelve las entradas de opiniones del CA (nota del CA + resumen) para el PDF combinado. [skill_pdfs_fuentes.py:220](../backend/skill_pdfs_fuentes.py#L220)
- **Parámetros:** `advisee` — str — nombre; `ca` — str — CA.
- **Devuelve:** list[dict] — entradas.
- **Efectos (Notion):** **Lee** `obtener_opiniones_ca_por_advisee` (tolera errores devolviendo lista vacía).
- **Se llama desde:** `generar_pdf_completo` [skill_pdfs_fuentes.py:288](../backend/skill_pdfs_fuentes.py#L288).
- **Notas:** Función interna.

### `_construir_pdf_secciones(titulo, advisee, ca, secciones, nombre_archivo)`
- **Qué hace:** Como `_construir_pdf` pero con varias secciones, cada una con su encabezado y contador de entradas. [skill_pdfs_fuentes.py:237](../backend/skill_pdfs_fuentes.py#L237)
- **Parámetros:** `titulo` — str; `advisee` — str; `ca` — str; `secciones` — list[tuple(str, list[dict])] — pares `(título_sección, entradas)`; `nombre_archivo` — str.
- **Devuelve:** str — ruta del PDF.
- **Efectos (disco):** Escribe el PDF en `CARPETA_WEB`.
- **Se llama desde:** `generar_pdf_completo` [skill_pdfs_fuentes.py:294](../backend/skill_pdfs_fuentes.py#L294).
- **Notas:** Lanza `RuntimeError` si falta reportlab. Añade el contador `(N)` en cada encabezado de sección.

### `generar_pdf_completo(advisee)`
- **Qué hace:** Genera un único PDF con TODA la información recibida de la persona (las 4 fuentes: opiniones del CA, evaluaciones mensuales, evaluaciones de proyecto, seguimiento personal). [skill_pdfs_fuentes.py:284](../backend/skill_pdfs_fuentes.py#L284)
- **Parámetros:** `advisee` — str — nombre.
- **Devuelve:** str — `slug`.
- **Efectos (Notion/disco):** **Lee Notion** (las 4 fuentes + CA); escribe `info_completa_{slug}.pdf`.
- **Se llama desde:** `api_server.py` [api_server.py:669](../backend/api_server.py#L669).
- **Notas:** Compone las secciones y delega en `_construir_pdf_secciones`.