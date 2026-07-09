# `backend/slack_bot.py` — Evaluación mensual de proyecto (Slack)

**Propósito:** Gestiona por completo la **evaluación mensual de proyecto** que los empleados realizan a través de mensajes directos (DM) de Slack. Cubre el envío inicial de la notificación, la conversación guiada por máquina de estados (elección de área, situación en proyecto o barbecho, elección de proyecto y de persona, preguntas por área y por relación jerárquica, resumen, confirmación y modificación), el guardado en Notion, la ventana de modificación de 2 días (grace period) y el scheduler de envíos y recordatorios.

Además actúa de **router** de mensajes: si el hilo en el que llega el mensaje corresponde a una revisión CA o a una evaluación personal, delega en los módulos `ca_reviews` / `personal_eval`. La lógica de evaluación mensual de proyecto es la única implementada íntegramente en este archivo.

**Cómo arranca / qué hilos lanza:**
- El punto de entrada del modo Socket es [`start_socket_mode()`](../backend/slack_bot.py#L2097), que arranca `SocketModeHandler(slack_app, config.SLACK_APP_TOKEN)`.
- El scheduler de envíos se lanza con [`enviar_evaluaciones_programadas()`](../backend/slack_bot.py#L133): en modo distinto de producción llama a [`enviar_evaluaciones_modo_prueba()`](../backend/slack_bot.py#L116) (envía una vez y luego cada `INTERVALO_PRUEBA_DIAS` días); en producción entra en un bucle infinito que consulta el calendario de Notion y espera hasta el próximo envío (intervalo fijo de 4 semanas).
- El bucle de recordatorios es [`ciclo_recordatorios_proyecto()`](../backend/slack_bot.py#L2065): duerme 30 s en cada iteración y envía recordatorios a quien lleve más de 1 semana sin completar la evaluación.
- Los handlers de eventos (`@slack_app.event("message")`) y de acciones (`@slack_app.action(...)`) se registran por decorador al importar el módulo; Slack Bolt los invoca.

**Estado en memoria que maneja:** todo el estado compartido vive en `backend/state.py` (importado) y se protege con `lock`:
- `conversaciones` — dict `user_id -> estado` con la máquina de estados de la conversación (clave `modo`, `respuestas`, `preguntas_area`, `proyecto_actual`, `evaluados_en_sesion`, `evaluaciones_guardadas`, `editando_page_id`, `cargo_evaluador`, `relacion_jerarquica`, `area`, `labores_barbecho`, `pregunta_actual`, `campo_modificando`).
- `evaluacion_dm_canal` — `user_id -> id de canal DM`.
- `evaluacion_dm_ts` — `user_id -> ts` del mensaje raíz de la evaluación (identifica el hilo válido).
- `evaluacion_hora` — `user_id -> timestamp` del último envío.
- `evaluacion_ultimo_recordatorio` — `user_id -> timestamp` del último recordatorio.
- `evaluaciones_dm_activas` / `evaluaciones_dm_expiradas` — sets de usuarios con evaluación activa / expirada.
- Estado local del módulo: `_sugerencias_por_usuario` ([línea 260](../backend/slack_bot.py#L260)), `user_id -> [nombres sugeridos]` para resolver selecciones numéricas de sugerencias.

Constantes del módulo: `_Q5_EJEMPLO` ([256](../backend/slack_bot.py#L256)), `_PALABRAS_NUMERO` ([258](../backend/slack_bot.py#L258)), `_VALORACION_CLAVES = {"q1", "mo_contribucion"}` ([262](../backend/slack_bot.py#L262)), `_RECORDATORIO_PROYECTO_SEGUNDOS = 1 semana` ([2024](../backend/slack_bot.py#L2024)).

---

## Flujo de conversación (lo que ve el usuario, paso a paso)

### 1. Notificación inicial

El scheduler envía por DM un mensaje con este texto ([enviar_una_evaluacion, líneas 66-99](../backend/slack_bot.py#L66)):

> 📍 *Tienes una evaluación mensual pendiente.*
>
> _Esta evaluación es totalmente privada, solo podrá verla el CA de la persona evaluada._
> _Si en algún momento quieres cancelar, escribe SOS en el hilo._

Debajo aparece la pregunta **"👉 ¿Quieres ver un ejemplo antes de empezar?"** con dos botones: **✅ Sí** (action `mensual_ejemplo_si`) y **❌ No** (action `mensual_ejemplo_no`).

> 📷 **[Captura pendiente: DM inicial con el texto de evaluación pendiente y los botones Sí / No del ejemplo]**

Al pulsar **✅ Sí** el bot publica en el hilo el ejemplo de guía obtenido de Notion y a continuación arranca la evaluación; al pulsar **❌ No** la evaluación arranca directamente. Ya no hace falta escribir un mensaje para comenzar (aunque escribir en el hilo sigue funcionando como antes). Ambos botones inyectan el mismo evento que generaba el primer mensaje del usuario (`_arrancar_mensual_desde_boton`); si la conversación ya está en marcha, **Sí** solo muestra el ejemplo y **No** no hace nada. El handler antiguo `mensual_ver_ejemplo` (modal) se conserva para los DMs enviados antes del cambio.

### 2. Elección de área

El usuario pulsa **Sí** o **No** (o escribe cualquier mensaje en el hilo). El bot responde con **"¿A qué área perteneces?"** y tres botones ([_bloques_area, 284](../backend/slack_bot.py#L284)): **Negocio**, **MiddleOffice**, **Palantir**. También acepta la respuesta escrita (1/uno/negocio, 2/dos/middleoffice/mo, 3/tres/palantir — mapa en [líneas 1132-1137](../backend/slack_bot.py#L1132)). Si no se reconoce responde: "Por favor, pulsa el botón del área al que perteneces 😊".

> 📷 **[Captura pendiente: pregunta "¿A qué área perteneces?" con botones Negocio / MiddleOffice / Palantir]**

- Si elige **MiddleOffice**: no hay proyecto ni situación; salta directamente a pedir la persona a evaluar (paso 5, variante MO).
- Si elige **Negocio** o **Palantir**: pasa a la pregunta de situación (paso 3).

### 3. En proyecto vs barbecho (solo Negocio / Palantir)

El bot pregunta **"¿Estás actualmente en proyecto o en barbecho?"** con botones **🏗️ En proyecto** y **⏸️ En barbecho** ([_handle_area_interactiva, 435-450](../backend/slack_bot.py#L435); despacho por texto en [1498-1513](../backend/slack_bot.py#L1498)).

> 📷 **[Captura pendiente: pregunta de situación con botones "🏗️ En proyecto" y "⏸️ En barbecho"]**

**Rama barbecho:** el bot pregunta **"¿Qué labores estás realizando?"**. Tras la respuesta muestra un resumen:

> 📋 Tus labores:
> _<texto>_
>
> ¿Lo entrego o prefieres modificarlo?

con botones **✅ Entregar** y **✏️ Modificar** ([mostrar_resumen_barbecho, 1515-1532](../backend/slack_bot.py#L1515)). Al entregar, guarda el barbecho en Notion y responde "✅ Registrado. Muchas gracias, ya puedes salir del hilo 👋". Al modificar vuelve a pedir "Escribe de nuevo tus labores:".

> 📷 **[Captura pendiente: resumen de labores de barbecho con botones "✅ Entregar" y "✏️ Modificar"]**

**Rama proyecto:** el bot pide el proyecto: "Escribe el nombre de uno de los proyectos en los que estás trabajando. Más adelante podrás evaluar el resto".

### 4. Elección de proyecto (Negocio / Palantir)

El usuario escribe el nombre del proyecto. El bot confirma y pide la primera persona:

> Perfecto 😊, vamos con el proyecto *<proyecto>*. Dime el nombre de uno de los miembros de tu equipo, podrás evaluar al resto después.

### 5. Elección de persona a evaluar

- **Negocio / Palantir:** el usuario escribe nombre y apellido. Si no se encuentra, el bot muestra "*<texto>* no aparece en la lista de empleados." y ofrece botones de sugerencias de nombres parecidos ([_mensaje_empleado_no_encontrado, 781](../backend/slack_bot.py#L781); [_bloques_sugerencias, 299](../backend/slack_bot.py#L299)). Si ya se evaluó a esa persona en ese proyecto en la sesión, avisa y pide otro miembro.
- **MiddleOffice:** el bot muestra la lista de personas evaluables: "¿A quién quieres evaluar?" seguida de la lista con guiones. Si el nombre elegido no está en la lista MO, vuelve a pedir la persona.

> 📷 **[Captura pendiente: pregunta "¿A quién quieres evaluar?" con lista de personas (MO) o mensaje de empleado no encontrado con botones de sugerencias]**

Al validar a la persona se determina la **relación jerárquica** entre evaluador y evaluado ([comparar_jerarquia](../backend/hierarchy.py) / [tipo_relacion](../backend/hierarchy.py)) y se cargan las preguntas del área correspondiente.

### 6. Preguntas por área y por relación jerárquica

Las preguntas se hacen de forma secuencial (`preguntando_area_secuencial`):

- **Preguntas de valoración** (claves `q1` de negocio y `mo_contribucion` de MiddleOffice): se envían con botones **1, 2, 3, 4** ([_bloques_valoracion, 265](../backend/slack_bot.py#L265)). El texto de la pregunta de valoración de negocio se resuelve según la relación jerárquica ([_pregunta_contribucion, 739](../backend/slack_bot.py#L739)):
  - Si la relación es "inferior": "¿Cómo valorarías del 1 al 4 la contribución *del Project Leader* al buen avance del proyecto?"
  - En otro caso: "…la contribución *de <nombre_evaluado>*…" (o "de tu compañero" si no hay nombre).
  Si el usuario responde con texto no numérico: "Por favor, responde con un número del 1 al 4 🔢".
- **Preguntas de texto** (p. ej. `q2` → "Indica un ejemplo concreto que justifique tu valoración"): respuesta libre.

Las preguntas concretas se obtienen de Notion según el área: `obtener_preguntas_desde_notion` (negocio), `obtener_preguntas_mo` (MiddleOffice), `obtener_preguntas_palantir` (Palantir), todas por tipo de relación jerárquica.

> 📷 **[Captura pendiente: pregunta de valoración con botones 1-4 y una pregunta de texto libre]**

### 7. Resumen y confirmación

Tras responder todas las preguntas se muestra el **resumen** ([resumen_respuestas, 152](../backend/slack_bot.py#L152)):

> *Resumen de tus respuestas:*
> - *Persona evaluada*: …
> - *Proyecto*: …  (si aplica)
> - *<etiqueta de cada pregunta>*: …
>
> ¿Estás satisfecho con tus respuestas?
> Responde o haz click en sí para guardar en Notion o modificar para cambiar una respuesta concreta.

con botones **✅ Sí, guardar** (action `proyecto_confirmar`) y **✏️ Modificar** (action `proyecto_modificar`) ([_enviar_resumen_con_botones, 942](../backend/slack_bot.py#L942)).

> 📷 **[Captura pendiente: resumen de respuestas con botones "✅ Sí, guardar" y "✏️ Modificar"]**

### 8. Modificación de una respuesta concreta

Al pulsar **Modificar** aparece el menú **"¿Qué respuesta quieres modificar?"** con un botón por campo (1. Persona evaluada, 2. Proyecto, 3+ cada pregunta de área) ([_bloques_menu_modificacion_area, 186](../backend/slack_bot.py#L186)). También acepta el número por texto. Tras modificar, el resumen se muestra de nuevo con el sufijo:

> ✅ Respuesta actualizada. ¿Quieres cambiar algo más o sigo?
> Haz click en *Modificar* para cambiar otra respuesta o en *Sí, guardar* para continuar.

> 📷 **[Captura pendiente: menú "¿Qué respuesta quieres modificar?" con un botón por campo]**

### 9. Guardado y más miembros / más proyectos

Al confirmar, se guarda en Notion. El bot responde:

> ✅ *Evaluación guardada en Notion*.
>
> ¿Hay más miembros en el equipo que quieras evaluar?

con botones **✅ Sí** / **❌ No** ([_enviar_mas_miembros, 842](../backend/slack_bot.py#L842)).

> 📷 **[Captura pendiente: mensaje "Evaluación guardada" con botones ¿Más miembros? Sí / No]**

- **Sí:** vuelve a pedir persona (mismo proyecto en Negocio/Palantir; lista MO en MiddleOffice).
- **No** (Negocio/Palantir): pregunta **"¿Estás trabajando en algún otro proyecto?"** con botones Sí/No ([_enviar_mas_proyectos, 814](../backend/slack_bot.py#L814)). Si sí, vuelve a pedir un proyecto; si no, finaliza.
- **No** (MiddleOffice): finaliza directamente.

Al finalizar: "Perfecto, muchas gracias por tu tiempo ❤️. Ya puedes salir del hilo 👋".

### 10. Ventana de modificación de 2 días (grace period)

Si hay evaluaciones guardadas hace menos de 2 días, al terminar se añade el mensaje:

> 💬 Si quieres modificar tus respuestas, tienes un plazo de 2 días.

con botón **✏️ Modificar respuestas** (action `proyecto_modificar_eval`) ([_enviar_boton_modificar, 870](../backend/slack_bot.py#L870)). Al pulsarlo se muestra **"✏️ ¿La evaluación de quién quieres modificar?"** con un botón por evaluación vigente (formato "Evaluado — Proyecto") ([_enviar_lista_modificar, 892](../backend/slack_bot.py#L892)). Al elegir una, se recupera su estado, se muestra el resumen y, tras confirmar, se **actualiza** la página existente en Notion en lugar de crear una nueva. Después pregunta "✅ ¡Respuestas actualizadas! ¿Quieres modificar la evaluación de alguien más?". Si el plazo expiró: "⚠️ El plazo de modificación de 2 días ha expirado.".

> 📷 **[Captura pendiente: mensaje del plazo de 2 días con botón "✏️ Modificar respuestas" y la lista de evaluaciones a modificar]**

### 11. SOS (cancelar)

En cualquier momento el usuario puede escribir **SOS** en el hilo ([handle_message_events, 1020-1029](../backend/slack_bot.py#L1020)):
- Si la evaluación ya terminó: "Esta evaluación ya ha concluido, por favor salga del hilo. 👋".
- En otro caso: borra el estado y responde "Evaluación *cancelada* voluntariamente. Si quieres volver a empezar, escribe cualquier mensaje en este hilo.".

### 12. Recordatorios

Quien no complete la evaluación recibe cada semana ([ciclo_recordatorios_proyecto, 2065](../backend/slack_bot.py#L2065)):

> *⏰ Recuerda realizar tu evaluación mensual.* Abre el hilo del mensaje de evaluación y responde.

---

## Referencia de funciones

### `enviar_una_evaluacion()`
- **Qué hace:** Envía el DM de notificación inicial de evaluación mensual a todos los empleados (o solo al usuario de prueba). Marca las evaluaciones activas anteriores como expiradas y registra el estado de las nuevas.
- **Parámetros:** ninguno.
- **Devuelve:** `None`.
- **Efectos (Slack/Notion/estado):** Obtiene los Slack IDs (de config en modo prueba, o `obtener_slack_ids_empleados` en producción). Mueve `evaluaciones_dm_activas` → `evaluaciones_dm_expiradas` y limpia el set activo. Abre un DM por usuario (`conversations_open`) y publica el mensaje con blocks (botón "Ver ejemplo"). Actualiza `evaluaciones_dm_activas`, `evaluacion_dm_canal`, `evaluacion_dm_ts`, `evaluacion_hora` y limpia `conversaciones[user_id]`. Omite usuarios `user_not_found` / `channel_not_found`.
- **Se dispara/llama desde:** `enviar_evaluaciones_modo_prueba`, `enviar_evaluaciones_programadas`.
- **Notas:** Todo el estado se muta bajo `lock`. Captura excepciones globalmente.

### `enviar_evaluaciones_modo_prueba()`
- **Qué hace:** Envía una evaluación de inmediato y luego repite cada `INTERVALO_PRUEBA_DIAS` días en bucle infinito.
- **Parámetros:** ninguno.
- **Devuelve:** nunca retorna (bucle infinito con `time.sleep`).
- **Efectos:** llama a `enviar_una_evaluacion`.
- **Se dispara/llama desde:** `enviar_evaluaciones_programadas` cuando `APP_MODE != "produccion"`.

### `siguiente_envio_produccion(ahora=None)`
- **Qué hace:** Calcula la próxima fecha/hora de envío en producción según día y hora configurados (`DIA_ENVIO_PRODUCCION`, `HORA_ENVIO_PRODUCCION`).
- **Parámetros:** `ahora` — `datetime` opcional — momento de referencia (por defecto `now` en zona Madrid).
- **Devuelve:** `datetime` del próximo envío (si el objetivo ya pasó, suma 7 días).
- **Efectos:** ninguno (función pura).
- **Notas:** No se referencia dentro de este archivo; el scheduler de producción usa `siguiente_envio_calendario`. Función auxiliar/legacy.

### `enviar_evaluaciones_programadas()`
- **Qué hace:** Scheduler principal. En modo no-producción delega en `enviar_evaluaciones_modo_prueba`. En producción, bucle infinito que lee la fecha "Proyecto y CA" del calendario de Notion y espera hasta el próximo envío (intervalo de 4 semanas), enviando entonces.
- **Parámetros:** ninguno.
- **Devuelve:** nunca retorna en la práctica.
- **Efectos:** `obtener_config_calendario`, `siguiente_envio_calendario`, `enviar_una_evaluacion`; espera con `time.sleep`. Si falta la fecha en Notion, reintenta en 1 h.
- **Se dispara/llama desde:** el arranque de la aplicación (hilo del scheduler).

### `resumen_respuestas(respuestas, area="negocio", preguntas_area=None, tras_modificacion=False)`
- **Qué hace:** Construye el texto Markdown del resumen de respuestas del evaluador.
- **Parámetros:** `respuestas` — dict — respuestas dadas; `area` — str — área; `preguntas_area` — list — preguntas para etiquetar cada respuesta; `tras_modificacion` — bool — cambia el sufijo del mensaje.
- **Devuelve:** str con el resumen y el sufijo (confirmación normal o "Respuesta actualizada…").
- **Efectos:** ninguno.
- **Se dispara/llama desde:** múltiples ramas de `handle_message_events`, `_aplicar_respuesta_valoracion`, `_handle_sugerencia_interactiva`, `handle_proyecto_seleccionar_modificar`.

### `_texto_menu_modificacion_area(estado)`
- **Qué hace:** Genera el texto del menú "¿Qué respuesta quieres modificar?" (versión texto, opciones numeradas 1=Persona, 2=Proyecto, 3+=preguntas).
- **Parámetros:** `estado` — dict — estado de conversación (usa `preguntas_area`).
- **Devuelve:** str.
- **Se dispara/llama desde:** `handle_message_events` (rama `confirmacion` → modificar).

### `_bloques_menu_modificacion_area(estado)`
- **Qué hace:** Genera los blocks de Slack del menú de modificación como botones (`value` = número de opción, `action_id` = `mod_area_<n>`), agrupando en filas de máximo 5.
- **Parámetros:** `estado` — dict.
- **Devuelve:** list de blocks.
- **Se dispara/llama desde:** `_enviar_menu_modificacion_area`.

### `_enviar_menu_modificacion_area(dm_channel, thread_ts, estado)`
- **Qué hace:** Publica en el hilo el menú de modificación con botones.
- **Parámetros:** `dm_channel` — str; `thread_ts` — str; `estado` — dict.
- **Devuelve:** `None`.
- **Efectos:** `chat_postMessage`.
- **Se dispara/llama desde:** `handle_message_events` (acción `pedir_modificacion`), `handle_proyecto_modificar`.

### `_clave_modificacion_area(texto, estado)`
- **Qué hace:** Traduce la respuesta del usuario (número o palabra) a la clave del campo a modificar.
- **Parámetros:** `texto` — str; `estado` — dict.
- **Devuelve:** str clave (`"evaluado"`, `"proyecto"`, o clave de pregunta) o `None` si no válido.
- **Se dispara/llama desde:** `handle_message_events` (modo `seleccionando_modificacion_area`).

### `texto_pregunta_por_clave(clave, preguntas=None)`
- **Qué hace:** Devuelve el texto de una pregunta a partir de su clave (usa `preguntas` para satisfacción, o `config.PREGUNTAS`).
- **Parámetros:** `clave` — str; `preguntas` — dict opcional.
- **Devuelve:** str (por defecto "Escribe la nueva respuesta.").
- **Se dispara/llama desde:** `handle_message_events` (modo `modificando_respuesta`).

### `respuesta_es_confirmacion(texto)`
- **Qué hace:** Indica si el texto es una confirmación ("si", "ok", "guardar", "correcto"…).
- **Parámetros:** `texto` — str.
- **Devuelve:** bool.
- **Se dispara/llama desde:** `handle_message_events` (modo `confirmacion`).

### `respuesta_es_modificacion(texto)`
- **Qué hace:** Indica si el texto pide modificar ("modificar", "cambiar", "editar", "repetir").
- **Parámetros:** `texto` — str.
- **Devuelve:** bool.
- **Se dispara/llama desde:** `handle_message_events` (modo `confirmacion`).

### `_es_si(texto)`
- **Qué hace:** Indica si el texto es afirmativo ("si", "yes", "ok", "claro", "vale"…).
- **Parámetros:** `texto` — str.
- **Devuelve:** bool.
- **Se dispara/llama desde:** múltiples ramas (barbecho, más personas, más proyectos, más modificaciones).

### `_es_no(texto)`
- **Qué hace:** Indica si el texto es negativo ("no", "nope", "nel").
- **Parámetros:** `texto` — str.
- **Devuelve:** bool.
- **Se dispara/llama desde:** ramas de confirmación y de "más …".

### `_bloques_valoracion(texto_pregunta, user_id)`
- **Qué hace:** Genera los blocks de una pregunta de valoración con botones 1-4 (`action_id` = `valoracion_<i>`).
- **Parámetros:** `texto_pregunta` — str; `user_id` — str (block_id).
- **Devuelve:** list de blocks.
- **Se dispara/llama desde:** `handle_message_events`, `_handle_sugerencia_interactiva` (acción `preguntar_valoracion`).

### `_bloques_area(texto, user_id="")`
- **Qué hace:** Genera los blocks de la pregunta de área con botones Negocio / MiddleOffice / Palantir.
- **Parámetros:** `texto` — str; `user_id` — str opcional (block_id).
- **Devuelve:** list de blocks.
- **Se dispara/llama desde:** `handle_message_events` (acción `pedir_area`).

### `_bloques_sugerencias(texto_intro, sugerencias, user_id)`
- **Qué hace:** Genera los blocks con un botón por cada nombre sugerido (`action_id` = `sugerencia_<i>`).
- **Parámetros:** `texto_intro` — str; `sugerencias` — list de nombres; `user_id` — str.
- **Devuelve:** list de blocks.
- **Se dispara/llama desde:** `handle_message_events` (acción `pedir_persona_invalida`).

### `_aplicar_respuesta_valoracion(user_id, valor)`
- **Qué hace:** Aplica una valoración (1-4) al estado del usuario, tanto para el flujo secuencial como para la modificación puntual. Avanza a la siguiente pregunta o al resumen.
- **Parámetros:** `user_id` — str; `valor` — str ("1"-"4").
- **Devuelve:** tupla `(accion, texto_siguiente)`: `("mostrar_resumen", resumen)`, `("preguntar", texto)`, o `(None, None)` si no procede.
- **Efectos:** muta `estado["respuestas"]`, `estado["modo"]`, `pregunta_actual` bajo `lock`. Solo actúa si el modo es `preguntando_area_secuencial` (con clave de valoración) o `modificando_respuesta_area` (con `campo_modificando` en `_VALORACION_CLAVES`).
- **Se dispara/llama desde:** `_handle_valoracion_interactiva`.

### `_handle_valoracion_interactiva(ack, body, client, logger)` — `@slack_app.action(r"^valoracion_[1-4]$")`
- **Qué hace:** Handler de los botones de valoración 1-4. Actualiza el mensaje del botón a "Valoración: *N / 4* ✅", aplica la valoración y envía la siguiente pregunta o el resumen.
- **Parámetros:** args estándar de Bolt.
- **Devuelve:** `None`.
- **Efectos:** `chat_update`, `_aplicar_respuesta_valoracion`, `_enviar_resumen_con_botones` o `chat_postMessage`.
- **Se dispara/llama desde:** clic en botón de valoración.

### `_handle_area_interactiva(ack, body, client, logger)` — `@slack_app.action(r"^area_(negocio|middleoffice|palantir)$")`
- **Qué hace:** Handler de los botones de área. Actualiza el mensaje a "Área: *X* ✅", fija el área en el estado y encamina: MiddleOffice → pedir persona (con lista MO); Negocio/Palantir → pregunta de situación (proyecto/barbecho).
- **Parámetros:** args estándar de Bolt.
- **Devuelve:** `None`.
- **Efectos:** `chat_update`, muta estado bajo `lock` (solo si activo y modo `esperando_area`), `chat_postMessage`. Para MO consulta `obtener_nombre_por_id_usuario` y `obtener_evaluados_middleoffice`.
- **Se dispara/llama desde:** clic en botón de área.

### `_handle_situacion_interactiva(ack, body, client, logger)` — `@slack_app.action(r"^situacion_(proyecto|barbecho)$")`
- **Qué hace:** Handler de los botones proyecto/barbecho. Actualiza el mensaje a "Situación: *…* ✅" y encamina: proyecto → pedir nombre del proyecto; barbecho → "¿Qué labores estás realizando?".
- **Parámetros:** args estándar de Bolt.
- **Devuelve:** `None`.
- **Efectos:** `chat_update`, muta estado (solo si activo y modo `esperando_situacion`), `chat_postMessage`.
- **Se dispara/llama desde:** clic en botón de situación.

### `_handle_barbecho_entregar(ack, body, logger)` — `@slack_app.action("barbecho_entregar")`
- **Qué hace:** Confirma y guarda el registro de barbecho en Notion. Actualiza el mensaje a "✅ Entregado", marca `terminado` y saca al usuario de las evaluaciones activas.
- **Parámetros:** args estándar de Bolt.
- **Devuelve:** `None`.
- **Efectos:** `chat_update`; bajo `lock` (solo si modo `confirmacion_barbecho`) lee área y labores; `guardar_barbecho_en_notion`; responde "✅ Registrado…" o "⚠️ No se pudo guardar…".
- **Se dispara/llama desde:** clic en botón "✅ Entregar" del resumen de barbecho.

### `_handle_barbecho_modificar(ack, body, logger)` — `@slack_app.action("barbecho_modificar")`
- **Qué hace:** Permite reescribir las labores de barbecho. Actualiza el mensaje a "✏️ Modificando…", vuelve a modo `esperando_labores_barbecho` y borra las labores guardadas.
- **Parámetros:** args estándar de Bolt.
- **Devuelve:** `None`.
- **Efectos:** `chat_update`, muta estado (solo si modo `confirmacion_barbecho`), `chat_postMessage` ("Escribe de nuevo tus labores:").
- **Se dispara/llama desde:** clic en botón "✏️ Modificar" del resumen de barbecho.

### `_handle_sugerencia_interactiva(ack, body, client, logger)` — `@slack_app.action(r"^sugerencia_\d+$")`
- **Qué hace:** Handler de los botones de sugerencia de nombre. Fija el empleado elegido, calcula relación jerárquica y preguntas de área, y avanza el flujo (primera pregunta de valoración, siguiente pregunta, o resumen si se estaba modificando).
- **Parámetros:** args estándar de Bolt (`value` = nombre elegido).
- **Devuelve:** `None`.
- **Efectos:** `chat_update`; limpia `_sugerencias_por_usuario`; lookups Notion fuera del `lock` (`buscar_empleado_y_cargo`, `obtener_evaluados_middleoffice`, `obtener_preguntas_mo/palantir/negocio`, `obtener_cargo_por_slack_id`, `comparar_jerarquia`); muta estado según modo (`esperando_persona` o `modificando_respuesta`); envía valoración con botones, resumen o texto. Valida duplicados con `evaluados_en_sesion` y validez MO.
- **Se dispara/llama desde:** clic en botón de sugerencia de empleado.

### `_normalizar_valoracion(texto)`
- **Qué hace:** Convierte un texto en "1"-"4" si es un número válido (dígito o palabra).
- **Parámetros:** `texto` — str.
- **Devuelve:** str "1"-"4" o `None`.
- **Se dispara/llama desde:** `handle_message_events` (preguntas de valoración por texto).

### `_pregunta_contribucion(relacion, nombre_evaluado="")`
- **Qué hace:** Devuelve el texto de la pregunta de contribución (valoración 1-4) según la relación jerárquica.
- **Parámetros:** `relacion` — str; `nombre_evaluado` — str.
- **Devuelve:** str.
- **Se dispara/llama desde:** `_resolver_texto_q1`.

### `_es_q1_texto_default(texto)`
- **Qué hace:** Detecta si el texto de la pregunta q1 es el texto por defecto/placeholder (vacío, empieza por "Este mes" o contiene "Puedes considerar claridad").
- **Parámetros:** `texto` — str.
- **Devuelve:** bool.
- **Se dispara/llama desde:** `_resolver_texto_q1`.

### `_resolver_texto_q1(texto, relacion, nombre)`
- **Qué hace:** Resuelve el texto final de q1: si es el default lo sustituye por la pregunta de contribución; si contiene `{nombre}` lo reemplaza (por "el Project Leader" si relación inferior).
- **Parámetros:** `texto` — str; `relacion` — str; `nombre` — str.
- **Devuelve:** str.
- **Se dispara/llama desde:** `_preguntas_negocio`, `_handle_sugerencia_interactiva`, `handle_message_events` (inyección de preguntas de área).

### `_preguntas_negocio(relacion, preguntas_notion=None, nombre_evaluado="")`
- **Qué hace:** Construye la lista de preguntas de negocio (q1 valoración resuelta + q2 ejemplo).
- **Parámetros:** `relacion` — str; `preguntas_notion` — dict opcional (textos desde Notion); `nombre_evaluado` — str.
- **Devuelve:** list de `{"clave", "texto"}`.
- **Se dispara/llama desde:** `handle_message_events`, `_handle_sugerencia_interactiva`.

### `_es_valor_satisfaccion(texto)`
- **Qué hace:** Indica si el texto es un valor de satisfacción válido (1-4).
- **Parámetros:** `texto` — str.
- **Devuelve:** bool.
- **Notas:** definida pero no referenciada en este archivo (auxiliar).

### `_parece_saludo(texto)`
- **Qué hace:** Detecta saludos ("hola", "buenas", "hey", "ei") para no interpretarlos como nombre de persona.
- **Parámetros:** `texto` — str.
- **Devuelve:** bool.
- **Se dispara/llama desde:** `handle_message_events` (modo `esperando_persona`, y pre-fetch).

### `_mensaje_empleado_no_encontrado(texto)`
- **Qué hace:** Genera el mensaje "no aparece en la lista" y, si hay nombres parecidos, los devuelve como sugerencias.
- **Parámetros:** `texto` — str (nombre buscado).
- **Devuelve:** tupla `(mensaje, sugerencias)`.
- **Efectos:** `sugerir_empleados_parecidos`.
- **Se dispara/llama desde:** `handle_message_events` (pre-fetch de búsqueda de empleado).

### `_nombre_real(user_id, logger)`
- **Qué hace:** Obtiene el nombre real del usuario: primero de Notion (`obtener_nombre_por_id_usuario`); si no, de Slack (`users_info`); si falla, el `user_id`.
- **Parámetros:** `user_id` — str; `logger`.
- **Devuelve:** str.
- **Se dispara/llama desde:** guardado (barbecho y proyecto), recordatorios, comprobación de ya-respondido.

### `_enviar_mas_proyectos(channel, thread_ts)`
- **Qué hace:** Publica "¿Estás trabajando en algún otro proyecto?" con botones Sí/No (`proyecto_proyectos_si` / `_no`).
- **Parámetros:** `channel` — str; `thread_ts` — str.
- **Devuelve:** `None`.
- **Efectos:** `chat_postMessage`.
- **Se dispara/llama desde:** `handle_message_events` (acción `pedir_mas_proyectos`), `handle_proyecto_mas_no`.

### `_enviar_mas_miembros(channel, thread_ts)`
- **Qué hace:** Publica "✅ Evaluación guardada en Notion. ¿Hay más miembros…?" con botones Sí/No (`proyecto_mas_si` / `_no`).
- **Parámetros:** `channel` — str; `thread_ts` — str.
- **Devuelve:** `None`.
- **Efectos:** `chat_postMessage`.
- **Se dispara/llama desde:** tras guardar en Notion (`handle_message_events`, `handle_proyecto_confirmar`).

### `_enviar_boton_modificar(channel, thread_ts)`
- **Qué hace:** Publica el aviso del plazo de 2 días con botón "✏️ Modificar respuestas" (`proyecto_modificar_eval`).
- **Parámetros:** `channel` — str; `thread_ts` — str.
- **Devuelve:** `None`.
- **Efectos:** `chat_postMessage`.
- **Se dispara/llama desde:** ramas de finalización con evaluaciones vigentes (`terminar`, `terminar_modificacion`, `handle_proyecto_mas_no` MO, `handle_proyecto_proyectos_no`, `handle_proyecto_modif_mas_no`).

### `_enviar_lista_modificar(channel, thread_ts, evaluaciones)`
- **Qué hace:** Publica "✏️ ¿La evaluación de quién quieres modificar?" con un botón por evaluación (label "Evaluado — Proyecto", `action_id` = `proyecto_sel_mod_<i>`, `value` = page_id).
- **Parámetros:** `channel` — str; `thread_ts` — str; `evaluaciones` — list de dicts guardados.
- **Devuelve:** `None`.
- **Efectos:** `chat_postMessage`.
- **Se dispara/llama desde:** `handle_message_events` (acción `mostrar_seleccion_modificar`), `handle_proyecto_modificar_eval`, `handle_proyecto_modif_mas_si`.

### `_enviar_pregunta_mas_modificaciones(channel, thread_ts)`
- **Qué hace:** Publica "✅ ¡Respuestas actualizadas! ¿Quieres modificar la evaluación de alguien más?" con botones Sí/No (`proyecto_modif_mas_si` / `_no`).
- **Parámetros:** `channel` — str; `thread_ts` — str.
- **Devuelve:** `None`.
- **Efectos:** `chat_postMessage`.
- **Se dispara/llama desde:** tras actualizar una evaluación existente en Notion (`handle_message_events`, `handle_proyecto_confirmar`).

### `_enviar_resumen_con_botones(channel, thread_ts, text)`
- **Qué hace:** Publica el resumen de respuestas con botones "✅ Sí, guardar" (`proyecto_confirmar`) y "✏️ Modificar" (`proyecto_modificar`).
- **Parámetros:** `channel` — str; `thread_ts` — str; `text` — str (resumen).
- **Devuelve:** `None`.
- **Efectos:** `chat_postMessage`.
- **Se dispara/llama desde:** varias ramas de resumen (`handle_message_events`, `_aplicar_respuesta_valoracion` vía handler, `_handle_sugerencia_interactiva`, `handle_proyecto_seleccionar_modificar`).

### `handle_message_events(event, logger)` — `@slack_app.event("message")`
- **Qué hace:** Handler principal de mensajes de texto. Es el corazón de la máquina de estados de la evaluación mensual y el router hacia CA / personal.
- **Parámetros:** `event` — dict del evento Slack; `logger`.
- **Devuelve:** `None`.
- **Efectos / lógica:**
  - Ignora mensajes de bots y no-DM. Exige que el mensaje esté en un hilo (si no: "Por favor, no contestes a las evaluaciones fuera de los hilos 😊").
  - **Router:** si `thread_ts` coincide con `ca_dm_ts[user]` → `manejar_mensaje_ca`; si coincide con `personal_dm_ts[user]` → `manejar_mensaje_personal`; si no coincide con `evaluacion_dm_ts[user]`, avisa (si hay otra evaluación activa) y retorna.
  - **SOS:** cancela la evaluación (ver flujo).
  - **Pre-fetch fuera del lock:** para `esperando_persona` y `modificando_respuesta`(evaluado) hace las llamadas Notion pesadas (`buscar_empleado_y_cargo`, preguntas por área, cargo/relación, validación MO, sugerencias). Resuelve selección numérica de sugerencias previas.
  - **Ya respondió:** en `pre_inicial` comprueba `evaluacion_proyecto_guardada_desde` para el ciclo actual.
  - **Máquina de estados bajo `lock`:** modos `pre_inicial`, `esperando_area`, `esperando_situacion`, `esperando_labores_barbecho`, `confirmacion_barbecho`, `esperando_proyecto`, `esperando_persona`, `preguntando_area_secuencial`, `confirmacion`, `modificando_respuesta`, `seleccionando_modificacion_area`, `modificando_respuesta_area`, `guardar`, `preguntar_mas_personas`, `preguntar_mas_proyectos`, `terminado`, `preguntar_mas_modificaciones`. Cada uno fija `(accion, pregunta)`.
  - **Despacho fuera del lock:** según `accion` publica los mensajes/blocks correspondientes (situación, resumen barbecho, guardar barbecho, área, sugerencias, menú modificación, valoración, resumen, guardar en Notion, más MO, terminar, selección de modificar, etc.). El guardado usa `guardar_en_notion` (nuevo) o `actualizar_en_notion` (si `editando_page_id`), registra la evaluación en `evaluaciones_guardadas` con timestamp para el grace period.
- **Se dispara/llama desde:** evento `message` de Slack; también invocado sintéticamente por `_handle_mod_area_opcion`.
- **Notas:** Todas las mutaciones de estado bajo `lock`; llamadas Notion/Slack fuera del lock para no bloquear.

### `handle_proyecto_confirmar(ack, body, logger)` — `@slack_app.action("proyecto_confirmar")`
- **Qué hace:** Handler del botón "✅ Sí, guardar" del resumen. Guarda (o actualiza) la evaluación en Notion.
- **Parámetros:** args estándar de Bolt.
- **Devuelve:** `None`.
- **Efectos:** solo si activo y modo `confirmacion`. Si `editando_page_id` → `actualizar_en_notion` y pasa a `preguntar_mas_modificaciones`; si no → `guardar_en_notion`, registra en `evaluados_en_sesion` y `evaluaciones_guardadas`, pasa a `preguntar_mas_personas` y envía "¿Más miembros?". Mensajes de error si falla Notion.
- **Se dispara/llama desde:** clic en "✅ Sí, guardar".

### `handle_proyecto_mas_si(ack, body)` — `@slack_app.action("proyecto_mas_si")`
- **Qué hace:** Handler del botón "✅ Sí" de "¿más miembros?". Vuelve a modo `esperando_persona` y pide otra persona (lista MO en MiddleOffice, o "¿Qué otro miembro del proyecto *X*…?").
- **Parámetros:** args estándar de Bolt.
- **Devuelve:** `None`.
- **Efectos:** solo si modo `preguntar_mas_personas`; `chat_postMessage`; en MO consulta `obtener_evaluados_middleoffice`.
- **Se dispara/llama desde:** clic en "✅ Sí" de más miembros.

### `handle_proyecto_mas_no(ack, body)` — `@slack_app.action("proyecto_mas_no")`
- **Qué hace:** Handler del botón "❌ No" de "¿más miembros?". En MiddleOffice termina (y ofrece botón de modificar si hay evaluaciones vigentes); en Negocio/Palantir pregunta por más proyectos.
- **Parámetros:** args estándar de Bolt.
- **Devuelve:** `None`.
- **Efectos:** solo si modo `preguntar_mas_personas`; muta modo; `_enviar_mas_proyectos` o mensaje de despedida + `_enviar_boton_modificar`.
- **Se dispara/llama desde:** clic en "❌ No" de más miembros.

### `handle_proyecto_modificar_eval(ack, body, logger)` — `@slack_app.action("proyecto_modificar_eval")`
- **Qué hace:** Handler del botón "✏️ Modificar respuestas" (grace period). Muestra la lista de evaluaciones vigentes (< 2 días) a modificar.
- **Parámetros:** args estándar de Bolt.
- **Devuelve:** `None`.
- **Efectos:** valida evaluación activa y evaluaciones vigentes; `_enviar_lista_modificar` o mensajes de "no hay evaluación activa" / "plazo expirado".
- **Se dispara/llama desde:** clic en "✏️ Modificar respuestas".

### `handle_proyecto_seleccionar_modificar(ack, body, logger)` — `@slack_app.action(r"^proyecto_sel_mod_\d+$")`
- **Qué hace:** Handler al elegir una evaluación concreta a modificar. Recupera su estado guardado (respuestas, preguntas, relación, área, page_id), lo carga en `conversaciones`, fija `editando_page_id` y modo `confirmacion`, y muestra su resumen con botones.
- **Parámetros:** args estándar de Bolt (`value` = page_id).
- **Devuelve:** `None`.
- **Efectos:** valida vigencia (< 2 días); muta estado bajo `lock`; `_enviar_resumen_con_botones`.
- **Se dispara/llama desde:** clic en un botón de la lista de modificar.

### `handle_proyecto_modif_mas_si(ack, body, logger)` — `@slack_app.action("proyecto_modif_mas_si")`
- **Qué hace:** Handler del botón "✅ Sí" de "¿modificar a alguien más?". Reenvía la lista de evaluaciones vigentes.
- **Parámetros:** args estándar de Bolt.
- **Devuelve:** `None`.
- **Efectos:** solo si modo `preguntar_mas_modificaciones`; `_enviar_lista_modificar` o "plazo expirado".
- **Se dispara/llama desde:** clic en "✅ Sí" de más modificaciones.

### `handle_proyecto_modif_mas_no(ack, body, logger)` — `@slack_app.action("proyecto_modif_mas_no")`
- **Qué hace:** Handler del botón "❌ No" de "¿modificar a alguien más?". Finaliza ("✅ ¡Listo! Evaluación finalizada…") y, si hay evaluaciones vigentes, ofrece de nuevo el botón de modificar.
- **Parámetros:** args estándar de Bolt.
- **Devuelve:** `None`.
- **Efectos:** solo si modo `preguntar_mas_modificaciones`; pasa a `terminado`; `_enviar_boton_modificar` si procede.
- **Se dispara/llama desde:** clic en "❌ No" de más modificaciones.

### `handle_proyecto_proyectos_si(ack, body)` — `@slack_app.action("proyecto_proyectos_si")`
- **Qué hace:** Handler del botón "✅ Sí" de "¿otro proyecto?". Vuelve a `esperando_proyecto` (resetea `proyecto_actual`) y pide un nuevo proyecto.
- **Parámetros:** args estándar de Bolt.
- **Devuelve:** `None`.
- **Efectos:** solo si modo `preguntar_mas_proyectos`; `chat_postMessage`.
- **Se dispara/llama desde:** clic en "✅ Sí" de más proyectos.

### `handle_proyecto_proyectos_no(ack, body)` — `@slack_app.action("proyecto_proyectos_no")`
- **Qué hace:** Handler del botón "❌ No" de "¿otro proyecto?". Termina la evaluación y, si hay evaluaciones vigentes, ofrece el botón de modificar.
- **Parámetros:** args estándar de Bolt.
- **Devuelve:** `None`.
- **Efectos:** solo si modo `preguntar_mas_proyectos`; pasa a `terminado`; despedida + `_enviar_boton_modificar` si procede.
- **Se dispara/llama desde:** clic en "❌ No" de más proyectos.

### `handle_proyecto_modificar(ack, body, logger)` — `@slack_app.action("proyecto_modificar")`
- **Qué hace:** Handler del botón "✏️ Modificar" del resumen. Pasa a modo `seleccionando_modificacion_area` y muestra el menú de qué respuesta modificar.
- **Parámetros:** args estándar de Bolt.
- **Devuelve:** `None`.
- **Efectos:** solo si modo `confirmacion`; `_enviar_menu_modificacion_area`.
- **Se dispara/llama desde:** clic en "✏️ Modificar" del resumen.

### `_handle_mod_area_opcion(ack, body, logger)` — `@slack_app.action(r"^mod_area_\d+$")`
- **Qué hace:** Handler de los botones del menú "¿Qué respuesta quieres modificar?". Sintetiza un mensaje de texto equivalente (el número de la opción) y lo reinyecta en `handle_message_events` para reutilizar toda la máquina de estados.
- **Parámetros:** args estándar de Bolt (`value` = número de opción).
- **Devuelve:** `None`.
- **Efectos:** llama a `handle_message_events` con un evento sintético (`channel`, `thread_ts`, `user`, `text`).
- **Se dispara/llama desde:** clic en un botón del menú de modificación.

### `_build_ejemplo_mensual_view()`
- **Qué hace:** Construye el `view` del modal "Ejemplo de guía — Evaluación Mensual" con el ejemplo obtenido de Notion (recortado a 3000 caracteres).
- **Parámetros:** ninguno.
- **Devuelve:** dict (modal view de Slack).
- **Efectos:** `obtener_ejemplos_guia`.
- **Se dispara/llama desde:** `_handle_mensual_ver_ejemplo`.

### `_handle_mensual_ver_ejemplo(ack, body, logger)` — `@slack_app.action("mensual_ver_ejemplo")`
- **Qué hace:** Handler del botón "Ver ejemplo" del DM inicial. Abre el modal de ejemplo.
- **Parámetros:** args estándar de Bolt.
- **Devuelve:** `None`.
- **Efectos:** `views_open` con `_build_ejemplo_mensual_view`.
- **Se dispara/llama desde:** clic en "Ver ejemplo".

### `ciclo_recordatorios_proyecto()`
- **Qué hace:** Bucle infinito (30 s) que envía recordatorios semanales a los usuarios con evaluación activa que aún no la han completado. Si detecta que ya la guardaron, los saca de las activas.
- **Parámetros:** ninguno.
- **Devuelve:** nunca retorna.
- **Efectos:** lee `evaluaciones_dm_activas`, `evaluacion_hora`, `evaluacion_ultimo_recordatorio` bajo `lock`; `_nombre_real`, `evaluacion_proyecto_guardada_desde`; `chat_postMessage` con el recordatorio; actualiza `evaluacion_ultimo_recordatorio`.
- **Se dispara/llama desde:** hilo de recordatorios en el arranque de la app.
- **Notas:** Umbral = `_RECORDATORIO_PROYECTO_SEGUNDOS` (1 semana) desde el máximo entre hora de envío y último recordatorio.

### `start_socket_mode()`
- **Qué hace:** Arranca el `SocketModeHandler` de Slack Bolt con `slack_app` y `config.SLACK_APP_TOKEN` (bloqueante).
- **Parámetros:** ninguno.
- **Devuelve:** nunca retorna (bloquea).
- **Efectos:** conecta el bot a Slack en modo Socket.
- **Se dispara/llama desde:** arranque de la aplicación.