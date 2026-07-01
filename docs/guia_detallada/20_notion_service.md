## `backend/notion_service.py` — La base de datos (Notion)

**Propósito:** Es la capa de persistencia de EvaluaBot: todo lo que el proyecto "guarda" o "lee" vive en Notion, y este módulo encapsula esa comunicación. Ofrece funciones de alto nivel (guardar/leer evaluaciones, opiniones CA, objetivos, barbecho, permisos, calendario, empleados, preguntas, criterios, informes finales, MiddleOffice) y una amplia capa de helpers de bajo nivel que resuelven la estructura real de páginas y bases dentro del workspace de Notion.

**Cómo modela los datos en Notion:** El módulo asume un workspace de Notion organizado en **páginas contenedoras** (p. ej. `Listas de datos`, `Evaluaciones individuales`, `Seguimiento Career Advisor`, `Evaluaciones Continuas`, `Preguntas Chatbot`, `Resultados Evaluaciones`) que a su vez contienen **bases de datos** (child_databases) o **links a páginas/bases**. Los nombres de estas páginas/bases se configuran en `config.py` (constantes `NOTION_*_PAGE_NAME`). El módulo no guarda IDs fijos de la mayoría de bases: los **descubre dinámicamente por nombre** (con `notion.search`, recorriendo bloques hijos, o siguiendo links) y los **cachea en memoria**. Patrones de modelado por dominio:
- **Empleados:** una sola base "Lista de empleados" (columnas Nombre, Email, Cargo, ID_usuario/Slack ID, Foto, Idioma, Pais, Baja, aliases Slack).
- **Evaluaciones mensuales de proyecto:** **una base de datos por persona evaluada**, titulada con `PREFIJO_BBDD_EVALUADO + nombre`, bajo la página "Evaluaciones individuales". Cada fila es una evaluación (valoración/justificación separada por relación superior/igual/inferior + Area).
- **Opiniones CA:** bases `Opiniones - {advisee}` (estructura nueva) u `Opiniones CA - {ca}` (antigua) bajo "Seguimiento Career Advisor".
- **Advisees y CA:** base "Lista CA" con columna CA y columnas A1, A2, … (los advisees de cada CA).
- **Objetivos:** base global "Objetivos empleados" y, aparte, bases por persona `Objetivos - {nombre}`.
- **Barbecho (evaluación continua):** base "Resultados Barbecho" (antes "Registros barbecho").
- **Preguntas:** bases configurables por grupo (Negocio, MiddleOffice, Palantir) y de evaluación personal, con auto-poblado idempotente de valores por defecto.
- **Permisos:** bases "Acceso CA" (por CA) y "Acceso Individual Advisee" (por par CA+advisee), con checkbox Activo.
- **Calendario:** base "Calendario evaluaciones" con filas cuya fecha marca inicios de ciclos.
- **MiddleOffice:** bases "Cargos de MiddleOffice" y "Relaciones de evaluaciones MiddleOffice" bajo la página "Gestión de MiddleOffice".

El módulo soporta **dos APIs de Notion**: la clásica basada en `databases` y la nueva basada en `data_sources`. `_usa_data_sources()` decide cuál, y casi todos los helpers tienen ramas duplicadas para ambas.

**Variables/constantes de módulo relevantes:**
- Importadas: `config` (nombres de páginas, IDs, zona horaria, prefijos), `notion` (cliente en `clients.py`), `IDIOMAS_SOPORTADOS` (i18n), `bbdd_por_evaluado` y `lock` (estado global en `state.py`), `normalizar_nombre` (utils).
- `_NOTION_PAGE_STYLE` — estilos (emoji/color/callout) por nombre de página para decorarlas.
- Cachés de IDs de bases (protegidos por `lock` o locks propios): `bbdd_por_evaluado`, `_cache_bbdd_continuas`, `_cache_bbdd_sesiones_anual`, `_cache_lista_ca`, `_cache_advisees_por_ca`, `_cache_objetivos_db`, `_cache_objetivos_persona`, `_cache_acceso_ca_db`, `_cache_acceso_individual_db`, `_cache_informes_finales_db`, `_cache_pagina_preguntas`, `_cache_preguntas_mo_db`, `_cache_preguntas_palantir_db`, `_cache_calendario_db`, `_cache_personales_page_id`, `_cache_personal_eval_db`, `_cache_personal_preguntas_db`, `_cache_gestion_mo_page`, `_cache_cargos_mo`, `_cache_relaciones_mo`, `_criterios_db_ids`, `_ejemplos_db_id`, `_cache_nombre_por_id`.
- Cachés de datos con TTL de 300 s (5 min): `_empleados_cache_*`, `_preguntas_cache*`, `_cache_preguntas_mo_data`, `_cache_preguntas_palantir_data`, `_cache_criterios`, `_cache_ejemplos`, `_cache_personal_preguntas`.
- Locks propios: `_lock_pagina_preguntas`, `_lock_preguntas`, `_lock_preguntas_mo`, `_lock_preguntas_palantir`, `_lock_empleados`, `_lock_criterios`, `_lock_ejemplos`.
- Sets "ya poblado" (idempotencia): `_preguntas_bbdd_pobladas`, `_preguntas_mo_bbdd_pobladas`, `_preguntas_palantir_bbdd_pobladas`, `_mensaje_inicial_migrado`.
- Constantes de nombres/props: `NOTION_QUESTIONS_DATABASE_NAME`, `_NOMBRE_BBDD_PREGUNTAS_*`, `_TITULO_BBDD_BARBECHO_*`, `_PROPS_*`, `_PREGUNTAS_*_DEFAULT`, `_CARGOS_MO_DEFAULT`, `_RELACIONES_MO_DEFAULT`, textos de preguntas `_Q4_*`/`_Q5_TEXTO`, `PREGUNTAS_PERSONALES_DEFAULT`.

> **Nota general sobre "Se usa desde":** este módulo es consumido principalmente por `api_server.py` (endpoints de la web), `reports.py` (informes) y la lógica del bot de Slack. Cuando la llamada concreta no es deducible del propio archivo, se indica el consumo probable.

---

## Helpers de bajo nivel (títulos, IDs, propiedades)

### `_titulo_bbdd(titulo)` — [notion_service.py:17](../backend/notion_service.py#L17)
- **Qué hace:** compone el título de la base de una persona evaluada anteponiendo `config.PREFIJO_BBDD_EVALUADO`.
- **Parámetros:** `titulo` — str — nombre de la persona.
- **Devuelve:** str con prefijo.
- **Efectos:** ninguno.

### `_extraer_titulo_bbdd(bbdd)` — [notion_service.py:21](../backend/notion_service.py#L21)
- **Qué hace:** extrae el título de un objeto base/data_source de Notion (usa `name` o concatena `title`).
- **Parámetros:** `bbdd` — dict — objeto de Notion.
- **Devuelve:** str.
- **Notas:** trivial; cubre ambos formatos de respuesta.

### `_extraer_titulo_pagina(pagina)` — [notion_service.py:27](../backend/notion_service.py#L27)
- **Qué hace:** busca la propiedad de tipo `title` de una página y devuelve su texto plano.
- **Parámetros:** `pagina` — dict.
- **Devuelve:** str (vacío si no hay título).

### `_propiedades_bbdd_evaluaciones()` — [notion_service.py:34](../backend/notion_service.py#L34)
- **Qué hace:** devuelve el esquema de columnas de una base de evaluaciones individuales (Name, Evaluador, Proyecto, Fecha, Area select, y valoraciones/justificaciones de superiores/iguales/inferiores).
- **Devuelve:** dict de propiedades listo para crear/actualizar la base.

### `_normalizar_notion_id(valor)` — [notion_service.py:52](../backend/notion_service.py#L52)
- **Qué hace:** limpia un ID o URL de Notion y extrae los últimos 32 caracteres hexadecimales.
- **Parámetros:** `valor` — str — ID o URL.
- **Devuelve:** str (ID normalizado sin guiones).

### `_data_source_id(resultado_bbdd)` — [notion_service.py:58](../backend/notion_service.py#L58)
- **Qué hace:** obtiene el `data_source_id` de un objeto de Notion (soporta API nueva y clásica).
- **Devuelve:** str id.
- **Notas:** si el objeto es un `data_source`, devuelve su `id`; si tiene `data_sources`, el primero; si no, el `id` propio.

### `_usa_data_sources()` — [notion_service.py:65](../backend/notion_service.py#L65)
- **Qué hace:** detecta si el cliente Notion usa la API de data_sources (tiene `data_sources` y no `databases.query`).
- **Devuelve:** bool. Determina las ramas de casi todas las operaciones de creación/consulta.

### `_tipo_objeto_busqueda_bbdd()` — [notion_service.py:69](../backend/notion_service.py#L69)
- **Qué hace:** devuelve `"data_source"` o `"database"` según la API, para filtrar en `notion.search`.
- **Devuelve:** str.

### `_coincide_parent_bbdd(bbdd, parent)` — [notion_service.py:73](../backend/notion_service.py#L73)
- **Qué hace:** comprueba si una base pertenece al parent dado. En API de data_sources siempre devuelve True (no fiable el parent), si no compara el campo `parent`.
- **Devuelve:** bool.

### `_query_bbdd(database_id, **kwargs)` — [notion_service.py:79](../backend/notion_service.py#L79)
- **Qué hace:** consulta una base con la API disponible (`databases.query` o `data_sources.query`).
- **Parámetros:** `database_id` — str; `**kwargs` — filtros/paginación de Notion.
- **Devuelve:** respuesta cruda de Notion (dict con `results`, `has_more`, `next_cursor`).
- **Efectos:** lectura de Notion. Es la primitiva de lectura usada por casi todo el módulo.

### `_titulo_child_database(bloque)` / `_titulo_child_page(bloque)` — [notion_service.py:85](../backend/notion_service.py#L85), [notion_service.py:89](../backend/notion_service.py#L89)
- **Qué hacen:** extraen el título de un bloque `child_database` / `child_page`.
- **Devuelven:** str.

### `_target_link_to_page(bloque)` — [notion_service.py:93](../backend/notion_service.py#L93)
- **Qué hace:** devuelve el ID destino de un bloque `link_to_page` (sea page_id o database_id).
- **Devuelve:** str o None.

### `_menciones_pagina_en_bloque(bloque)` — [notion_service.py:102](../backend/notion_service.py#L102)
- **Qué hace:** generador que recorre el rich_text de un bloque y produce `(texto, id)` por cada mención a página o base.
- **Devuelve:** yields de tuplas `(str, str|None)`.

### `_iter_blocks(page_id)` — [notion_service.py:112](../backend/notion_service.py#L112)
- **Qué hace:** itera todos los bloques hijos de una página, paginando de 100 en 100.
- **Parámetros:** `page_id` — str.
- **Devuelve:** generador de dicts de bloque.
- **Efectos:** lecturas repetidas a `blocks.children.list`. Es la primitiva para recorrer la estructura de páginas.

### `_page_or_database_link_by_name(page_id, nombre_objetivo)` — [notion_service.py:126](../backend/notion_service.py#L126)
- **Qué hace:** dentro de una página, busca por nombre (igual o contenido, normalizado) una child_page, child_database, link_to_page o mención, y devuelve su ID.
- **Parámetros:** `page_id` — str; `nombre_objetivo` — str.
- **Devuelve:** str id o None.
- **Efectos:** recorre bloques y, para links, hace `pages.retrieve`/`databases.retrieve`. Es el localizador clave de navegación por nombre.

### `_elegir_child_database(bases, nombre_objetivo)` — [notion_service.py:163](../backend/notion_service.py#L163)
- **Qué hace:** de una lista de bloques child_database elige el que coincide con el nombre objetivo (exacto, luego contenido, luego por palabras clave tipo "empleados", "equipo", "staff"…).
- **Devuelve:** el bloque elegido o None.

### `_child_database_preferida(page_id)` — [notion_service.py:182](../backend/notion_service.py#L182)
- **Qué hace:** lista las child_databases de una página y elige la lista de empleados; lanza error si no hay ninguna o no la encuentra.
- **Devuelve:** bloque child_database.
- **Efectos:** lectura de bloques. **Notas:** puede lanzar `RuntimeError`.

### `_pagina_objetivo_en_bbdd(database_id, nombre_objetivo)` — [notion_service.py:200](../backend/notion_service.py#L200)
- **Qué hace:** busca dentro de una base la página cuyo título coincide con el nombre objetivo, paginando.
- **Devuelve:** dict página o None.

### `_buscar_objeto_notion_por_nombre(nombre_objetivo)` — [notion_service.py:219](../backend/notion_service.py#L219)
- **Qué hace:** búsqueda global (`notion.search`) de una base o página por nombre.
- **Devuelve:** str id o None.
- **Efectos:** llamadas a `notion.search`. Fallback cuando la navegación por estructura falla.

### `_resolver_ruta_lista_empleados(origen_id)` — [notion_service.py:236](../backend/notion_service.py#L236)
- **Qué hace:** a partir de un ID/URL de origen, resuelve el ID de la lista de empleados: primero busca la página de listas de datos, luego el link a la lista de empleados dentro de ella, con varios fallbacks (link directo, búsqueda global). Decora la página de listas por el camino.
- **Parámetros:** `origen_id` — str — configurado en `NOTION_EMPLOYEES_DATABASE_ID`.
- **Devuelve:** str id.
- **Notas:** lanza `RuntimeError` si no encuentra la lista.

### `_retrieve_bbdd(database_id)` — [notion_service.py:263](../backend/notion_service.py#L263)
- **Qué hace:** resuelve y recupera la base de empleados real (data_source o database), atravesando la posible jerarquía y validando que el título coincida con `NOTION_EMPLOYEES_DATABASE_NAME`.
- **Devuelve:** tupla `(database_id/data_source_id, objeto_base)`.
- **Efectos:** múltiples `retrieve`/`query`. Usada por todas las lecturas de empleados.

### `_parent_para_nueva_pagina(database_id)` — [notion_service.py:297](../backend/notion_service.py#L297)
- **Qué hace:** construye el dict `parent` para crear una página dentro de una base (`data_source_id` o `database_id`).
- **Devuelve:** dict.

### `_crear_pagina_en_bbdd(database_id, properties)` — [notion_service.py:301](../backend/notion_service.py#L301)
- **Qué hace:** crea una página (fila) dentro de una base con las propiedades dadas.
- **Efectos:** `notion.pages.create`. Es la primitiva de escritura de filas usada por casi todo el módulo.

### `asegurar_propiedades_bbdd(database_id)` — [notion_service.py:305](../backend/notion_service.py#L305)
- **Qué hace:** asegura que una base de evaluaciones tenga todas las columnas necesarias; añade las que falten.
- **Efectos:** `retrieve` + `update` de la base si faltan columnas.
- **Se usa desde:** `obtener_o_crear_bbdd_evaluado`, `guardar_en_notion`.

### `_parent_bbdd_referencia()` — [notion_service.py:320](../backend/notion_service.py#L320)
- **Qué hace:** devuelve el parent raíz donde crear bases nuevas: `NOTION_PARENT_PAGE_ID` si está configurado, si no el parent de la base de referencia.
- **Devuelve:** dict `{"type":"page_id","page_id":...}`.
- **Notas:** lanza `RuntimeError` si no puede determinarlo.

### `_bloque_texto(texto, negrita=False)` — [notion_service.py:358](../backend/notion_service.py#L358)
- **Qué hace:** helper que construye un rich_text de Notion con anotaciones.
- **Devuelve:** lista de un elemento rich_text.

### `_decorar_pagina_notion(page_id, nombre_pagina)` — [notion_service.py:373](../backend/notion_service.py#L373)
- **Qué hace:** aplica estética (icono emoji + callout descriptivo + divisor) a una página conocida, según `_NOTION_PAGE_STYLE`. Idempotente (no duplica el callout si ya existe "Evaluabot").
- **Efectos:** `pages.update` (icono) y `blocks.children.append`.
- **Notas:** best-effort, captura y loguea excepciones.

### `aplicar_estetica_notion()` — [notion_service.py:414](../backend/notion_service.py#L414)
- **Qué hace:** recorre las páginas principales del workspace y las decora; crea las que faltan (excepto "Listas de datos").
- **Efectos:** lecturas + decoraciones/creaciones. **Se usa desde:** arranque/inicialización de la app.

### `_buscar_pagina_en_jerarquia(nombre_pagina, root_id)` — [notion_service.py:429](../backend/notion_service.py#L429)
- **Qué hace:** busca una página por nombre en la raíz y hasta 2 niveles bajo las páginas TO-DO / TO-SEE.
- **Devuelve:** str id o None.

### `_parent_bbdd_en_pagina(nombre_pagina, crear=False)` — [notion_service.py:453](../backend/notion_service.py#L453)
- **Qué hace:** localiza (o crea si `crear=True`) una página contenedora por nombre y devuelve su dict parent, decorándola.
- **Parámetros:** `nombre_pagina` — str; `crear` — bool.
- **Devuelve:** dict parent (page_id de la página encontrada/creada, o el parent raíz como fallback).
- **Efectos:** puede crear páginas. Helper central para ubicar dónde viven las bases por dominio.

### `_buscar_bbdd_en_pagina(nombre_pagina, titulo_bbdd)` — [notion_service.py:470](../backend/notion_service.py#L470)
- **Qué hace:** dentro de una página contenedora (por nombre), busca una base por título (child_database o link_to_page) y devuelve su data_source_id.
- **Devuelve:** str id o None.

### `_buscar_bbdd_en_pagina_id(pagina_id, titulo_bbdd)` — [notion_service.py:498](../backend/notion_service.py#L498)
- **Qué hace:** igual que la anterior pero recibe el `page_id` directamente.
- **Devuelve:** str id o None.

### `_obtener_o_crear_pagina_preguntas_id()` — [notion_service.py:527](../backend/notion_service.py#L527)
- **Qué hace:** localiza (o crea) la sub-página de preguntas de evaluación mensual. Busca en la estructura nueva ("Preguntas Chatbot" → "Preguntas evaluación mensual"/"Preguntas") y en la antigua ("Listas de datos" → "Preguntas"); si no existe, la crea.
- **Devuelve:** str page_id o None. **Efectos:** lecturas + posible creación; cachea en `_cache_pagina_preguntas` bajo `_lock_pagina_preguntas`.

### `obtener_parent_bbdd_evaluados()` — [notion_service.py:585](../backend/notion_service.py#L585)
- **Qué hace:** wrapper de `_parent_bbdd_referencia()` que devuelve None (con warning) en vez de lanzar excepción.
- **Devuelve:** dict parent o None.

---

## Preguntas de evaluación mensual (Negocio, MiddleOffice, Palantir)

### `_propiedades_bbdd_preguntas()` — [notion_service.py:604](../backend/notion_service.py#L604)
- **Qué hace:** esquema de la base de preguntas de Negocio (Texto title, Tipo select, Clave select).
- **Devuelve:** dict de props.

### `_obtener_o_crear_bbdd_preguntas()` — [notion_service.py:622](../backend/notion_service.py#L622)
- **Qué hace:** obtiene (o crea) la base "Preguntas Negocio" dentro de la sub-página de preguntas, con fallback a ubicación antigua; la puebla con las preguntas por defecto.
- **Devuelve:** str db_id o None. **Efectos:** search/retrieve/create + `_poblar_bbdd_preguntas`.

### `_poblar_bbdd_preguntas(bbdd_id)` — [notion_service.py:684](../backend/notion_service.py#L684)
- **Qué hace:** añade las filas de `_PREGUNTAS_INICIALES` que falten; migra el texto de q1 antiguo ("Este mes…"). Idempotente vía `_preguntas_bbdd_pobladas`.
- **Efectos:** lecturas + `create`/`update` de filas.

### `obtener_preguntas_desde_notion(tipo)` — [notion_service.py:722](../backend/notion_service.py#L722)
- **Qué hace:** devuelve `{clave: texto}` de las preguntas de Negocio para un tipo de relación (Top-Bottom / Bottom-Top / Same Level). Cachea 5 min por tipo.
- **Parámetros:** `tipo` — str.
- **Devuelve:** dict.
- **Se usa desde:** flujo de evaluación de proyecto (Slack) para preguntar según jerarquía.

### `_obtener_o_crear_bbdd_preguntas_mo()` — [notion_service.py:773](../backend/notion_service.py#L773)
- **Qué hace:** obtiene/crea la base "Preguntas MiddleOffice" (sin jerarquía), la puebla y cachea su id.
- **Devuelve:** str db_id o None.

### `_poblar_bbdd_preguntas_mo(db_id)` — [notion_service.py:822](../backend/notion_service.py#L822)
- **Qué hace:** añade las filas por defecto de MiddleOffice (`_PREGUNTAS_MO_DEFAULT`) que falten. Idempotente.
- **Efectos:** lecturas paginadas + `create` de filas.

### `obtener_preguntas_mo()` — [notion_service.py:859](../backend/notion_service.py#L859)
- **Qué hace:** devuelve `[{clave, texto}]` de MiddleOffice, filtradas a claves conocidas y ordenadas según `_CLAVES_MO_ORDEN`. Cachea 5 min. Fallback a defaults ante error.
- **Devuelve:** lista de dicts.

### `_obtener_o_crear_bbdd_preguntas_palantir()` — [notion_service.py:936](../backend/notion_service.py#L936)
- **Qué hace:** obtiene/crea la base "Preguntas Palantir" (con jerarquía Tipo), la puebla y cachea su id.
- **Devuelve:** str db_id o None.

### `_poblar_bbdd_preguntas_palantir(db_id)` — [notion_service.py:988](../backend/notion_service.py#L988)
- **Qué hace:** añade filas por defecto de Palantir (`_PREGUNTAS_PALANTIR_DEFAULT`) que falten; migra q1 antiguo. Idempotente.
- **Efectos:** lecturas + `create`/`update`.

### `obtener_preguntas_palantir(tipo)` — [notion_service.py:1026](../backend/notion_service.py#L1026)
- **Qué hace:** devuelve `[{clave, texto}]` de Palantir para el tipo de jerarquía dado, ordenadas por clave. Cachea 5 min por tipo. Fallback a defaults.
- **Devuelve:** lista de dicts.

---

## Evaluaciones mensuales de proyecto

### `obtener_o_crear_bbdd_evaluado(nombre_evaluado)` — [notion_service.py:1072](../backend/notion_service.py#L1072)
- **Qué hace:** obtiene o crea la base de datos de una persona evaluada (título `PREFIJO + nombre`) bajo "Evaluaciones individuales". Cachea en `bbdd_por_evaluado`.
- **Parámetros:** `nombre_evaluado` — str.
- **Devuelve:** str database_id/data_source_id.
- **Efectos:** search + create de base + `asegurar_propiedades_bbdd`. **Se usa desde:** `guardar_en_notion`.

### `guardar_en_notion(nombre, respuestas, relacion="igual", area="Negocio")` — [notion_service.py:1111](../backend/notion_service.py#L1111)
- **Qué hace:** guarda una evaluación de proyecto como nueva fila en la base del evaluado. Extrae valoración/justificación de `respuestas` (ignora evaluado/proyecto/satisfaccion) y las escribe en la columna según la relación (de superiores/iguales/inferiores).
- **Parámetros:** `nombre` — str (evaluador); `respuestas` — dict; `relacion` — str ("superior"/"inferior"/otro→igual); `area` — str.
- **Devuelve:** str page_id de la fila creada, o None ante error.
- **Efectos:** crea/actualiza base + crea fila. **Se usa desde:** flujo de evaluación de proyecto en Slack.

### `actualizar_en_notion(page_id, nombre, respuestas, relacion="igual", area="Negocio")` — [notion_service.py:1141](../backend/notion_service.py#L1141)
- **Qué hace:** actualiza las columnas de valoración/justificación de una fila de evaluación ya existente.
- **Devuelve:** bool éxito.
- **Efectos:** `notion.pages.update`. **Notas:** `nombre` y `area` no se usan realmente en el update.

---

## Barbecho (evaluación continua)

### `_obtener_o_crear_bbdd_continuas()` — [notion_service.py:1168](../backend/notion_service.py#L1168)
- **Qué hace:** obtiene/crea la base de barbecho ("Resultados Barbecho", antes "Registros barbecho"), bajo "Resultados Evaluaciones" o el root. Cachea en `_cache_bbdd_continuas`.
- **Devuelve:** str db_id.
- **Notas:** columnas Name, Empleado, Area, Labores, Fecha.

### `guardar_barbecho_en_notion(nombre, area, labores)` — [notion_service.py:1210](../backend/notion_service.py#L1210)
- **Qué hace:** guarda un registro de barbecho (área + labores en periodo sin proyecto) como nueva fila.
- **Devuelve:** bool éxito.
- **Efectos:** crea fila. **Se usa desde:** flujo de barbecho en Slack.

### `obtener_barbecho_por_empleado(nombre)` — [notion_service.py:1230](../backend/notion_service.py#L1230)
- **Qué hace:** lee los registros de barbecho de un empleado (filtrando por nombre normalizado), ordenados por fecha.
- **Devuelve:** lista de `{area, labores, fecha, page_id, url}`.
- **Efectos:** lectura paginada.

---

## Log de evaluación anual asistida (auditoría CA vs IA)

### `_obtener_o_crear_bbdd_sesiones_anual()` — [notion_service.py:1278](../backend/notion_service.py#L1278)
- **Qué hace:** obtiene/crea la base "Log evaluacion anual asistida" (columnas Advisee, CA, Anio, Dimension, ValoracionCA, ValoracionIA, Eleccion, Divergencia, TextoFinal, Fecha). Cachea.
- **Devuelve:** str db_id o None.

### `guardar_log_evaluacion_anual(advisee, ca, anio, entradas)` — [notion_service.py:1325](../backend/notion_service.py#L1325)
- **Qué hace:** escribe un log de auditoría con las decisiones (CA vs IA) por dimensión. Best-effort, trunca textos a 2000 chars.
- **Parámetros:** `advisee` — str; `ca` — str; `anio` — int/str; `entradas` — list[dict] (con etiqueta, caTexto, claudeTexto, eleccion, divergencia, textoFinal).
- **Devuelve:** bool éxito.
- **Efectos:** crea una fila por entrada.

---

## Helpers de extracción de propiedades

### `_texto_rich_text(propiedades, nombre_propiedad)` — [notion_service.py:1356](../backend/notion_service.py#L1356)
- **Qué hace:** extrae el texto del primer item de una propiedad rich_text. Trivial.

### `_texto_title(propiedades, nombre_propiedad)` — [notion_service.py:1361](../backend/notion_service.py#L1361)
- **Qué hace:** extrae el texto del primer item de una propiedad title. Trivial.

### `_texto_propiedad(propiedades, nombre_propiedad)` — [notion_service.py:1366](../backend/notion_service.py#L1366)
- **Qué hace:** extrae texto de una propiedad **según su tipo** (title, rich_text, select, multi_select, people, email, formula string). Devuelve "" si no aplica.
- **Devuelve:** str. Helper de lectura genérico muy usado.

### `_texto_email_propiedad(propiedades, nombre_propiedad)` — [notion_service.py:1393](../backend/notion_service.py#L1393)
- **Qué hace:** extrae email de una propiedad email o people; si no, delega en `_texto_propiedad`.
- **Devuelve:** str.

### `_url_foto_propiedad(props, nombre_propiedad)` — [notion_service.py:1408](../backend/notion_service.py#L1408)
- **Qué hace:** extrae la URL de una foto de una propiedad files o url; si no, delega en `_texto_propiedad`.
- **Devuelve:** str.

---

## Empleados

### `_codigo_idioma(valor)` — [notion_service.py:1427](../backend/notion_service.py#L1427)
- **Qué hace:** extrae un código de idioma de 2 letras del texto de la columna Idioma (acepta "ES", "Español (ES)", "English"…). Devuelve el código en minúsculas o "".
- **Devuelve:** str.

### `_normalizar_idioma(valor)` — [notion_service.py:1451](../backend/notion_service.py#L1451)
- **Qué hace:** mapea la columna Idioma a un idioma soportado (`IDIOMAS_SOPORTADOS`); por defecto "es".
- **Devuelve:** str código.

### `_obtener_registros_empleados()` — [notion_service.py:1462](../backend/notion_service.py#L1462)
- **Qué hace:** lee la lista completa de empleados desde Notion, detectando dinámicamente la columna de nombre y extrayendo email, aliases (Slack), cargo, id_usuario (Slack ID), foto, idioma, país y flag Baja. Cachea 5 min; ante error devuelve caché anterior si existe.
- **Devuelve:** list[dict] de registros de empleado.
- **Efectos:** `_retrieve_bbdd` + query paginada. Es la fuente base de casi todas las consultas de empleados.

### `obtener_lista_empleados()` — [notion_service.py:1575](../backend/notion_service.py#L1575)
- **Qué hace:** devuelve solo los nombres canónicos de empleados.
- **Devuelve:** list[str].

### `obtener_registros_empleados()` — [notion_service.py:1580](../backend/notion_service.py#L1580)
- **Qué hace:** wrapper público de `_obtener_registros_empleados()`.
- **Devuelve:** list[dict].

### `obtener_perfil_empleado(nombre)` — [notion_service.py:1585](../backend/notion_service.py#L1585)
- **Qué hace:** devuelve `{cargo, foto, idioma, pais}` del empleado que coincide con el nombre (normalizado).
- **Devuelve:** dict.

### `idioma_de_persona(nombre)` — [notion_service.py:1594](../backend/notion_service.py#L1594)
- **Qué hace:** devuelve el idioma del empleado; por defecto "es".
- **Devuelve:** str.

---

## Criterios de evaluación por grupo

### `_obtener_db_criterios(grupo)` — [notion_service.py:1617](../backend/notion_service.py#L1617)
- **Qué hace:** descubre y cachea (5 min) el data_source_id de la base de criterios de cada grupo, buscando la página "Criterios de evaluaciones" (ubicación nueva y antigua) y recorriendo sus child_database/child_page.
- **Parámetros:** `grupo` — str (nombre de la BD de criterios, p. ej. Negocio/Palantir/MiddleOffice).
- **Devuelve:** str db_id o None.

### `obtener_criterios_evaluacion(grupo)` — [notion_service.py:1677](../backend/notion_service.py#L1677)
- **Qué hace:** devuelve `{criterio: {nivel: [texto]}}` para el grupo, leyendo columnas de niveles (Analista, Asociado, Asociado Sr, Manager). Cachea 5 min.
- **Devuelve:** dict anidado. **Se usa desde:** generación de informes/prompts de evaluación.

### `_rt(prop)` (interna en `obtener_criterios_evaluacion`) — [notion_service.py:1694](../backend/notion_service.py#L1694)
- **Qué hace:** helper local que concatena el plain_text de una propiedad rich_text.

---

## Ejemplos de guía

### `_obtener_db_ejemplos()` — [notion_service.py:1744](../backend/notion_service.py#L1744)
- **Qué hace:** descubre y cachea (5 min) el id de la base "Ejemplos de Guia para bot" (con nombres fallback), buscando en las páginas contenedoras y con fallback a búsqueda global; soporta que el id sea la base directamente o una página que la contiene.
- **Devuelve:** str db_id o None.

### `obtener_ejemplos_guia()` — [notion_service.py:1803](../backend/notion_service.py#L1803)
- **Qué hace:** devuelve `{tipo: texto_ejemplo}` leyendo la base de ejemplos (columna title = tipo, primera rich_text = ejemplo). Cachea 5 min.
- **Devuelve:** dict.

### `_rt(prop)` (interna en `obtener_ejemplos_guia`) — [notion_service.py:1819](../backend/notion_service.py#L1819)
- **Qué hace:** igual helper local para rich_text.

---

## Matching de nombres (fuzzy)

### `_tokens_nombre(nombre)` — [notion_service.py:1864](../backend/notion_service.py#L1864)
- **Qué hace:** devuelve el set de tokens (>1 char) del nombre normalizado. Trivial.

### `_normalizar_para_match(valor)` — [notion_service.py:1868](../backend/notion_service.py#L1868)
- **Qué hace:** normaliza (minúsculas, sin acentos, solo alfanumérico y espacios) para comparar nombres.
- **Devuelve:** str.

### `_compactar_match(valor)` — [notion_service.py:1877](../backend/notion_service.py#L1877)
- **Qué hace:** como el anterior pero sin espacios. Trivial.

### `_lcs_len(a, b)` — [notion_service.py:1881](../backend/notion_service.py#L1881)
- **Qué hace:** longitud de la subsecuencia común más larga (LCS) entre dos strings, con DP.
- **Devuelve:** int.

### `_score_orden_letras(buscado, candidato)` — [notion_service.py:1893](../backend/notion_service.py#L1893)
- **Qué hace:** puntúa la coincidencia por orden de letras usando LCS y coberturas ponderadas.
- **Devuelve:** float (0–1).

### `_score_nombre(buscado, candidato)` — [notion_service.py:1904](../backend/notion_service.py#L1904)
- **Qué hace:** score compuesto de similitud entre dos nombres (ratio, ratio compacto, coincidencia por tokens, orden de letras, bonus de prefijo).
- **Devuelve:** float. **Se usa desde:** `sugerir_empleados_parecidos`.

### `buscar_empleado_en_lista(nombre)` — [notion_service.py:1927](../backend/notion_service.py#L1927)
- **Qué hace:** devuelve el nombre canónico de la lista que coincide **exactamente** (tras normalizar) con el texto dado, o None.
- **Devuelve:** str o None. **Se usa desde:** validación de empleados.

### `buscar_empleado_y_cargo(nombre)` — [notion_service.py:1939](../backend/notion_service.py#L1939)
- **Qué hace:** devuelve `(nombre_canonico, cargo)` del empleado coincidente exacto, o `(None, None)`.
- **Devuelve:** tuple.

### `obtener_cargo_por_slack_id(user_id)` — [notion_service.py:1951](../backend/notion_service.py#L1951)
- **Qué hace:** devuelve el cargo del empleado cuyo ID_usuario coincide con el Slack user_id.
- **Devuelve:** str o None.

### `obtener_slack_ids_empleados()` — [notion_service.py:1959](../backend/notion_service.py#L1959)
- **Qué hace:** devuelve todos los Slack IDs (ID_usuario) no vacíos.
- **Devuelve:** list[str].

### `obtener_slack_id_por_nombre(nombre)` — [notion_service.py:1964](../backend/notion_service.py#L1964)
- **Qué hace:** devuelve el Slack ID del empleado cuyo nombre coincide (normalizado).
- **Devuelve:** str o None.

### `sugerir_empleados_parecidos(nombre, limite=8)` — [notion_service.py:1975](../backend/notion_service.py#L1975)
- **Qué hace:** devuelve una lista de nombres de empleados ordenados por similitud (`_score_nombre` sobre nombre y aliases), deduplicados. Mantiene al menos 3 aunque el score sea bajo.
- **Devuelve:** list[str]. **Se usa desde:** desambiguación cuando un nombre no coincide exactamente.

### `obtener_nombre_por_id_usuario(user_id)` — [notion_service.py:2000](../backend/notion_service.py#L2000)
- **Qué hace:** busca el nombre en la lista de empleados por la columna ID_usuario, consultando directamente Notion (paginado) y cacheando por user_id.
- **Devuelve:** str o None.
- **Efectos:** query paginada; cachea en `_cache_nombre_por_id`.

### `validar_empleado_en_lista(nombre)` — [notion_service.py:2040](../backend/notion_service.py#L2040)
- **Qué hace:** True si el nombre coincide con algún empleado.
- **Devuelve:** bool.

---

## Lectura de evaluaciones de proyecto

### `listar_bbdd_evaluados()` — [notion_service.py:2045](../backend/notion_service.py#L2045)
- **Qué hace:** lista todas las bases de evaluados (con prefijo `PREFIJO_BBDD_EVALUADO`) mediante `notion.search`, filtrando por parent.
- **Devuelve:** list[dict] `{id, evaluado}`.

### `obtener_evaluaciones_de_bbdd(database_id, evaluado)` — [notion_service.py:2058](../backend/notion_service.py#L2058)
- **Qué hace:** lee todas las evaluaciones de una base de evaluado, normalizando columnas (soporta nombres nuevos y antiguos), infiriendo la relación (superior/inferior/igual) y devolviendo un dict por fila.
- **Devuelve:** list[dict] con nombre, evaluado, proyecto, q1, q2, relacion, fecha, page_id, url…
- **Efectos:** query paginada.

### `obtener_evaluaciones()` — [notion_service.py:2114](../backend/notion_service.py#L2114)
- **Qué hace:** agrega las evaluaciones de todas las bases de evaluados; si no hay bases, lee la base de referencia como "General".
- **Devuelve:** list[dict].

### `obtener_evaluaciones_por_evaluado(evaluado)` — [notion_service.py:2124](../backend/notion_service.py#L2124)
- **Qué hace:** devuelve las evaluaciones de una persona concreta.
- **Devuelve:** list[dict]. **Notas:** lanza `RuntimeError` si falta `evaluado` o no hay tabla.

### `obtener_historial_mis_evaluaciones(evaluado, evaluador, proyecto_web)` — [notion_service.py:2133](../backend/notion_service.py#L2133)
- **Qué hace:** devuelve las evaluaciones que `evaluador` registró sobre `evaluado` en un proyecto similar a `proyecto_web` (matching difuso de proyecto por tokens y SequenceMatcher). Ordenadas por fecha.
- **Devuelve:** list[dict].
- **Se usa desde:** la web, para precargar evaluaciones previas al reeditar.

### `_tokenize(s)` / `_proyecto_coincide(pw, pn)` (internas) — [notion_service.py:2138](../backend/notion_service.py#L2138), [notion_service.py:2142](../backend/notion_service.py#L2142)
- **Qué hacen:** helpers locales para tokenizar y decidir si dos nombres de proyecto coinciden difusamente.

---

## Advisees y opiniones CA

### `_extraer_url_foto(prop)` — [notion_service.py:2178](../backend/notion_service.py#L2178)
- **Qué hace:** extrae URL de foto de una propiedad url/files/rich_text/title.
- **Devuelve:** str.

### `obtener_advisees(ca_nombre, ca_aliases=None)` — [notion_service.py:2197](../backend/notion_service.py#L2197)
- **Qué hace:** devuelve la lista de advisees de un CA desde "Lista CA" (fila con columna CA coincidente, leyendo columnas A1, A2…). Cachea la base y los resultados por CA.
- **Parámetros:** `ca_nombre` — str; `ca_aliases` — list opcional.
- **Devuelve:** list[str] de nombres de advisees.
- **Efectos:** search de la base + query paginada.

### `obtener_datos_empleados_por_nombres(nombres)` — [notion_service.py:2279](../backend/notion_service.py#L2279)
- **Qué hace:** para una lista de nombres, devuelve `{nombre, foto, email}` leyendo la lista de empleados.
- **Devuelve:** list[dict]. **Notas:** ante error devuelve entradas vacías por cada nombre.

### `obtener_opiniones_ca_por_advisee(ca_nombre, advisee, ca_aliases=None)` — [notion_service.py:2318](../backend/notion_service.py#L2318)
- **Qué hace:** devuelve las opiniones que un CA guardó sobre un advisee, combinando estructura nueva (`Opiniones - {advisee}`, filtrando por CA) y antigua (`Opiniones CA - {ca}`, filtrando por advisee). Ordenadas por fecha desc.
- **Devuelve:** list[dict] `{fecha, ca, opinion, resumen_advisee, page_id, url}`.
- **Notas:** contiene helpers locales `texto_alias`, `buscar_bbdd`, `leer_opiniones_nuevo`, `leer_opiniones_antiguo` (líneas 2323–2394).

### `listar_advisees_con_opiniones_ca(ca_nombre, ca_aliases=None)` — [notion_service.py:2418](../backend/notion_service.py#L2418)
- **Qué hace:** lista los advisees para los que existe una base "Opiniones - …" en "Seguimiento CA" con al menos una fila de este CA.
- **Devuelve:** list[str] ordenada.
- **Notas:** helpers locales `texto_alias` y `db_desde_bloque` (líneas 2424–2450).

---

## Objetivos (base global y por persona)

### `_obtener_o_crear_bbdd_objetivos()` — [notion_service.py:2493](../backend/notion_service.py#L2493)
- **Qué hace:** obtiene/crea la base global "Objetivos empleados" (props `_PROPS_OBJETIVOS`), preferentemente bajo la página "Listas de datos". Cachea.
- **Devuelve:** str db_id.

### `guardar_objetivos(ca_nombre, advisee_nombre, texto)` — [notion_service.py:2551](../backend/notion_service.py#L2551)
- **Qué hace:** guarda una fila de objetivos (texto libre) para un advisee en la base global.
- **Devuelve:** None. **Efectos:** crea fila (trunca a 2000 chars).

### `obtener_objetivos(advisee_nombre)` — [notion_service.py:2567](../backend/notion_service.py#L2567)
- **Qué hace:** lee los objetivos (base global) de un advisee, ordenados por fecha desc.
- **Devuelve:** list[dict] `{fecha, ca, objetivos}`.

### `_obtener_o_crear_bbdd_objetivos_persona(nombre)` — [notion_service.py:2615](../backend/notion_service.py#L2615)
- **Qué hace:** obtiene/crea la base por persona `Objetivos - {nombre}` (props `_PROPS_OBJETIVO_PERSONA`) bajo la página "Objetivos empleados". Cachea por clave normalizada.
- **Devuelve:** str db_id.

### `guardar_objetivo_persona(ca_nombre, advisee_nombre, titulo, kpis, descripcion, tipo)` — [notion_service.py:2662](../backend/notion_service.py#L2662)
- **Qué hace:** guarda un objetivo estructurado (título, KPIs, descripción, tipo) en la base de la persona.
- **Devuelve:** None. **Efectos:** crea fila (trunca a 2000 chars).

### `obtener_objetivos_persona(advisee_nombre)` — [notion_service.py:2678](../backend/notion_service.py#L2678)
- **Qué hace:** lee los objetivos estructurados de una persona, ordenados por fecha desc.
- **Devuelve:** list[dict] `{page_id, titulo, ca, kpis, descripcion, tipo, fecha}`.

### `eliminar_objetivo_persona(page_id)` — [notion_service.py:2725](../backend/notion_service.py#L2725)
- **Qué hace:** archiva (soft-delete) una fila de objetivo por page_id.
- **Devuelve:** bool. **Efectos:** `pages.update(archived=True)`.

---

## Lista CA y CA por empleado

### `_obtener_db_id_lista_ca()` — [notion_service.py:2738](../backend/notion_service.py#L2738)
- **Qué hace:** localiza y cachea el id de la base "Lista CA" (por búsqueda).
- **Devuelve:** str o None.

### `_asegurar_columna_acceso_lista_ca(db_id)` — [notion_service.py:2761](../backend/notion_service.py#L2761)
- **Qué hace:** asegura que "Lista CA" tenga la columna checkbox "Acceso habilitado".
- **Efectos:** `databases.update` si falta. **Notas:** no parece invocada dentro del archivo.

### `_ca_fila_por_nombre(db_id, ca_norms)` — [notion_service.py:2770](../backend/notion_service.py#L2770)
- **Qué hace:** busca en "Lista CA" la fila cuyo CA coincide con alguno de los nombres normalizados dados.
- **Devuelve:** dict fila o None.

### `obtener_ca_de_empleado(empleado_nombre)` — [notion_service.py:2794](../backend/notion_service.py#L2794)
- **Qué hace:** determina quién es el CA de un empleado revisando las columnas A1, A2… de "Lista CA".
- **Devuelve:** str (nombre del CA) o None.

---

## Permisos de acceso (por CA y por advisee individual)

### `_norm_ca(nombre)` — [notion_service.py:2834](../backend/notion_service.py#L2834)
- **Qué hace:** normaliza un nombre de CA (sin acentos, minúsculas, espacios colapsados) para usar como clave.
- **Devuelve:** str.

### `_obtener_o_crear_bbdd_acceso_ca()` — [notion_service.py:2839](../backend/notion_service.py#L2839)
- **Qué hace:** obtiene/crea la base "Acceso CA" (Name title, Activo checkbox). Cachea.
- **Devuelve:** str db_id.

### `_acceso_ca_fila(db_id, ca_keys)` — [notion_service.py:2878](../backend/notion_service.py#L2878)
- **Qué hace:** busca en "Acceso CA" la fila cuyo título normalizado esté en `ca_keys`.
- **Devuelve:** dict fila o None.

### `ca_tiene_acceso_activo(ca_nombre, ca_aliases=None)` — [notion_service.py:2895](../backend/notion_service.py#L2895)
- **Qué hace:** indica si un CA tiene el checkbox Activo marcado en "Acceso CA".
- **Devuelve:** bool. **Se usa desde:** control de acceso a la web del CA.

### `toggle_acceso_advisees(ca_nombre, activo, ca_aliases=None)` — [notion_service.py:2908](../backend/notion_service.py#L2908)
- **Qué hace:** activa/desactiva el acceso global de un CA (actualiza o crea la fila en "Acceso CA").
- **Devuelve:** bool.

### `_obtener_o_crear_bbdd_acceso_individual()` — [notion_service.py:2933](../backend/notion_service.py#L2933)
- **Qué hace:** obtiene/crea la base "Acceso Individual Advisee" (Name, CA rich_text, Activo checkbox). Cachea.
- **Devuelve:** str db_id.

### `_acceso_individual_fila(db_id, advisee_key, ca_key)` — [notion_service.py:2972](../backend/notion_service.py#L2972)
- **Qué hace:** busca la fila que empareja un advisee y un CA (ambos normalizados).
- **Devuelve:** dict fila o None.

### `advisee_tiene_acceso_individual(advisee, ca_nombre)` — [notion_service.py:2993](../backend/notion_service.py#L2993)
- **Qué hace:** indica si un advisee concreto tiene acceso individual habilitado para un CA.
- **Devuelve:** bool.

### `toggle_acceso_advisee_individual(ca_nombre, advisee, activo)` — [notion_service.py:3005](../backend/notion_service.py#L3005)
- **Qué hace:** activa/desactiva el acceso individual de un advisee para un CA (actualiza o crea fila).
- **Devuelve:** bool.

---

## Informes finales

### `_obtener_o_crear_bbdd_informes_finales()` — [notion_service.py:3039](../backend/notion_service.py#L3039)
- **Qué hace:** obtiene/crea la base "Informes Finales" (Name, CA, Fecha, Archivo_docx, Archivo_html, URL). Cachea.
- **Devuelve:** str db_id.

### `guardar_informe_final(ca_nombre, advisee, docx_filename, html_filename, url)` — [notion_service.py:3077](../backend/notion_service.py#L3077)
- **Qué hace:** guarda un informe final para un advisee; antes archiva los informes antiguos (mantiene solo 1 previo) y borra del disco (`config.CARPETA_WEB`) los ficheros de los archivados.
- **Devuelve:** None. **Efectos:** query paginada + archive de filas + borrado de ficheros + creación de fila.

### `obtener_informe_final_reciente(advisee)` — [notion_service.py:3130](../backend/notion_service.py#L3130)
- **Qué hace:** devuelve el informe final más reciente de un advisee.
- **Devuelve:** dict `{fecha, docx, html}` o None.

---

## Evaluaciones personales (seguimiento privado)

### `_migrar_mensaje_inicial(db_id)` — [notion_service.py:3198](../backend/notion_service.py#L3198)
- **Qué hace:** migra (una sola vez por db) el texto antiguo de la fila "mensaje_inicial" al texto actual de `PREGUNTAS_PERSONALES_DEFAULT`. Limpia la caché de preguntas si migra.
- **Efectos:** posible `pages.update`.

### `_obtener_o_crear_pagina_personales()` — [notion_service.py:3234](../backend/notion_service.py#L3234)
- **Qué hace:** localiza la página antigua "Evaluaciones Personales" (si aún existe) y cachea su id.
- **Devuelve:** str page_id o None.

### `_buscar_bbdd_personal_en_nueva_ubicacion(titulo_db)` — [notion_service.py:3249](../backend/notion_service.py#L3249)
- **Qué hace:** busca una BD personal en su ubicación post-migración según el mapa `_PERSONAL_DB_NUEVA_UBICACION` (Preguntas→"Preguntas evaluación personal" en "Preguntas Chatbot"; Respuestas→"Resultados Seguimiento personal" en "Resultados Evaluaciones").
- **Devuelve:** str db_id o None.

### `_buscar_o_crear_bbdd_en_personales(titulo_db, props, cache, poblar=None)` — [notion_service.py:3270](../backend/notion_service.py#L3270)
- **Qué hace:** localiza (nueva ubicación → antigua) o crea una de las bases personales; opcionalmente la puebla. Cachea en el dict `cache` dado.
- **Devuelve:** str db_id o None.

### `_poblar_bbdd_preguntas_personal(db_id)` — [notion_service.py:3332](../backend/notion_service.py#L3332)
- **Qué hace:** puebla la base de preguntas personales con `PREGUNTAS_PERSONALES_DEFAULT`.
- **Efectos:** crea filas.

### `obtener_preguntas_personales()` — [notion_service.py:3343](../backend/notion_service.py#L3343)
- **Qué hace:** devuelve `{clave: texto}` de las preguntas de seguimiento personal (localiza/crea/puebla la base, migra el mensaje inicial). Cachea 5 min; fallback a defaults.
- **Devuelve:** dict.

### `guardar_evaluacion_personal(nombre, respuestas)` — [notion_service.py:3389](../backend/notion_service.py#L3389)
- **Qué hace:** guarda un comentario de seguimiento personal (columna Comentario) como fila en la base "Respuestas" personal.
- **Devuelve:** bool éxito. **Efectos:** crea fila.

---

## Calendario de evaluaciones

### `_obtener_o_crear_bbdd_calendario()` — [notion_service.py:3423](../backend/notion_service.py#L3423)
- **Qué hace:** obtiene/crea la base "Calendario evaluaciones" (Nombre title, Fecha inicio date), preferentemente bajo "Listas de datos". Cachea.
- **Devuelve:** str db_id o None.

### `obtener_config_calendario()` — [notion_service.py:3484](../backend/notion_service.py#L3484)
- **Qué hace:** lee las fechas de inicio de ciclo del calendario y devuelve `{personal, proyecto_ca}` (YYYY-MM-DD o None), interpretando el nombre de cada fila (inicio/personal/proyecto/ca).
- **Devuelve:** dict.

### `siguiente_envio_calendario(fecha_inicio_str, semanas)` — [notion_service.py:3511](../backend/notion_service.py#L3511)
- **Qué hace:** dado un inicio y un intervalo en semanas, calcula el próximo momento de envío posterior a "ahora".
- **Devuelve:** datetime.
- **Notas:** usa `timedelta` que **no está importado** en el módulo — llamarla provocaría `NameError`. Posible bug / código en desuso.

### `obtener_comentarios_personales(nombre)` — [notion_service.py:3524](../backend/notion_service.py#L3524)
- **Qué hace:** devuelve los comentarios de evaluación personal escritos por un nombre (columna Nombre = autor), con fecha, page_id y url.
- **Devuelve:** list[dict].

### `evaluacion_proyecto_guardada_desde(evaluador_nombre, desde_ts)` — [notion_service.py:3558](../backend/notion_service.py#L3558)
- **Qué hace:** True si el evaluador guardó al menos una evaluación de proyecto desde el timestamp dado (recorre todas las bases de evaluados).
- **Devuelve:** bool. **Se usa desde:** recordatorios/estado del bot.

### `evaluacion_personal_guardada_desde(nombre, desde_ts)` — [notion_service.py:3578](../backend/notion_service.py#L3578)
- **Qué hace:** True si el usuario guardó al menos un comentario personal desde el timestamp dado.
- **Devuelve:** bool.

---

## MiddleOffice: cargos y relaciones

### `_obtener_o_crear_pagina_gestion_mo()` — [notion_service.py:3641](../backend/notion_service.py#L3641)
- **Qué hace:** localiza/crea la página "Gestión de MiddleOffice" dentro de "Listas de datos". Cachea.
- **Devuelve:** dict parent.

### `_obtener_o_crear_bbdd_mo(titulo, props, cache, filas_default)` — [notion_service.py:3669](../backend/notion_service.py#L3669)
- **Qué hace:** helper genérico que obtiene/crea una BD de MiddleOffice bajo la página de gestión, poblándola con `filas_default` (detecta automáticamente la columna title y la rich_text). Cachea.
- **Devuelve:** str db_id o None.

### `inicializar_bbdd_middleoffice()` — [notion_service.py:3719](../backend/notion_service.py#L3719)
- **Qué hace:** crea al arranque, si no existen, las BDs "Cargos de MiddleOffice" y "Relaciones de evaluaciones MiddleOffice" con sus datos por defecto.
- **Devuelve:** None. **Se usa desde:** inicialización de la app.

### `obtener_evaluados_middleoffice(evaluador_nombre, evaluador_aliases=None)` — [notion_service.py:3725](../backend/notion_service.py#L3725)
- **Qué hace:** devuelve la lista de personas que un evaluador puede evaluar en MiddleOffice, leyendo la base de relaciones (matching por nombre/alias, con y sin espacios).
- **Devuelve:** list[str].