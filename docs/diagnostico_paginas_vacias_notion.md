# Diagnóstico: páginas vacías/duplicadas en Notion

**Fecha:** 2026-07-13 · **Fase:** 2 aplicada (ver sección final "Arreglo aplicado")

## Resumen ejecutivo

Todos los objetos basura encontrados los crea **nuestro propio código** (no hay
automatizaciones nativas de Notion ni webhooks implicados). La causa raíz es común:

> Los flujos "buscar-o-crear" del bot deduplican usando `notion.search` (búsqueda
> global de la API), y **la búsqueda de Notion NO devuelve las bases de datos que se
> migraron al workspace de empresa el 06/07/2026** (una semana después siguen sin
> estar indexadas), aunque sí son accesibles por `blocks.children.list`. Resultado:
> el bot no "ve" la BD original, cree que no existe y crea una copia vacía al lado.

Agravantes:
- Las cachés de IDs son **en memoria** → cada reinicio del bot vuelve a pasar por
  búsqueda+creación.
- Varios getters **no mantienen el lock durante la búsqueda+creación** → dos hilos
  concurrentes (el arranque lanza 8 hilos, y la web dispara peticiones en paralelo)
  pueden crear dos BDs a la vez.
- En un punto, los errores de `notion.search` se tragan con `except: pass`
  ([notion_service.py:4324](../backend/notion_service.py#L4324)): un 429 (rate limit)
  durante la ráfaga de arranque se interpreta como "no existe" → se crea duplicado.

## Evidencia en vivo (API de Notion, solo lectura)

Verificado hoy consultando el workspace con el token del bot:

| Objeto | Original (migración 06/07) | Duplicado creado por el bot |
|---|---|---|
| BD "Acceso CA" | 06/07 16:42, en "Activaciones de permisos" (`c75a294d…`) | **13/07 09:49**, misma página (`a759c2b8…`), vacía |
| BD "Relaciones de evaluaciones MiddleOffice" | 06/07 16:43, en "Gestión de MiddleOffice", **con filas reales de admins** | **13/07 09:19**, misma página, con filas por defecto |
| BD "Calendario evaluaciones" | *(no se migró: no está entre los hijos del 06/07)* | **13/07 09:19**, en "Datos a Monitorizar", vacía |
| BD "Log evaluacion anual asistida" | — | 09/07 06:51, **colgada de la página raíz** |
| BD "Evaluaciones" ×2 bajo la misma página de proyecto | — | 08/07 13:51 **las dos en el mismo minuto** (carrera entre hilos) |

Dato clave: `POST /v1/search?query="Acceso CA"` devuelve **solo** el duplicado del
13/07; la original del 06/07 no aparece en los resultados. Eso confirma que el
fallo no es del matching de títulos sino de la propia búsqueda de Notion con
contenido migrado.

## Caso 1 — "Calendario evaluaciones"

**Quién la crea:** `_obtener_o_crear_bbdd_calendario`
([notion_service.py:4295](../backend/notion_service.py#L4295)).

**Cuándo se dispara:** al arrancar el bot la llaman 4 hilos planificadores
(`slack_bot.py:203`, `ca_reviews.py:1634`, `personal_eval.py:1256`,
`eval_tracking.py:117`) y también peticiones web
(`api/routers/personal_slack.py:63`).

**Por qué aparece "de vez en cuando":** esta BD **no es basura** — es la BD de
configuración donde los admins deben poner las filas con "Fecha inicio" para que el
bot sepa cuándo enviar evaluaciones. No se migró al workspace nuevo el 06/07, así
que el bot la crea (vacía) en cada arranque en que no la encuentra. Si alguien la
borra pensando que sobra, el siguiente reinicio la recrea → parece "espontánea".
La única del workspace ahora mismo es la creada hoy a las 09:19 (coincide al minuto
con otro duplicado → fue un arranque del bot).

Este flujo ya fue endurecido el 03/07 (commit `cef32e9`: lock dedicado + escaneo de
hijos de "Datos a Monitorizar" antes de buscar globalmente), por eso **no** hay
duplicados dentro de esa página. Vector residual de duplicado: si la resolución de
"Datos a Monitorizar" falla puntualmente, el parent cae a la página raíz y el
fallback de `notion.search` puede fallar en silencio (`except: pass`) → crearía una
copia en la raíz.

**Conclusión:** causa identificada. Acción de fondo: rellenar la BD (fila "Inicio" +
fecha) y no borrarla; endurecer el `except: pass`.

## Caso 2 — "Acceso CA"

> **Obsoleto:** el acceso global por CA se eliminó de la web (solo queda el acceso
> individual por advisee), y con él `_obtener_o_crear_bbdd_acceso_ca`, `_acceso_ca_fila`,
> `ca_tiene_acceso_activo` y `toggle_acceso_advisees`. Ya no se crea la BD "Acceso CA".
> Se conserva este caso como registro del diagnóstico.

**Quién la crea:** `_obtener_o_crear_bbdd_acceso_ca`
([notion_service.py:3426](../backend/notion_service.py#L3426)).

**Cuándo se dispara:** bajo demanda desde la web — cada vez que se comprueba o
cambia el permiso de un CA (`api/routers/reports.py:58,182`, `api/files.py:104`,
`api/routers/ca.py:88,96`).

**Por qué se queda en blanco:** su deduplicación depende **solo** de
`notion.search`, sin escaneo de hijos ni lock durante búsqueda+creación (el `lock`
solo protege la caché). Como la búsqueda no devuelve la BD original migrada, la
primera petición web tras un reinicio creó hoy (09:49) una copia vacía junto a la
original. Matiz: la copia está dentro de **"Activaciones de permisos"** (TO-SEE),
no de "Datos a Monitorizar"; lo que se ve es una BD sin filas, que en Notion parece
una página en blanco.

**Riesgo funcional añadido:** a partir de ahora la búsqueda sí devuelve el
duplicado (el contenido creado por API sí se indexa), así que el bot leerá/escribirá
los permisos en la copia vacía. Hoy ambas copias tienen 0 filas, así que aún no hay
divergencia, pero cualquier toggle irá a la nueva.

**Conclusión:** causa identificada y reproducida con evidencia.

## Otros puntos detectados (no los conocíais)

1. **"Relaciones de evaluaciones MiddleOffice" — el más urgente.** Duplicada hoy
   09:19 por `_obtener_o_crear_bbdd_mo`
   ([notion_service.py:4538](../backend/notion_service.py#L4538), mismo patrón
   búsqueda-global). La original tiene la configuración real de los admins
   (evaluadores Natalia Vega, Alicia Sardina…); el duplicado tiene filas por defecto
   y **es el que el bot usa ahora** → las evaluaciones MO están leyendo relaciones
   equivocadas. Split-brain activo de datos, no solo estética.
2. ~~**"Log evaluacion anual asistida" colgada de la página raíz**~~ — **RESUELTO el
   15/07.** `_obtener_o_crear_bbdd_sesiones_anual` usaba `_parent_bbdd_referencia()`
   (la raíz) cuando `arquitectura.md` ya la situaba bajo TO-SEE; ahora usa
   `_parent_bbdd_en_pagina(config.NOTION_TOSEE_PAGE_NAME, crear=False)`
   ([notion_service.py:1507](../backend/notion_service.py#L1507)).

   El split-brain estaba activo: 14 filas en el duplicado de la raíz y 21 en el
   original de TO-SEE. Ambas resultaron ser **solo datos de prueba** (`CA=javireneclaude`,
   "esto es una prueba", "hola"), así que se archivaron las 21 filas y el duplicado
   de la raíz se mandó a la papelera en vez de fusionar. Se volcaron las 35 filas a
   JSON antes de borrar; la copia **no se versiona** (lleva texto de evaluación sobre
   empleados con nombre) y quedó en el equipo de Javier, fuera del repo. Todo lo
   archivado sigue además recuperable desde la papelera de Notion.

   **Ojo para el resto de casos de esta lista:** `notion.databases.update(archived=True)`
   **no archiva y no lanza error** — un no-op silencioso. Hay que usar `in_trash=True`.
   Y `pages.update` sobre el id de una BD da `ObjectNotFound`.

   Sigue pendiente el fallo de fondo: `_coincide_parent_bbdd`
   ([notion_service.py:73](../backend/notion_service.py#L73)) devuelve `True` sin
   comprobar el padre cuando el objeto es un `data_source` — y con notion-client
   3.1.0 siempre lo es. O sea, el filtro por padre del fallback de `notion.search`
   hoy no filtra nada, en este caso y en todos los demás de esta lista.
3. **Carrera en BDs "Evaluaciones" por proyecto/persona:** dos BDs idénticas creadas
   el 08/07 a las 13:51 bajo la misma página. En
   `_obtener_o_crear_bbdd_evaluacion_proyecto`
   ([project_evals.py:289](../backend/project_evals.py#L289)) la búsqueda+creación
   queda fuera del lock; `obtener_o_crear_bbdd_evaluado`
   ([notion_service.py:1271](../backend/notion_service.py#L1271)) tiene el mismo
   problema y además depende de `notion.search`.
4. **Mismo patrón frágil (aún sin síntoma visible)** en "Acceso Individual Advisee"
   ([notion_service.py:3520](../backend/notion_service.py#L3520)) e
   "Informes Finales" ([notion_service.py:3626](../backend/notion_service.py#L3626)):
   duplicarán en el próximo fallo de búsqueda.
5. **Arranque (`aplicar_estetica_notion`,
   [notion_service.py:545](../backend/notion_service.py#L545)):** crea en la raíz
   las páginas "Resultados Evaluaciones Mensuales" y "Resultados Evaluaciones CA"
   si no las encuentra (no están en la lista `_PAGINAS_SOLO_SI_EXISTEN`); la
   búsqueda jerárquica solo mira 2 niveles bajo TO-DO/TO-SEE, así que mover páginas
   de sitio provocaría páginas vacías nuevas en la raíz.
6. **Descartado:** "Frecuencia evaluaciones" (creada hoy 08:31 en Datos a
   Monitorizar) no aparece en el código → la creó una persona a mano. No es basura
   del bot. Tampoco hay automatizaciones nativas de Notion ni webhooks: todos los
   objetos anómalos coinciden al minuto con arranques/peticiones del bot.

## Propuesta de arreglo (Fase 2, pendiente de confirmación)

1. **Regla general:** deduplicar primero con `blocks.children.list` de la página
   padre esperada (consistencia inmediata) y dejar `notion.search` solo como
   fallback; mantener el lock durante toda la búsqueda+creación; si la búsqueda
   falla con excepción, **no crear** (propagar/reintentar), nunca tratar el error
   como "no existe". Aplicarlo a: Acceso CA, Acceso Individual Advisee, Informes
   Finales, BDs de MiddleOffice, Log anual, Evaluaciones por proyecto/persona.
2. **Calendario:** ya tiene el patrón bueno; solo endurecer el `except: pass` del
   fallback y, operativamente, rellenar la BD con las fechas.
3. **No se borra nada:** decidir a mano qué copia conservar en cada duplicado
   (especialmente urgente en "Relaciones de evaluaciones MiddleOffice", donde la
   copia buena es la original del 06/07).

Nota operativa: si el bot (`bot.py`) y el backend de desarrollo (`run_backend.ps1`)
corren a la vez, son dos procesos con cachés y locks independientes; los locks no
protegen entre procesos. El escaneo de hijos como primera comprobación mitiga
también ese caso.

## Arreglo aplicado (Fase 2, 2026-07-13)

Patrón aplicado a cada getter frágil: **(1)** escanear los hijos de la página padre
esperada con `blocks.children.list` (consistencia inmediata) antes de recurrir a
`notion.search`; **(2)** mantener el lock durante TODA la búsqueda+creación;
**(3)** si la búsqueda global falla con excepción, propagar/abortar en vez de crear
a ciegas. Getters modificados:

| Getter | Archivo | Cambio |
|---|---|---|
| `_obtener_o_crear_bbdd_acceso_ca` | notion_service.py | escaneo de "Activaciones de permisos" + lock dedicado (función ya eliminada, ver Caso 2) |
| `_obtener_o_crear_bbdd_acceso_individual` | notion_service.py | escaneo de "Activaciones de permisos" y raíz + lock |
| `_obtener_o_crear_bbdd_informes_finales` | notion_service.py | escaneo de la raíz + lock |
| `_obtener_o_crear_bbdd_sesiones_anual` | notion_service.py | escaneo de la raíz + lock dedicado |
| `_obtener_o_crear_bbdd_mo` | notion_service.py | escaneo de "Gestión de MiddleOffice" + lock (encuentra la ORIGINAL, primera en la página) |
| `obtener_o_crear_bbdd_evaluado` | notion_service.py | escaneo de "Resultados Evaluaciones Mensuales" + lock |
| `_obtener_o_crear_bbdd_calendario` | notion_service.py | el `except: pass` del fallback ya no crea tras un error de búsqueda |
| `_obtener_o_crear_bbdd_ca` | ca_reviews.py | escaneo de "Resultados Evaluaciones CA" + lock |
| `_obtener_o_crear_bbdd_evaluacion_proyecto` / `_obtener_o_crear_bbdd_evals_proyecto` | project_evals.py | búsqueda+creación dentro del lock (cierra la carrera del 08/07 13:51) |

**Verificación** (script de solo lectura con `databases.create`/`pages.create`
bloqueados, contra el workspace real): los 5 getters clave resolvieron la BD
correcta sin intentar crear nada — Acceso CA → original `c75a294d`, Relaciones MO
→ original `7a4a294d` (la de los admins), Acceso Individual → `6d6a294d`, Log anual
→ `9370ea7b`, Calendario → `07a31f78`. Suite de tests: 70/70 pasan.

**No tocado a propósito:** `aplicar_estetica_notion` (arranque) y los getters de
evaluación personal (ya usaban escaneo de hijos). No se ha borrado ni modificado
ninguna página/BD existente en Notion.

**Pendiente (decisión manual del usuario):**
1. Borrar los duplicados vacíos: "Acceso CA" del 13/07 (`a759c2b8…`), "Relaciones
   de evaluaciones MiddleOffice" del 13/07 (`dbc6e9f6…`). El bot ya no los usa.
2. Rellenar "Calendario evaluaciones" (fila con "Fecha inicio") y no borrarla.
3. Reiniciar el bot en producción: el proceso en marcha aún tiene en caché los
   ids de los duplicados de esta mañana.
