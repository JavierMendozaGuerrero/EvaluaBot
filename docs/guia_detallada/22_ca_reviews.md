# `backend/ca_reviews.py` — Revisión del Career Advisor (Slack)

**Propósito:** Implementa el flujo completo de **revisión de advisees por parte del Career Advisor (CA)** a través de mensajes directos (DM) de Slack. El bot envía un DM privado a cada empleado que tenga advisees asignados; el CA responde en el hilo, elige un advisee, ve todas las evaluaciones recibidas por ese advisee desde la última revisión, opcionalmente pide un **resumen estructurado por competencias generado por Claude** (previo consentimiento explícito), redacta su opinión, la confirma/modifica y la guarda en Notion. Al terminar con un advisee se le ofrece hacer seguimiento de otro, hasta cubrir todos. Todo el módulo enfatiza la privacidad: "_Esta evaluación es totalmente privada, solo podrás verla tú._".

**Cómo arranca / hilos y scheduler:**
- El envío periódico lo gestiona [`ciclo_envio_ca()`](../backend/ca_reviews.py#L1101), pensado para ejecutarse en un hilo dedicado.
  - En modo **no producción** (`config.APP_MODE != "produccion"`): lanza un envío inmediato y luego repite cada `config.INTERVALO_PRUEBA_DIAS` días.
  - En **producción**: consulta el "Calendario evaluaciones" de Notion (`obtener_config_calendario()`), toma la fecha `proyecto_ca` y calcula el siguiente envío a 4 semanas con `siguiente_envio_calendario(fecha, 4)`; duerme hasta esa fecha y envía. Si no hay fecha configurada, reintenta cada hora.
- El ciclo de recordatorios lo gestiona [`ciclo_recordatorios_ca()`](../backend/ca_reviews.py#L1013), también pensado para un hilo dedicado: cada 30 s revisa las evaluaciones activas y reenvía recordatorio a quien lleve ≥ 1 semana sin completar (`_RECORDATORIO_CA_SEGUNDOS = 7 días`).
- Los handlers `@slack_app.action(...)` se registran al importar el módulo (decoradores sobre `slack_app`, importado de [`.clients`](../backend/ca_reviews.py#L17)).
- La lógica conversacional [`manejar_mensaje_ca(event, logger)`](../backend/ca_reviews.py#L573) se invoca desde `slack_bot.py` cuando llega un mensaje al hilo del DM.

**Estado en memoria:** (líneas [48–57](../backend/ca_reviews.py#L48)), todo protegido por `_lock = threading.Lock()`:
- `ca_dm_activas: set` — `user_id`s con evaluación CA activa.
- `ca_dm_ts: dict` — `user_id` → `ts` del mensaje inicial (raíz del hilo).
- `ca_dm_canal: dict` — `user_id` → id del canal DM.
- `ca_hora_dm: dict` — `user_id` → timestamp de envío del DM inicial.
- `ca_ultimo_recordatorio_dm: dict` — `user_id` → timestamp del último recordatorio.
- `conversaciones_ca: dict` — `user_id` → diccionario de estado de la conversación (contiene `modo`, `ca_nombre`, `advisee_actual`, `resumen_bruto`, `resumen_actual`, `resumen_claude_cache` (memo `{bruto, texto}` del último resumen de Claude, para no re-llamar a la API con el mismo texto), `opinion_actual`, `lista_advisees`, `advisees_guardados`, `campo_modificando`, etc.).
- `_cache_bbdd: dict` — cache título de BD → id de data source (evita recrear/buscar BD de opiniones).
- `_cache_nombre_usuario: dict` — cache `user_id` → nombre real (desde Notion).
- `_cache_lista_empleados: dict` — cache de lista de empleados (declarado; no se usa activamente en este archivo).

**Constantes clave:**
- `PREFIJO_BBDD = "Opiniones - "` — prefijo del título de la BD de opiniones por advisee.
- `_PALABRAS_NUMERO_CA` — mapa de palabras ("uno".."diez") a números 1–10, para resolver selección de advisee por número.
- `_PROPS_CA` — esquema de propiedades de la BD de opiniones: `Name` (title), `Fecha` (date), `CA` (rich_text), `Opinion` (rich_text), `Resumen` (rich_text).
- `_OPCIONES_MODIFICACION_CA` — mapea "1"/"advisee" → `advisee`, "2"/"opinion" → `opinion`.
- `_RECORDATORIO_CA_SEGUNDOS = 7 * 24 * 60 * 60` — 1 semana.

---

## Flujo de conversación (lo que ve el CA, paso a paso)

**1. Notificación inicial por DM** (enviada por `enviar_pregunta_inicial_ca`, [L522](../backend/ca_reviews.py#L522)). Fallback de texto: `"📋 CA: Tienes evaluación de advisees pendiente"`. Bloques:
> 📋 *CA: Tienes evaluación de advisees pendiente*
>
> _Esta evaluación es totalmente privada, solo podrás verla tú._
> _Si en algún momento quieres cancelar, escribe SOS en el hilo._

Seguido de una sección con botón **"Ver ejemplo"** (`action_id: ca_ver_ejemplo`):
> :point_right: Ejemplo:  [Ver ejemplo]

Y una sección final:
> :point_right: *Envía cualquier mensaje en el hilo para comenzar la evaluación*

(y un `divider`). El botón "Ver ejemplo" abre un modal con el ejemplo de guía para CA (ver `_build_ejemplo_ca_view` / `_handle_ca_ver_ejemplo`).

> 📷 **[Captura pendiente: DM inicial de notificación CA con botón "Ver ejemplo"]**
> 📷 **[Captura pendiente: modal "Ejemplo de guía — Evaluación CA"]**

**2. El CA envía cualquier mensaje en el hilo** → estado pasa de `pre_inicial` a `esperando_advisee`, acción `pedir_advisee`. El bot muestra la **lista de advisees pendientes como botones** (`_reply_lista_advisees`, [L717](../backend/ca_reviews.py#L717)):
> ¿De qué advisee te gustaría hacer seguimiento?  [Nombre1] [Nombre2] … [❌ Terminar]

Cada botón de advisee tiene `action_id: ca_advisee_{i}`; el de terminar es `ca_advisee_no`. Si ya se opinó sobre todos los advisees, en su lugar aparece:
> Ya has opinado sobre todos tus advisees. ¡Perfecto, gracias por tu tiempo! 🎉

> 📷 **[Captura pendiente: lista de advisees en botones con "❌ Terminar"]**

**3. Elección de advisee** (por botón o por texto/número). Se valida con `validar_y_mostrar` ([L762](../backend/ca_reviews.py#L762)):
- Si el advisee **no está en la lista de advisees** del CA:
> *{advisee}* no aparece en tu lista de advisees.
>
> Por favor, escribe el nombre o número correspondiente del advisee a evaluar. Si quieres terminar la evaluación escribe *no*
- Si es válido, se calcula la fecha "desde" (máximo entre la última opinión guardada y hace 4 semanas) y se muestra el **resumen de evaluaciones** del advisee (`_resumen_advisee`).

**4. Muestra de evaluaciones desde la última revisión** (`_resumen_advisee`, [L250](../backend/ca_reviews.py#L250)). Formato:
> *{advisee}* – N evaluaciones desde {fecha}:
> • [YYYY-MM-DD] *{quien evalúa}* en {proyecto} – Valoración: {q1} | Ejemplo: {q2}
> …
>
> *Comentarios personales (N):*
> • [fecha] *{autor}* → _{comentario}_
>
> 📌 *Objetivos de {advisee}:*
> • *{título objetivo}*
>   _KPIs: {kpis}_

Casos "sin novedades" (que saltan directo a ofrecer otro advisee):
> *{advisee}*: no hay evaluaciones nuevas desde tu última revisión ({fecha}).

o
> No hay evaluaciones registradas para *{advisee}*.

> 📷 **[Captura pendiente: resumen de evaluaciones + comentarios personales + objetivos del advisee]**

**5. Opción de resumen por Claude (consentimiento)** ([L795](../backend/ca_reviews.py#L795)). Si hay novedades, tras el resumen bruto se pregunta con botones (`blq_claude_{user_id}`):
> ¿Quieres un resumen estructurado por competencias generado por Claude?
> _Evitar el uso excesivo por favor._
>
> [Sí] [No]

Botones: `permiso_claude_si` (estilo primary) y `permiso_claude_no`. Este es el punto de **consentimiento explícito** antes de invocar a Claude.

> 📷 **[Captura pendiente: pregunta de consentimiento con botones Sí/No para resumen de Claude]**

- **Si "Sí"** (`llamar_claude`, [L895](../backend/ca_reviews.py#L895)): se llama a `generar_resumen_evaluacion(advisee, cargo, resumen_bruto)` y se muestra:
  (Para ahorrar API: si el estado ya tiene un `resumen_claude_cache` con el **mismo** `resumen_bruto` —p. ej. el CA vuelve atrás y reenvía— se reutiliza ese resumen y **no** se vuelve a llamar a Claude.)
> 📊 *Resumen generado por Claude:*
>
> {resumen_claude}
>
> ¿Qué opinas de esto?
  Si falla la generación:
> ⚠️ No se pudo generar el resumen con Claude.
>
> ¿Qué opinas de esto?

- **Si "No"** (`pedir_opinion_sin_claude`, [L912](../backend/ca_reviews.py#L912)):
> ¿Qué comentario deseas registrar sobre las evaluaciones de tu advisee?

- Si la respuesta no es sí/no (`aclarar_permiso_claude`):
> Responde `sí` para generar un resumen con Claude, o `no` para continuar directamente.

> 📷 **[Captura pendiente: "Resumen generado por Claude" mostrado en el hilo]**

**6. Redacción de opinión** → el texto del CA se guarda en `opinion_actual` y se pasa a confirmación.

**7. Resumen / confirmación** (`mostrar_confirmacion_ca`, [L829](../backend/ca_reviews.py#L829)):
> *Resumen de tu valoración:*
> • Advisee: *{advisee}*
> • Opinión: {opinion}
>
> Responde o haz click en sí para guardar en Notion o modificar para cambiar una respuesta concreta.
>
> [✅ Sí, guardar] [✏️ Modificar]

Botones: `ca_confirmar` y `ca_modificar`.

> 📷 **[Captura pendiente: tarjeta de confirmación con botones "Sí, guardar" y "Modificar"]**

**8. Modificación** (opcional). Si se pulsa Modificar (`pedir_modificacion_ca`, [L861](../backend/ca_reviews.py#L861)):
> *¿Qué respuesta quieres modificar?*
> [Advisee] [Opinión]

Botones: `mod_ca_1` (Advisee) y `mod_ca_2` (Opinión). Al elegir campo se pide el nuevo valor (`_texto_pregunta_ca_por_clave`): "¿Cuál es el nombre de tu advisee?" u "¿Qué opinas de las evaluaciones?". Tras introducir el nuevo valor se vuelve a la confirmación. Si se modifica el advisee y el nuevo nombre no existe o no está asociado al CA, se muestran mensajes de error con sugerencias / lista de advisees válidos.

> 📷 **[Captura pendiente: menú "¿Qué respuesta quieres modificar?" con botones Advisee/Opinión]**

**9. Guardado y siguiente advisee** (`guardar_y_preguntar_otro`, [L921](../backend/ca_reviews.py#L921)). Se revalida que el advisee esté asociado al CA en `Lista CA`; si no:
> No puedo guardar esta opinión: *{advisee}* no aparece asociado a ti en `Lista CA`.
> Tus advisees actuales:
> - …

Si guarda bien, se marca el advisee como guardado y se vuelve a mostrar la lista con prefijo:
> ✅ Opinión guardada en Notion.

Si falla:
> ⚠️ No se pudo guardar en Notion: `{error}`

Si el CA responde "no" a "¿otro advisee?" o pulsa "❌ Terminar":
> ¡Perfecto, gracias por tu tiempo! 🎉

Y si vuelve a escribir tras terminar (`ya_terminado`):
> Esta evaluación ya ha concluido. 👋

**10. Cancelación / SOS** ([L589](../backend/ca_reviews.py#L589)). En cualquier momento, escribir "SOS" cancela:
> Evaluación *cancelada* voluntariamente. Si quieres volver a empezar, escribe cualquier mensaje en este hilo.

(Se conservan los `advisees_guardados` para no repetir.) El botón "❌ Terminar" reinyecta el texto `"sos"` internamente.

**11. Recordatorios** (`ciclo_recordatorios_ca`). Tras ≥ 1 semana sin completar (y si el CA no ha guardado ya alguna opinión desde el envío):
> *📋 Recuerda realizar tu revisión de Career Advisor.* Abre el hilo del mensaje CA y responde.

> 📷 **[Captura pendiente: DM de recordatorio semanal CA]**

---

## Referencia de funciones

#### `_resolver_numero_advisee(texto, estado)`
- **Qué hace:** Si el texto es un número (dígito o palabra "uno".."diez") válido dentro del rango de `lista_advisees`, devuelve el nombre del advisee correspondiente; si no, devuelve el texto original.
- **Parámetros:** `texto` — str — entrada del usuario; `estado` — dict — estado de conversación (usa `lista_advisees`).
- **Devuelve:** str — nombre del advisee resuelto o el texto original.
- **Efectos:** ninguno (función pura de lectura del estado).
- **Se llama desde:** `manejar_mensaje_ca` en modos `esperando_advisee` y `esperando_otro` ([L618](../backend/ca_reviews.py#L618), [L710](../backend/ca_reviews.py#L710)).
- **Notas:** Usa `_PALABRAS_NUMERO_CA`.

#### `_asegurar_propiedades_ca(database_id)`
- **Qué hace:** Comprueba que la BD/data source de opiniones tenga todas las propiedades de `_PROPS_CA` y crea las que falten.
- **Parámetros:** `database_id` — str — id de la base de datos o data source.
- **Devuelve:** `None`.
- **Efectos (Notion):** `data_sources.retrieve/update` o `databases.retrieve/update` según `_usa_data_sources()`. Captura y loguea cualquier excepción.
- **Se llama desde:** `_obtener_o_crear_bbdd_ca` cuando reutiliza una BD existente ([L126](../backend/ca_reviews.py#L126)).
- **Notas:** Idempotente; solo añade propiedades faltantes.

#### `_obtener_o_crear_bbdd_ca(advisee)`
- **Qué hace:** Devuelve el id de la BD de opiniones del advisee (`"Opiniones - {advisee}"`), buscándola en Notion o creándola si no existe. Usa cache `_cache_bbdd`.
- **Parámetros:** `advisee` — str — nombre del advisee.
- **Devuelve:** str — id del data source de la BD de opiniones.
- **Efectos (Notion/estado):** `notion.search`, `databases.create`, `databases.retrieve`; escribe en `_cache_bbdd` bajo `_lock`. Usa `_parent_bbdd_referencia()` y `_parent_bbdd_en_pagina(config.NOTION_CA_TRACKING_PAGE_NAME, crear=True)` como parents. Loguea la creación.
- **Se llama desde:** `_guardar_opinion` ([L162](../backend/ca_reviews.py#L162)) y `_ca_guardo_desde` ([L225](../backend/ca_reviews.py#L225)).
- **Notas:** La creación difiere entre modo `data_sources` (con `initial_data_source`) y modo clásico.

#### `guardar_nota_ca_web(ca_nombre, advisee, nota)`
- **Qué hace:** Guarda una nota del CA sobre un advisee registrada **desde la web**. Simple envoltorio de `_guardar_opinion`.
- **Parámetros:** `ca_nombre` — str; `advisee` — str; `nota` — str — texto de la opinión.
- **Devuelve:** `tuple[bool, str]` — (éxito, mensaje de error).
- **Efectos (Notion):** los de `_guardar_opinion` (crea página en la BD de opiniones).
- **Se llama desde:** capa web / API (fuera de este archivo).
- **Notas:** No pasa `resumen`, así que este queda vacío.

#### `_guardar_opinion(ca_nombre, advisee, opinion, resumen="")`
- **Qué hace:** Crea una página en la BD de opiniones del advisee con la opinión y el resumen.
- **Parámetros:** `ca_nombre` — str; `advisee` — str; `opinion` — str; `resumen` — str (opcional) — resumen (bruto o de Claude).
- **Devuelve:** `tuple[bool, str]` — (True, "") si va bien; (False, mensaje) si falla.
- **Efectos (Notion):** `_obtener_o_crear_bbdd_ca` + `_crear_pagina_en_bbdd` con `Name="Opinion {fecha}"`, `Fecha` (UTC ISO), `CA`, `Opinion` (truncada a 2000), `Resumen` (truncado a 2000). Fecha visible en zona Madrid.
- **Se llama desde:** `guardar_nota_ca_web` y `manejar_mensaje_ca` (acción `guardar_y_preguntar_otro`, [L934](../backend/ca_reviews.py#L934)).
- **Notas:** Trunca opinión y resumen a 2000 caracteres.

#### `_fecha_ultima_opinion(ca_nombre, advisee)`
- **Qué hace:** Devuelve la fecha (ISO) de la última opinión que ese CA guardó sobre ese advisee, o `None`.
- **Parámetros:** `ca_nombre` — str; `advisee` — str.
- **Devuelve:** `str | None` — fecha máxima o `None`.
- **Efectos (Notion):** `notion.search` para hallar la BD y `_query_bbdd` (hasta 100 filas). Filtra por CA comparando nombres normalizados; acepta propiedad `CA` o `Evaluador` como fallback.
- **Se llama desde:** `manejar_mensaje_ca` (acción `validar_y_mostrar`, [L778](../backend/ca_reviews.py#L778)) y `obtener_resumen_advisee_para_ca` ([L1055](../backend/ca_reviews.py#L1055)).
- **Notas:** Loguea y devuelve `None` ante error.

#### `_ca_guardo_desde(ca_nombre, desde_ts)`
- **Qué hace:** True si el CA guardó al menos una opinión en Notion desde el timestamp dado (para dejar de recordar).
- **Parámetros:** `ca_nombre` — str; `desde_ts` — float — timestamp UNIX de referencia.
- **Devuelve:** `bool`.
- **Efectos (Notion):** recorre `obtener_advisees(ca_nombre)`, obtiene cada BD con `_obtener_o_crear_bbdd_ca` y consulta filas con `_query_bbdd`; compara fecha (`>=`) y CA (normalizado).
- **Se llama desde:** `ciclo_recordatorios_ca` ([L1036](../backend/ca_reviews.py#L1036)).
- **Notas:** Devuelve `False` ante excepción.

#### `_resumen_advisee(advisee, desde_fecha)`
- **Qué hace:** Construye el texto (mrkdwn) del resumen de evaluaciones del advisee desde `desde_fecha`, incluyendo comentarios personales y objetivos.
- **Parámetros:** `advisee` — str; `desde_fecha` — `str | None` — fecha ISO límite inferior.
- **Devuelve:** str — resumen formateado o mensaje de "sin novedades" / "sin evaluaciones" / "error".
- **Efectos (Notion):** `obtener_evaluaciones_por_evaluado`, `obtener_comentarios_personales`, `obtener_objetivos_persona`. Filtra por fecha si hay `desde_fecha`.
- **Se llama desde:** `manejar_mensaje_ca` (`validar_y_mostrar`, [L781](../backend/ca_reviews.py#L781)) y `obtener_resumen_advisee_para_ca` ([L1058](../backend/ca_reviews.py#L1058)).
- **Notas:** Los mensajes de "sin novedades" son detectados por texto en el llamador (`sin_novedades`).

#### `_es_si(texto)`
- **Qué hace:** True si el texto normalizado es afirmativo (`si`, `sí`, `s`, `yes`, `y`, `claro`, `sip`, `vale`).
- **Parámetros:** `texto` — str.
- **Devuelve:** `bool`.
- **Se llama desde:** modo `esperando_permiso_claude` ([L623](../backend/ca_reviews.py#L623)).

#### `_es_no(texto)`
- **Qué hace:** True si el texto normalizado es negativo (`no`, `n`, `nope`, `nel`).
- **Parámetros:** `texto` — str.
- **Devuelve:** `bool`.
- **Se llama desde:** varios modos de `manejar_mensaje_ca` (para terminar/cancelar).

#### `_es_confirmar(texto)`
- **Qué hace:** True si el texto normalizado confirma (`si`, `sí`, `s`, `ok`, `okay`, `confirmar`, `guardar`, `correcto`).
- **Parámetros:** `texto` — str.
- **Devuelve:** `bool`.
- **Se llama desde:** modo `confirmacion_ca` ([L647](../backend/ca_reviews.py#L647)).

#### `_es_modificar(texto)`
- **Qué hace:** True si el texto normalizado pide modificar (`modificar`, `cambiar`, `editar`, `repetir`).
- **Parámetros:** `texto` — str.
- **Devuelve:** `bool`.
- **Se llama desde:** modo `confirmacion_ca` ([L650](../backend/ca_reviews.py#L650)).

#### `_texto_menu_modificacion_ca()`
- **Qué hace:** Devuelve el texto del menú de modificación ("¿Qué respuesta quieres modificar? 1. Advisee 2. Opinión…").
- **Devuelve:** str.
- **Se llama desde:** `pedir_valor_modificacion_ca` como fallback ([L893](../backend/ca_reviews.py#L893)).

#### `_bloques_menu_modificacion_ca()`
- **Qué hace:** Construye los bloques Slack del menú de modificación con botones **Advisee** (`mod_ca_1`) y **Opinión** (`mod_ca_2`).
- **Devuelve:** `list` — bloques Slack.
- **Se llama desde:** acción `pedir_modificacion_ca` ([L865](../backend/ca_reviews.py#L865)).

#### `_clave_modificacion_ca(texto)`
- **Qué hace:** Traduce texto/número a la clave de campo (`advisee` / `opinion`) usando `_OPCIONES_MODIFICACION_CA`.
- **Parámetros:** `texto` — str.
- **Devuelve:** `str | None`.
- **Se llama desde:** modo `seleccionando_modificacion_ca` ([L663](../backend/ca_reviews.py#L663)).

#### `_texto_pregunta_ca_por_clave(clave)`
- **Qué hace:** Devuelve la pregunta a mostrar según el campo a modificar ("¿Cuál es el nombre de tu advisee?" / "¿Qué opinas de las evaluaciones?" / genérica).
- **Parámetros:** `clave` — str.
- **Devuelve:** str.
- **Se llama desde:** acción `pedir_valor_modificacion_ca` ([L893](../backend/ca_reviews.py#L893)).

#### `_mensaje_advisee_no_encontrado(nombre)`
- **Qué hace:** Genera un mensaje de error para cuando el advisee no aparece en la lista de empleados, incluyendo sugerencias de nombres parecidos si las hay.
- **Parámetros:** `nombre` — str.
- **Devuelve:** str.
- **Efectos (Notion):** `sugerir_empleados_parecidos`.
- **Se llama desde:** definido pero no invocado dentro de este archivo (helper disponible para el flujo/otros módulos).
- **Notas:** El flujo interactivo genera mensajes de error similares inline en `validar_y_mostrar` y `pedir_valor_modificacion_ca`.

#### `_nombre_desde_notion(user_id)`
- **Qué hace:** Busca en la BD "Lista de empleados" el nombre asociado a un `ID_usuario` de Slack. Usa cache `_cache_nombre_usuario`.
- **Parámetros:** `user_id` — str — id de Slack.
- **Devuelve:** `str | None`.
- **Efectos (Notion/estado):** `notion.search` + `_query_bbdd`; escribe en cache bajo `_lock`.
- **Se llama desde:** `_nombre_real` e `_identidad_usuario_slack`.
- **Notas:** Loguea y devuelve `None` ante error.

#### `_nombre_real(user_id, logger)`
- **Qué hace:** Devuelve el nombre real del usuario: primero desde Notion; si no, desde `users_info` de Slack; si todo falla, el propio `user_id`.
- **Parámetros:** `user_id` — str; `logger` — logger.
- **Devuelve:** str.
- **Efectos (Slack):** `users_info`.
- **Se llama desde:** definido pero no invocado dentro de este archivo (helper disponible; el flujo usa `_identidad_usuario_slack`).

#### `_identidad_usuario_slack(user_id, logger)`
- **Qué hace:** Devuelve `(nombre, aliases)` del CA: nombre canónico (preferido de Notion) y lista de alias únicos (user_id, nombre Notion, real_name, name, display_name, email de Slack), para comparar contra `Lista CA`.
- **Parámetros:** `user_id` — str; `logger` — logger.
- **Devuelve:** `tuple[str, list[str]]`.
- **Efectos (Slack/Notion):** `_nombre_desde_notion` + `users_info`.
- **Se llama desde:** `enviar_pregunta_inicial_ca`, `manejar_mensaje_ca` (varias acciones), `_reply_lista_advisees`.
- **Notas:** Normaliza y elimina alias duplicados con `normalizar_nombre`.

#### `_advisee_permitido_para_ca(ca_nombre, ca_aliases, advisee)`
- **Qué hace:** Comprueba si un advisee está entre los advisees permitidos del CA (según `Lista CA`), comparando nombres normalizados.
- **Parámetros:** `ca_nombre` — str; `ca_aliases` — `list[str]`; `advisee` — str.
- **Devuelve:** `tuple[bool, list[str]]` — (permitido, lista de advisees permitidos).
- **Efectos (Notion):** `obtener_advisees(ca_nombre, ca_aliases=...)`.
- **Se llama desde:** `manejar_mensaje_ca` en `validar_y_mostrar`, modificación de advisee y `guardar_y_preguntar_otro`.

#### `enviar_pregunta_inicial_ca()`
- **Qué hace:** Envía el DM inicial de revisión CA a todos los empleados que tengan advisees (o solo al usuario de prueba fuera de producción). Inicializa el estado por usuario.
- **Parámetros:** ninguno.
- **Devuelve:** `None`.
- **Efectos (Slack/Notion/estado):** limpia `ca_dm_activas`; para cada usuario: `_identidad_usuario_slack`, `obtener_advisees`, `conversations_open`, `chat_postMessage` (mensaje inicial con botón "Ver ejemplo"); rellena `ca_dm_activas/ca_dm_canal/ca_dm_ts/ca_hora_dm` y resetea `conversaciones_ca` bajo `_lock`.
- **Se llama desde:** `ciclo_envio_ca` ([L1104](../backend/ca_reviews.py#L1104), [L1110](../backend/ca_reviews.py#L1110), [L1127](../backend/ca_reviews.py#L1127)).
- **Notas:** Omite usuarios sin advisees. En modo prueba usa `config.SLACK_TEST_USER_ID`.

#### `manejar_mensaje_ca(event, logger)`
- **Qué hace:** **Máquina de estados principal** de la conversación CA. Interpreta el mensaje del CA según el `modo` actual, actualiza el estado y ejecuta la acción resultante (mostrar lista, validar advisee, pedir permiso a Claude, llamar a Claude, confirmar, modificar, guardar, terminar…).
- **Parámetros:** `event` — dict — evento Slack (`user`, `thread_ts`, `channel`, `text`); `logger` — logger.
- **Devuelve:** `None`.
- **Efectos (Slack/Notion/Claude/estado):** múltiples `chat_postMessage`; llama a `generar_resumen_evaluacion` (Claude) en `llamar_claude`; `_guardar_opinion` (Notion) al confirmar; lee/escribe `conversaciones_ca` bajo `_lock`. Ignora usuarios no activos. Gestiona el atajo "SOS".
  - **Modos:** `pre_inicial` → `esperando_advisee` → `esperando_permiso_claude` → `esperando_opinion` → `confirmacion_ca` → (`seleccionando_modificacion_ca` → `modificando_respuesta_ca`) → `esperando_otro` → `terminado`.
  - **Función anidada `reply(text)`:** publica un mensaje en el hilo.
  - **Función anidada `_reply_lista_advisees(prefijo="")`:** recalcula advisees pendientes (excluye `advisees_guardados`), guarda `lista_advisees`, y muestra la lista de botones o cierra si no quedan.
- **Se llama desde:** `slack_bot.py` (mensajes de hilo) y los action handlers de este archivo (que reinyectan eventos sintéticos).
- **Notas:** La detección de "sin novedades" se basa en el texto del resumen. El consentimiento para Claude se materializa en el modo `esperando_permiso_claude`.

#### `_handle_ca_elegir_advisee(ack, body, client, logger)` — `@slack_app.action(r"^ca_advisee_\d+$")`
- **Qué hace:** Handler de los botones de selección de advisee. Actualiza el mensaje a "Advisee: *X* ✅" y reinyecta el nombre elegido como evento a `manejar_mensaje_ca`.
- **Parámetros:** `ack`, `body`, `client`, `logger` (Bolt).
- **Devuelve:** `None`.
- **Efectos (Slack):** `ack()`, `chat_update`, luego `manejar_mensaje_ca`.
- **Se llama desde:** Slack (interacción de botón).

#### `_handle_ca_advisee_no(ack, body, client, logger)` — `@slack_app.action("ca_advisee_no")`
- **Qué hace:** Handler del botón "❌ Terminar". Marca el mensaje como "❌ Terminado" y reinyecta el texto `"sos"` (cancela/termina).
- **Parámetros:** `ack`, `body`, `client`, `logger`.
- **Devuelve:** `None`.
- **Efectos (Slack):** `ack()`, `chat_update`, `manejar_mensaje_ca` con `text="sos"`.
- **Se llama desde:** Slack.
- **Notas:** Reutiliza la rama SOS que conserva los advisees ya guardados.

#### `ciclo_recordatorios_ca()`
- **Qué hace:** Bucle infinito que cada 30 s reenvía un recordatorio por DM a los CAs con evaluación activa que llevan ≥ 1 semana sin completar y que aún no han guardado ninguna opinión desde el envío.
- **Parámetros:** ninguno.
- **Devuelve:** `None` (nunca retorna).
- **Efectos (Slack/Notion/estado):** `obtener_nombre_por_id_usuario` / `users_info` para el nombre; `_ca_guardo_desde` para saber si ya completó (si sí, `discard` de `ca_dm_activas`); `chat_postMessage` con el recordatorio; actualiza `ca_ultimo_recordatorio_dm`.
- **Se llama desde:** hilo de arranque (fuera de este archivo).
- **Notas:** Selecciona pendientes usando el máximo entre hora de envío y último recordatorio.

#### `obtener_resumen_advisee_para_ca(ca_nombre, advisee)`
- **Qué hace:** Devuelve `(resumen_texto, sin_novedades)` de un advisee, con la misma lógica de fecha ("desde" = máx. entre última opinión y hace 4 semanas). **Para uso desde la web.**
- **Parámetros:** `ca_nombre` — str; `advisee` — str.
- **Devuelve:** `tuple[str, bool]`.
- **Efectos (Notion):** `_fecha_ultima_opinion` + `_resumen_advisee`.
- **Se llama desde:** capa web / API (fuera de este archivo).

#### `_build_ejemplo_ca_view()`
- **Qué hace:** Construye la vista modal Slack con el ejemplo de guía para CA (obtenido de `obtener_ejemplos_guia()["CA"]`).
- **Parámetros:** ninguno.
- **Devuelve:** `dict` — vista modal (`callback_id: ejemplo_ca_ver`).
- **Efectos (Notion):** `obtener_ejemplos_guia`.
- **Se llama desde:** `_handle_ca_ver_ejemplo`.
- **Notas:** Trunca el ejemplo a 3000 caracteres.

#### `_handle_ca_ver_ejemplo(ack, body, logger)` — `@slack_app.action("ca_ver_ejemplo")`
- **Qué hace:** Handler del botón "Ver ejemplo": abre el modal con el ejemplo de guía CA.
- **Parámetros:** `ack`, `body`, `logger`.
- **Devuelve:** `None`.
- **Efectos (Slack):** `ack()`, `views_open` con `_build_ejemplo_ca_view()`.
- **Se llama desde:** Slack.

#### `ciclo_envio_ca()`
- **Qué hace:** Bucle de programación del envío de la revisión CA (modo prueba: intervalo fijo; producción: calculado sobre el "Calendario evaluaciones" de Notion, 4 semanas desde `proyecto_ca`).
- **Parámetros:** ninguno.
- **Devuelve:** `None` (bucle infinito).
- **Efectos (Notion/Slack):** `obtener_config_calendario`, `siguiente_envio_calendario`, `enviar_pregunta_inicial_ca`; `time.sleep`.
- **Se llama desde:** hilo de arranque (fuera de este archivo).
- **Notas:** Si falta la fecha en Notion, reintenta cada hora.

#### `_handle_ca_confirmar(ack, body, logger)` — `@slack_app.action("ca_confirmar")`
- **Qué hace:** Handler del botón "✅ Sí, guardar": reinyecta `text="sí"` a `manejar_mensaje_ca`.
- **Parámetros:** `ack`, `body`, `logger`.
- **Devuelve:** `None`.
- **Efectos (Slack):** `ack()`, `manejar_mensaje_ca`.
- **Se llama desde:** Slack.

#### `_handle_ca_modificar(ack, body, logger)` — `@slack_app.action("ca_modificar")`
- **Qué hace:** Handler del botón "✏️ Modificar": reinyecta `text="modificar"` a `manejar_mensaje_ca`.
- **Parámetros:** `ack`, `body`, `logger`.
- **Devuelve:** `None`.
- **Efectos (Slack):** `ack()`, `manejar_mensaje_ca`.
- **Se llama desde:** Slack.

#### `_handle_mod_ca_opcion(ack, body, logger)` — `@slack_app.action(r"^mod_ca_\d+$")`
- **Qué hace:** Handler de los botones del menú "¿Qué respuesta quieres modificar?": toma el `value` del botón (número de campo) y lo reinyecta a `manejar_mensaje_ca`.
- **Parámetros:** `ack`, `body`, `logger`.
- **Devuelve:** `None`.
- **Efectos (Slack):** `ack()`, `manejar_mensaje_ca`.
- **Se llama desde:** Slack.

#### `_handle_permiso_claude(ack, body, client, logger)` — `@slack_app.action(r"^permiso_claude_(si|no)$")`
- **Qué hace:** Handler de los botones **Sí/No de consentimiento para el resumen de Claude**. Actualiza el mensaje al texto seleccionado ("✅ Sí, generar resumen con Claude" / "❌ No, continuar sin resumen") y reinyecta `text="sí"/"no"` a `manejar_mensaje_ca`.
- **Parámetros:** `ack`, `body`, `client`, `logger`.
- **Devuelve:** `None`.
- **Efectos (Slack):** `ack()`, `chat_update`, `manejar_mensaje_ca`.
- **Se llama desde:** Slack.
- **Notas:** Es el disparador interactivo del **consentimiento** antes de invocar a Claude.