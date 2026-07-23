# Infraestructura, arranque y utilidades

Este documento describe, función por función, los archivos de infraestructura, arranque y utilidades del proyecto EvaluaBot. Estos archivos son los cimientos sobre los que se apoyan las funcionalidades de evaluación: el punto de entrada, la carga de configuración, la inicialización de clientes externos (Slack/Notion/Claude), el estado en memoria, la jerarquía de cargos, la internacionalización, la gestión de usuarios web (auth), la web antigua integrada y los scripts de creación de usuarios y migración de Notion.

---

## `bot.py` — Punto de entrada raíz

**Propósito:** Es el ejecutable de arranque del proyecto (`python bot.py`). Solo delega en `backend.main.main`. Ver [bot.py:1](../../bot.py#L1).

### `if __name__ == "__main__": main()`
- **Qué hace:** Importa `main` desde [backend/main.py](../../backend/main.py) y lo ejecuta cuando el archivo se lanza directamente. Ver [bot.py:4](../../bot.py#L4).
- **Parámetros:** Ninguno.
- **Devuelve:** Nada.
- **Efectos:** Arranca toda la aplicación (hilos + Socket Mode de Slack).
- **Notas:** El archivo tiene solo 5 líneas: toda la lógica de arranque real vive en `backend/main.py`.

---

## `backend/main.py` — Arranque y lanzamiento de hilos

**Propósito:** Valida la configuración, aplica la estética inicial de Notion, inicializa las bases de datos de MiddleOffice y lanza en hilos daemon los seis ciclos de envío/recordatorios (proyecto, CA y personal), el servidor web (API o legacy) y finalmente arranca el bot de Slack en modo Socket Mode (bloqueante). Ver [main.py:1](../../backend/main.py#L1).

### `validar_configuracion()`
- **Qué hace:** Comprueba requisitos mínimos antes de arrancar. Ver [main.py:15](../../backend/main.py#L15).
- **Parámetros:** Ninguno.
- **Devuelve:** `True` si la configuración es suficiente para arrancar; `False` si falta `NOTION_PARENT_PAGE_ID`.
- **Efectos:** Imprime avisos por consola:
  - Si falta `config.NOTION_PARENT_PAGE_ID`, imprime instrucciones y devuelve `False` (arranque abortado). Ver [main.py:16](../../backend/main.py#L16).
  - Si falta `config.ANTHROPIC_API_KEY`, avisa de que la web no podrá generar informes con Claude (no bloquea). Ver [main.py:20](../../backend/main.py#L20).
  - Si `Document is None` (falta `python-docx`), avisa de cómo instalarlo (no bloquea). Ver [main.py:22](../../backend/main.py#L22).
- **Notas:** `Document` se importa desde [clients.py](../../backend/clients.py); es `None` si `python-docx` no está instalado.

### `main()`
- **Qué hace:** Función de arranque principal de la aplicación. Ver [main.py:27](../../backend/main.py#L27).
- **Parámetros:** Ninguno.
- **Devuelve:** Nada (bloquea en `start_socket_mode()`).
- **Efectos:**
  1. Configura logging a nivel `INFO` ([main.py:28](../../backend/main.py#L28)).
  2. Si `validar_configuracion()` devuelve `False`, termina con `sys.exit(1)` ([main.py:29](../../backend/main.py#L29)).
  3. Llama a `aplicar_estetica_notion()` dentro de try/except (registra excepción sin abortar) ([main.py:32](../../backend/main.py#L32)).
  4. Llama a `inicializar_bbdd_middleoffice()` dentro de try/except ([main.py:37](../../backend/main.py#L37)).
  5. Lanza seis hilos daemon con los ciclos ([main.py:42](../../backend/main.py#L42)):
     - `enviar_evaluaciones_programadas` (proyecto)
     - `ciclo_envio_ca` (CA)
     - `ciclo_envio_personal` (personal)
     - `ciclo_recordatorios_proyecto`
     - `ciclo_recordatorios_ca`
     - `ciclo_recordatorios_personal`
  6. Elige el servidor web según `config.WEB_MODE`: `iniciar_servidor_web` si es `"legacy"`, si no `iniciar_api_backend` ([main.py:48](../../backend/main.py#L48)), y lo lanza en un hilo daemon.
  7. Imprime mensajes de estado según `config.APP_MODE` (`"produccion"` vs prueba) y según `config.WEB_MODE` (URL de web legacy o de API backend) ([main.py:51](../../backend/main.py#L51)).
  8. Llama a `start_socket_mode()`, que arranca la conexión con Slack y bloquea el hilo principal ([main.py:60](../../backend/main.py#L60)).
- **Notas:** El import de `ca_reviews` en [main.py:9](../../backend/main.py#L9) lleva `# noqa: F401` porque su único efecto necesario es registrar el handler de Slack al importarse. Todos los ciclos se lanzan como daemon: mueren cuando muere el hilo principal.

---

## `backend/config.py` — Variables de entorno y constantes

**Propósito:** Centraliza toda la configuración leída del entorno (`os.environ`) y las constantes del proyecto. Ver [config.py:1](../../backend/config.py#L1). Incluye tokens obligatorios (que abortan si faltan), nombres de páginas/BD de Notion, configuración SMTP, modos de la app y web, y una hoja de estilos CSS integrada.

### `env_bool(name, default="false")`
- **Qué hace:** Lee una variable de entorno booleana. Ver [config.py:9](../../backend/config.py#L9).
- **Parámetros:** `name` (nombre de la variable), `default` (texto por defecto, `"false"`).
- **Devuelve:** `True` si el valor (en minúsculas, sin espacios) está en `{"1", "true", "yes", "si", "sí"}`; si no, `False`.
- **Efectos:** Ninguno (solo lectura).

### `_require_env(name)`
- **Qué hace:** Lee una variable de entorno obligatoria. Ver [config.py:39](../../backend/config.py#L39).
- **Parámetros:** `name`.
- **Devuelve:** El valor de la variable.
- **Efectos:** Si la variable no está definida (`None`), lanza `SystemExit` con un mensaje de error, abortando el arranque.
- **Notas:** Se usa para `SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN`, `NOTION_TOKEN` y `NOTION_DATABASE_ID`.

### Tabla de constantes y variables de entorno

| Nombre | Valor por defecto | Para qué sirve |
|--------|-------------------|----------------|
| `BASE_DIR` | Ruta absoluta del directorio `backend/` | Directorio base para construir rutas (p.ej. `CARPETA_WEB`). [config.py:6](../../backend/config.py#L6) |
| `CHANNEL_ID` | `"C0BBFRM14SU"` (env `SLACK_CHANNEL_ID`) | ID del canal de Slack principal. [config.py:13](../../backend/config.py#L13) |
| `APP_MODE` | `"prueba"` (env `APP_MODE`, en minúsculas) | Modo de la app: `"produccion"` envía según fecha configurada; otro valor = modo prueba. [config.py:15](../../backend/config.py#L15) |
| `INTERVALO_PRUEBA_DIAS` | `30` | Días entre envíos en modo prueba. [config.py:16](../../backend/config.py#L16) |
| `ZONA_HORARIA_MADRID` | `ZoneInfo("Europe/Madrid")` | Zona horaria de referencia para envíos programados. [config.py:17](../../backend/config.py#L17) |
| `DIA_ENVIO_PRODUCCION` | `4` | Día de la semana para el envío en producción (0=lunes … 4=viernes). [config.py:18](../../backend/config.py#L18) |
| `HORA_ENVIO_PRODUCCION` | `time(10, 0)` | Hora del envío en producción (10:00). [config.py:19](../../backend/config.py#L19) |
| `PUERTO_WEB` | `8000` (env `PUERTO_WEB`) | Puerto del servidor web/API. [config.py:21](../../backend/config.py#L21) |
| `CARPETA_WEB` | `BASE_DIR/dashboard_web` | Carpeta con los estáticos de la web legacy, `users.json` y archivos generados. [config.py:22](../../backend/config.py#L22) |
| `PREFIJO_BBDD_EVALUADO` | `"Evaluaciones - "` | Prefijo de los nombres de las BD de resultados por evaluado en Notion. [config.py:23](../../backend/config.py#L23) |
| `FRONTEND_ORIGIN` | `"http://localhost:5173"` (env `FRONTEND_ORIGIN`) | Origen del frontend React (para CORS). [config.py:24](../../backend/config.py#L24) |
| `APP_PUBLIC_URL` | `FRONTEND_ORIGIN` (env `APP_PUBLIC_URL`, sin `/` final) | URL pública de la app; base de los enlaces de reset de contraseña. [config.py:25](../../backend/config.py#L25) |
| `WEB_MODE` | `"api"` (env `WEB_MODE`, en minúsculas) | `"api"` arranca la API backend (Flask); `"legacy"` arranca la web antigua integrada. [config.py:26](../../backend/config.py#L26) |
| `SMTP_HOST` | `""` (env `SMTP_HOST`) | Servidor SMTP para enviar correos de reset. [config.py:27](../../backend/config.py#L27) |
| `SMTP_PORT` | `587` (env `SMTP_PORT`) | Puerto SMTP. [config.py:28](../../backend/config.py#L28) |
| `SMTP_USER` | `""` (env `SMTP_USER`) | Usuario SMTP. [config.py:29](../../backend/config.py#L29) |
| `SMTP_PASSWORD` | `""` (env `SMTP_PASSWORD`) | Contraseña SMTP. [config.py:30](../../backend/config.py#L30) |
| `SMTP_FROM` | `SMTP_USER` (env `SMTP_FROM`) | Dirección remitente de los correos. [config.py:31](../../backend/config.py#L31) |
| `SMTP_USE_TLS` | `True` (env `SMTP_USE_TLS`) | Si usar STARTTLS al conectar por SMTP. [config.py:32](../../backend/config.py#L32) |
| `INSTRUCCIONES_RESPONDER_EN_HILO` | Texto fijo | Nota que se añade a las notificaciones de Slack pidiendo responder en el hilo. [config.py:33](../../backend/config.py#L33) |
| `SLACK_BOT_TOKEN` | **obligatoria** (env) | Token del bot de Slack (`xoxb-…`). Aborta si falta. [config.py:46](../../backend/config.py#L46) |
| `SLACK_APP_TOKEN` | **obligatoria** (env) | Token de app de Slack para Socket Mode (`xapp-…`). Aborta si falta. [config.py:47](../../backend/config.py#L47) |
| `SLACK_TEST_USER_ID` | `""` (env) | ID de usuario Slack para pruebas dirigidas. [config.py:48](../../backend/config.py#L48) |
| `NOTION_TOKEN` | **obligatoria** (env) | Token de integración de Notion. Aborta si falta. [config.py:49](../../backend/config.py#L49) |
| `NOTION_DATABASE_ID` | **obligatoria** (env) | ID de la BD principal de Notion. Aborta si falta. [config.py:50](../../backend/config.py#L50) |
| `NOTION_EMPLOYEES_DATABASE_ID` | `NOTION_DATABASE_ID` (env) | ID de la BD de empleados. [config.py:51](../../backend/config.py#L51) |
| `NOTION_TODO_PAGE_NAME` | `"TO-DO"` (env) | Nombre de la página contenedora TO-DO. [config.py:53](../../backend/config.py#L53) |
| `NOTION_TOSEE_PAGE_NAME` | `"TO-SEE"` (env) | Nombre de la página contenedora TO-SEE. [config.py:54](../../backend/config.py#L54) |
| `NOTION_DATA_LISTS_PAGE_NAME` | `"Datos a Monitorizar"` (env) | Página bajo TO-DO con las listas de datos. [config.py:56](../../backend/config.py#L56) |
| `NOTION_DATA_MODIFICABLES_PAGE_NAME` | `"Datos opcionalmente modificables"` (env) | Página bajo TO-DO con datos editables. [config.py:57](../../backend/config.py#L57) |
| `NOTION_PREGUNTAS_CHATBOT_PAGE_NAME` | `"Preguntas Chatbot"` (env) | Página con las preguntas del chatbot. [config.py:58](../../backend/config.py#L58) |
| `NOTION_RESULTADOS_EVAL_PAGE_NAME` | `"Resultados Evaluaciones"` (env) | Página bajo TO-SEE con resultados. [config.py:60](../../backend/config.py#L60) |
| `NOTION_ACTIVACIONES_PERMISOS_PAGE_NAME` | `"Activaciones de permisos"` (env) | Página bajo TO-SEE de permisos. [config.py:61](../../backend/config.py#L61) |
| `NOTION_INDIVIDUAL_EVALUATIONS_PAGE_NAME` | `"Resultados Evaluaciones Mensuales"` (env) | Página de resultados de evaluaciones mensuales. [config.py:63](../../backend/config.py#L63) |
| `NOTION_CA_TRACKING_PAGE_NAME` | `"Resultados Evaluaciones CA"` (env) | Página de resultados de seguimiento CA. [config.py:64](../../backend/config.py#L64) |
| `NOTION_CONTINUOUS_EVALUATIONS_PAGE_NAME` | `"Resultados Barbecho"` (env) | Página de resultados de barbecho (evaluación continua). [config.py:65](../../backend/config.py#L65) |
| `NOTION_EMPLOYEES_DATABASE_NAME` | `"Lista de empleados"` (env) | Nombre de la BD de empleados. [config.py:66](../../backend/config.py#L66) |
| `NOTION_USERS_DATABASE_ID` | `""` (env) | ID de la BD de usuarios web (si se conoce). [config.py:67](../../backend/config.py#L67) |
| `NOTION_USERS_DATABASE_NAME` | `"Usuarios Web"` (env) | Nombre de la BD de usuarios web. [config.py:68](../../backend/config.py#L68) |
| `NOTION_PARENT_PAGE_ID` | `""` (env) | ID de la página raíz de la estructura Notion. **Obligatoria para arrancar** (la valida `main`). [config.py:69](../../backend/config.py#L69) |
| `NOTION_ANNUAL_DATABASE_ID` | `""` (env) | ID de la BD de evaluaciones anuales. [config.py:70](../../backend/config.py#L70) |
| `NOTION_ANNUAL_DATABASE_NAME` | `"Evaluaciones anuales"` (env) | Nombre de la BD de evaluaciones anuales. [config.py:71](../../backend/config.py#L71) |
| `NOTION_QUESTIONS_DATABASE_NAME` | `"Preguntas"` (env) | Nombre de la BD de preguntas. [config.py:72](../../backend/config.py#L72) |
| `ANTHROPIC_API_KEY` | `""` (env) | Clave de la API de Claude (Anthropic). Sin ella, la web no genera informes con IA. [config.py:73](../../backend/config.py#L73) |
| `PREGUNTAS` | Lista fija de 2 preguntas | Preguntas iniciales del flujo (proyecto, evaluado). [config.py:74](../../backend/config.py#L74) |
| `IGENERIS_CSS` | Bloque CSS | Hoja de estilos con el diseño de igeneris para las páginas HTML de la web legacy. [config.py:79](../../backend/config.py#L79) |

**Notas:** El valor de `ANTHROPIC_API_KEY` es la clave de acceso al modelo de Claude (Anthropic) usado en la generación de informes; el cliente concreto se instancia en [clients.py](../../backend/clients.py). El bloque `PREGUNTAS` contiene diccionarios con claves `"clave"` y `"texto"`.

---

## `backend/clients.py` — Inicialización de clientes Slack/Notion/Claude

**Propósito:** Instancia los clientes de servicios externos que usa el resto del backend, con imports opcionales tolerantes a la ausencia de dependencias. Ver [clients.py:1](../../backend/clients.py#L1).

Este archivo no define funciones: solo imports condicionales y objetos a nivel de módulo.

### Imports opcionales
- **`Anthropic`** (SDK de Claude): se importa dentro de try/except; si `anthropic` no está instalado, queda como `None`. Ver [clients.py:6](../../backend/clients.py#L6).
- **`Document`** (de `python-docx`): se importa dentro de try/except; si falta, queda como `None`. Ver [clients.py:11](../../backend/clients.py#L11). Se reexporta e importa en `main.py` para avisar si falta.

### Objetos a nivel de módulo
- **`slack_app`**: `App(token=config.SLACK_BOT_TOKEN, token_verification_enabled=False)` — la app de Slack Bolt. Ver [clients.py:17](../../backend/clients.py#L17).
- **`notion`**: `NotionClient(auth=config.NOTION_TOKEN)` — cliente de la API de Notion. Ver [clients.py:18](../../backend/clients.py#L18).
- **`anthropic_client`**: `Anthropic(api_key=config.ANTHROPIC_API_KEY)` si tanto `Anthropic` como la clave están disponibles; si no, `None`. Es el cliente del modelo de Claude usado para generar informes. Ver [clients.py:19](../../backend/clients.py#L19).

**Notas:** `token_verification_enabled=False` desactiva la verificación del token del bot al inicializar (útil para Socket Mode / arranque sin round-trip de verificación).

---

## `backend/state.py` — Estado en memoria thread-safe

**Propósito:** Define el estado compartido en memoria entre los distintos hilos (ciclos de Slack, web) con un lock reentrante para la sincronización. Ver [state.py:1](../../backend/state.py#L1).

No hay funciones; solo un lock y varias estructuras de datos globales.

### Estructuras
- **`lock`**: `threading.RLock()` — lock reentrante para proteger accesos concurrentes al estado compartido. Ver [state.py:4](../../backend/state.py#L4).
- **`evaluaciones_dm_activas`** (`set`): `user_id`s con evaluación por DM activa. [state.py:5](../../backend/state.py#L5).
- **`evaluaciones_dm_expiradas`** (`set`): `user_id`s de la ronda anterior. [state.py:6](../../backend/state.py#L6).
- **`evaluacion_dm_canal`** (`dict`): `user_id` → `dm_channel_id`. [state.py:7](../../backend/state.py#L7).
- **`evaluacion_dm_ts`** (`dict`): `user_id` → `ts` del mensaje inicial (raíz del hilo). [state.py:8](../../backend/state.py#L8).
- **`evaluacion_hora`** (`dict`): `user_id` → timestamp de envío. [state.py:9](../../backend/state.py#L9).
- **`evaluacion_ultimo_recordatorio`** (`dict`): `user_id` → timestamp del último recordatorio. [state.py:10](../../backend/state.py#L10).
- **`conversaciones`** (`dict`): `user_id` → estado de conversación. [state.py:11](../../backend/state.py#L11).
- **`bbdd_por_evaluado`** (`dict`): caché de BD por evaluado. [state.py:12](../../backend/state.py#L12).
- **`sesiones_web`** (`dict`): token de sesión → datos de sesión web (usado por `users.py`). [state.py:13](../../backend/state.py#L13).
- **`password_reset_tokens`** (`dict`): token de reset → datos (usuario, email, caducidad). [state.py:14](../../backend/state.py#L14).
- **`evaluaciones_pendientes`** (`list`): cola de evaluaciones pendientes. [state.py:15](../../backend/state.py#L15).

**Notas:** Al estar en memoria, todo este estado se pierde al reiniciar el proceso (sesiones web y tokens de reset incluidos).

---

## `backend/utils.py` — Utilidades

**Propósito:** Funciones auxiliares de normalización de texto y generación de nombres de archivo seguros. Ver [utils.py:1](../../backend/utils.py#L1).

### `normalizar_nombre(valor)`
- **Qué hace:** Normaliza un texto para comparaciones robustas. Ver [utils.py:4](../../backend/utils.py#L4).
- **Parámetros:** `valor` (texto o `None`).
- **Devuelve:** El texto en minúsculas, sin espacios sobrantes al inicio/fin y con espacios internos colapsados a uno solo. Si `valor` es `None`, opera sobre cadena vacía.
- **Efectos:** Ninguno.
- **Notas:** Se usa por todo el código (usuarios, jerarquía, permisos) como clave canónica de comparación de nombres/emails.

### `slug_archivo(valor)`
- **Qué hace:** Convierte un texto en un slug apto para nombres de archivo. Ver [utils.py:8](../../backend/utils.py#L8).
- **Parámetros:** `valor` (texto).
- **Devuelve:** El texto con cualquier secuencia de caracteres no `[a-zA-Z0-9_-]` reemplazada por `_`, sin `_` al inicio/fin. Si el resultado queda vacío, devuelve `"todas"`.
- **Efectos:** Ninguno.
- **Notas:** Usado para nombrar los archivos `informe_<slug>.html/.docx` y `trayectoria_<slug>.html`.

---

## `backend/hierarchy.py` — Relación jerárquica entre evaluador y evaluado

**Propósito:** Determina la relación jerárquica (superior / inferior / igual) entre dos cargos, para elegir el conjunto de preguntas y la sección correcta en la BD de Preguntas. Ver [hierarchy.py:1](../../backend/hierarchy.py#L1).

### Constante `_NIVELES_CARGO`
- **Qué es:** Diccionario que mapea cada cargo (en minúsculas) a un nivel numérico. Ver [hierarchy.py:1](../../backend/hierarchy.py#L1).
- **Escala general (0–7):** `trainee`=0, `analyst`=1, `associate`=2, `sr. associate`=3, `manager`=4, `sr. manager`=5, `director`=6, `partner`=7.
- **Track Palantir** (mapeado a la escala general): `jr. palantir engineer`=1, `palantir engineer`=2, `palantir sr. engineer`=3. [hierarchy.py:10](../../backend/hierarchy.py#L10).

### `_normalizar_cargo(cargo)`
- **Qué hace:** Normaliza el nombre de un cargo. Ver [hierarchy.py:17](../../backend/hierarchy.py#L17).
- **Parámetros:** `cargo` (`str`).
- **Devuelve:** El cargo en minúsculas, sin espacios extra y con espacios colapsados.
- **Efectos:** Ninguno.

### `nivel_cargo(cargo)`
- **Qué hace:** Obtiene el nivel numérico de un cargo. Ver [hierarchy.py:21](../../backend/hierarchy.py#L21).
- **Parámetros:** `cargo` (`str`).
- **Devuelve:** El nivel entero según `_NIVELES_CARGO`, o `None` si el cargo está vacío o no reconocido.
- **Efectos:** Ninguno.

### `comparar_jerarquia(cargo_evaluador, cargo_evaluado)`
- **Qué hace:** Compara la seniority del evaluador frente al evaluado. Ver [hierarchy.py:27](../../backend/hierarchy.py#L27).
- **Parámetros:** `cargo_evaluador`, `cargo_evaluado` (`str`).
- **Devuelve:** `"superior"` si el evaluador es más senior, `"inferior"` si es menos, `"igual"` en el resto de casos (incluye cuando algún nivel es `None`).
- **Efectos:** Ninguno.

### `sufijo_preguntas(relacion)`
- **Qué hace:** Devuelve el sufijo textual del bloque de preguntas según la relación. Ver [hierarchy.py:40](../../backend/hierarchy.py#L40).
- **Parámetros:** `relacion` (`"superior"`/`"inferior"`/otro).
- **Devuelve:**
  - `"superior"` → `" -EVALUANDO A DEBAJO"`
  - `"inferior"` → `" -EVALUANDO A GENTE DE ARRIBA"`
  - otro → `" -EVALUANDO A GENTE DE MI NIVEL"`
- **Efectos:** Ninguno.

### `tipo_relacion(relacion)`
- **Qué hace:** Convierte la relación jerárquica al nombre de sección de la BD de Preguntas. Ver [hierarchy.py:48](../../backend/hierarchy.py#L48).
- **Parámetros:** `relacion` (`"superior"`/`"inferior"`/otro).
- **Devuelve:**
  - `"superior"` → `"Top-Bottom"`
  - `"inferior"` → `"Bottom-Top"`
  - otro → `"Same Level"`
- **Efectos:** Ninguno.

---

## `backend/i18n.py` — Internacionalización del backend (ES/EN)

**Propósito:** Provee las traducciones de los textos fijos que genera el propio código (interfaz del bot e informes). El contenido escrito a mano en Notion (preguntas, objetivos, comentarios) NO se traduce aquí. Idiomas soportados: `es` (por defecto) y `en`. Ver [i18n.py:1](../../backend/i18n.py#L1).

### Constantes
- **`IDIOMA_POR_DEFECTO`**: `"es"`. [i18n.py:15](../../backend/i18n.py#L15).
- **`IDIOMAS_SOPORTADOS`**: `("es", "en")`. [i18n.py:16](../../backend/i18n.py#L16).
- **`TEXTOS`**: `dict[str, dict[str, str]]` — catálogo de textos indexado por clave, con subclaves `"es"`/`"en"`. Los textos admiten placeholders de `str.format`. Incluye claves del informe (`report.titulo`, `report.generado`, `report.cerrar`, `report.evaluado`, `report.evaluaciones`, `report.fuente`, `report.word_meta`) y `report.prompt` (la instrucción de idioma y formato para el prompt de Claude). [i18n.py:21](../../backend/i18n.py#L21).

### `normalizar_idioma(idioma)`
- **Qué hace:** Valida un código de idioma. Ver [i18n.py:56](../../backend/i18n.py#L56).
- **Parámetros:** `idioma` (`str` o `None`).
- **Devuelve:** El propio idioma si está en `IDIOMAS_SOPORTADOS`; si no (o es `None`), `IDIOMA_POR_DEFECTO` (`"es"`).
- **Efectos:** Ninguno.

### `t(clave, idioma="es", **kwargs)`
- **Qué hace:** Traduce una clave al idioma dado y aplica `str.format` con los kwargs si los hay. Ver [i18n.py:63](../../backend/i18n.py#L63).
- **Parámetros:** `clave` (clave del catálogo), `idioma` (código, por defecto `"es"`), `**kwargs` (valores para los placeholders).
- **Devuelve:** El texto traducido y formateado. Reglas:
  - Si la clave no existe en `TEXTOS`, avisa por log (`warning`) y devuelve la propia clave (formateada si hay kwargs).
  - Si falta la traducción en el idioma pedido, cae al idioma por defecto y, en última instancia, a la clave.
  - Si `str.format` falla por `KeyError`/`IndexError`, devuelve el texto sin formatear.
- **Efectos:** Puede emitir un `logging.warning` para claves sin traducir (útil en desarrollo).

---

## `backend/users.py` — Auth PBKDF2, sesiones, reset por email, roles

**Propósito:** Gestiona los usuarios web: persistencia en la BD "Usuarios Web" de Notion (con fallback a `users.json` local), hashing de contraseñas con PBKDF2, autenticación, sesiones en memoria, registro, roles y flujo de reset de contraseña por email. Ver [users.py:1](../../backend/users.py#L1).

### Modelo de roles (employee / CA / admin)

El modelo de roles del backend distingue efectivamente entre dos niveles según el flag `is_admin`, además del emparejamiento por `persona`:

- **admin** (`is_admin=True`): puede consultar las evaluaciones de cualquier persona. En la web legacy ve el selector de "Persona evaluada" con todas las opciones y su rol se muestra como "Admin". El acceso lo concede `validar_acceso_sesion` sin más comprobaciones ([users.py:444](../../backend/users.py#L444)).
- **employee** (`is_admin=False`): solo puede ver las evaluaciones hechas sobre sí mismo. La coincidencia se hace comparando `normalizar_nombre(sesion["persona"])` con el evaluado ([users.py:446](../../backend/users.py#L446)).
- **CA** (Career Advisor): no es un flag booleano propio en este archivo, sino que se modela mediante el parámetro `extra_permitidos` de `validar_acceso_sesion`: un usuario no-admin puede acceder también a los evaluados que se le pasen explícitamente como permitidos (sus advisees) ([users.py:448](../../backend/users.py#L448)). El campo persistido de cada usuario es `persona` (la persona a la que representa) además de `username`, `email`, `is_admin`, `salt` y `password_hash`.

Cada usuario en Notion tiene las propiedades definidas en `_propiedades_usuarios`: `Name` (title), `Username`, `Persona`, `Email`, `Is admin` (checkbox), `Salt`, `Password hash` y `Fecha alta` (date). Ver [users.py:35](../../backend/users.py#L35).

### Hashing de contraseñas

Se usa **PBKDF2-HMAC-SHA256** con **120 000 iteraciones** y un salt hexadecimal de 16 bytes generado con `secrets.token_hex(16)` (ver `hash_password`, [users.py:243](../../backend/users.py#L243)). La verificación se hace en tiempo constante con `hmac.compare_digest` (ver `verificar_password`, [users.py:249](../../backend/users.py#L249)). Salt y hash se almacenan como texto (`rich_text`) en Notion o en `users.json`.

### Sesiones

Las sesiones viven en memoria en `state.sesiones_web` (token → datos). Se crean con `crear_sesion` usando un token `secrets.token_urlsafe(32)` y almacenan `username`, `persona`, `email` e `is_admin`. Se recuperan por cookie `session` (`obtener_sesion`) o directamente por token (`obtener_sesion_por_token`). Al reiniciar el proceso se pierden.

### Reset de contraseña por email

Flujo en dos pasos:
1. `solicitar_reset_password(email, base_url=None)` localiza al usuario, genera un token `secrets.token_urlsafe(32)` con caducidad de **30 minutos** guardado en `state.password_reset_tokens`, construye la URL `{base}/#/reset/{token}` y envía el email vía SMTP. `base` es la URL pública real desde la que llegó la petición (la API la deriva de las cabeceras `X-Forwarded-Host`/`X-Forwarded-Proto`, validada contra la lista blanca de CORS para evitar *host header injection*); si no se recibe un origen permitido, se cae en `APP_PUBLIC_URL`. Así el enlace apunta al despliegue actual sin depender de configurar la variable a mano.
2. `cambiar_password_con_token(token, nueva_password, confirm_password)` valida el token y su caducidad, valida la robustez de la contraseña, re-hashea y guarda el usuario, y consume el token.

---

### `_ruta_usuarios()`
- **Qué hace:** Devuelve la ruta de `users.json` local. Ver [users.py:31](../../backend/users.py#L31).
- **Devuelve:** `CARPETA_WEB/users.json`.

### `_propiedades_usuarios()`
- **Qué hace:** Define el esquema de propiedades de la BD "Usuarios Web" en Notion. Ver [users.py:35](../../backend/users.py#L35).
- **Devuelve:** `dict` con `Name`, `Username`, `Persona`, `Email`, `Is admin`, `Salt`, `Password hash`, `Fecha alta`.

### `_texto_rich_text(propiedades, nombre_propiedad)`
- **Qué hace:** Extrae el texto plano de una propiedad `rich_text`. Ver [users.py:48](../../backend/users.py#L48).
- **Devuelve:** Cadena concatenada y `strip`.

### `_texto_title(propiedades, nombre_propiedad)`
- **Qué hace:** Extrae el texto plano de una propiedad `title`. Ver [users.py:53](../../backend/users.py#L53).
- **Devuelve:** Cadena concatenada y `strip`.

### `_texto_email(propiedades, nombre_propiedad)`
- **Qué hace:** Extrae el valor de una propiedad `email`. Ver [users.py:58](../../backend/users.py#L58).
- **Devuelve:** El email (`strip`) o cadena vacía.

### `_asegurar_propiedades_usuarios(database_id)`
- **Qué hace:** Garantiza que la BD de usuarios tenga todas las propiedades necesarias; crea las que falten. Ver [users.py:62](../../backend/users.py#L62).
- **Efectos:** Recupera la BD (vía `data_sources` o `databases` según `_usa_data_sources()`) y actualiza solo las propiedades faltantes.

### `_obtener_o_crear_bbdd_usuarios()`
- **Qué hace:** Obtiene (cacheado) o crea la BD "Usuarios Web" en Notion. Ver [users.py:77](../../backend/users.py#L77).
- **Devuelve:** El `database_id` (o data source id).
- **Efectos:** Usa `_cache_users_database_id` como caché de módulo. Si `NOTION_USERS_DATABASE_ID` está configurado, lo usa (resolviendo data source si hace falta). Si no, busca por nombre bajo la página `NOTION_DATA_LISTS_PAGE_NAME`; si no existe, la crea (con soporte para data sources y para el modelo clásico).
- **Notas:** Registra por log la creación de la base.

### `_cargar_usuarios_local()`
- **Qué hace:** Lee `users.json` local. Ver [users.py:131](../../backend/users.py#L131).
- **Devuelve:** El `dict` de usuarios, o `{}` si el archivo no existe.

### `_guardar_usuarios_local(usuarios)`
- **Qué hace:** Escribe `users.json` local de forma atómica. Ver [users.py:139](../../backend/users.py#L139).
- **Efectos:** Escribe a un archivo temporal en `CARPETA_WEB` y hace `os.replace` (escritura atómica).

### `cargar_usuarios()`
- **Qué hace:** Carga todos los usuarios desde Notion (paginado), con fallback local. Ver [users.py:148](../../backend/users.py#L148).
- **Devuelve:** `dict` `clave_normalizada` → datos de usuario (`username`, `persona`, `email`, `is_admin`, `salt`, `password_hash`, `_page_id`).
- **Efectos:** Si falla la lectura de Notion, registra la excepción y cae a `_cargar_usuarios_local()`.
- **Notas:** La clave es `normalizar_nombre(username)`; ignora filas sin username.

### `_pagina_usuario_existente(database_id, clave)`
- **Qué hace:** Busca el `_page_id` de un usuario ya existente por su clave. Ver [users.py:182](../../backend/users.py#L182).
- **Devuelve:** El `_page_id` o `None`.

### `guardar_usuario(usuario)`
- **Qué hace:** Crea o actualiza un único usuario en Notion. Ver [users.py:189](../../backend/users.py#L189).
- **Efectos:** Si el usuario ya tiene página, la actualiza; si no, crea una nueva añadiendo `Fecha alta`. Ante error de Notion, actualiza el fallback local (excluyendo claves con prefijo `_`).

### `guardar_usuarios(usuarios)`
- **Qué hace:** Crea/actualiza en lote todos los usuarios en Notion. Ver [users.py:218](../../backend/users.py#L218).
- **Efectos:** Itera sobre el `dict`, actualizando o creando cada página. Ante error, cae a `_guardar_usuarios_local`.

### `hash_password(password, salt=None)`
- **Qué hace:** Deriva el hash PBKDF2 de una contraseña. Ver [users.py:243](../../backend/users.py#L243).
- **Parámetros:** `password`, `salt` (si `None`, genera uno con `secrets.token_hex(16)`).
- **Devuelve:** Tupla `(salt, hash_hex)` — PBKDF2-HMAC-SHA256, 120 000 iteraciones.
- **Efectos:** Ninguno.

### `verificar_password(password, salt, password_hash)`
- **Qué hace:** Verifica una contraseña frente a su hash. Ver [users.py:249](../../backend/users.py#L249).
- **Devuelve:** `True`/`False` usando `hmac.compare_digest` (comparación en tiempo constante).

### `validar_password_segura(password)`
- **Qué hace:** Valida la robustez de la contraseña. Ver [users.py:254](../../backend/users.py#L254).
- **Efectos:** Lanza `ValueError` si tiene menos de 8 caracteres, si no tiene ninguna mayúscula o si no tiene ningún carácter especial (no alfanumérico).

### `_buscar_usuario_por_email_empleado(usuarios, email)`
- **Qué hace:** Resuelve un usuario a partir de un email presente en la Lista de empleados (email o aliases). Ver [users.py:263](../../backend/users.py#L263).
- **Devuelve:** El usuario cuyo `persona` coincide con el nombre del empleado que tiene ese email, o `None`.
- **Efectos:** Consulta `obtener_registros_empleados()` (protegido con try/except).

### `_usuario_por_login_o_email(login)`
- **Qué hace:** Localiza un usuario por username, por email directo o por email de empleado. Ver [users.py:285](../../backend/users.py#L285).
- **Devuelve:** Tupla `(usuarios, usuario)` (el segundo puede ser `None`).

### `_enviar_email_reset(destinatario, reset_url)`
- **Qué hace:** Envía el correo de reset de contraseña por SMTP. Ver [users.py:299](../../backend/users.py#L299).
- **Efectos:** Si falta `SMTP_HOST` o `SMTP_FROM`, lanza `RuntimeError`. Construye un `EmailMessage` (asunto "Restablece tu contraseña", cuerpo con el enlace y aviso de caducidad de 30 minutos) y lo envía; usa `starttls()` si `SMTP_USE_TLS` y `login()` si hay usuario/contraseña.

### `solicitar_reset_password(email)`
- **Qué hace:** Inicia el flujo de reset: genera token y envía el email. Ver [users.py:322](../../backend/users.py#L322).
- **Parámetros:** `email`.
- **Efectos:** Valida el email (lanza `ValueError` si no es válido). Si no encuentra usuario, retorna silenciosamente (no revela si el email existe). Genera token `token_urlsafe(32)` con caducidad de 30 minutos en `password_reset_tokens` (bajo `lock`), construye la URL `{APP_PUBLIC_URL}/#/reset/{token}` y envía el correo.

### `cambiar_password_con_token(token, nueva_password, confirm_password=None)`
- **Qué hace:** Completa el reset cambiando la contraseña con un token válido. Ver [users.py:343](../../backend/users.py#L343).
- **Efectos:**
  - Lanza `ValueError` si faltan token o contraseña, o si `confirm_password` no coincide.
  - Valida robustez con `validar_password_segura`.
  - Bajo `lock`, comprueba que el token exista y no haya caducado (si no, lo elimina y lanza `PermissionError`).
  - Localiza el usuario; si no existe, lanza `PermissionError`.
  - Re-hashea, guarda con `guardar_usuario` y consume el token.

### `registrar_usuario(username, password)`
- **Qué hace:** Registra un nuevo usuario. Ver [users.py:372](../../backend/users.py#L372).
- **Efectos:** Valida que username/password no estén vacíos y la robustez de la contraseña. Si el usuario ya existe, lanza `ValueError`. Crea el usuario con `is_admin=False` y `persona=username`, y persiste con `guardar_usuarios`.

### `autenticar_usuario(username, password)`
- **Qué hace:** Autentica un usuario por username, email directo o email de empleado. Ver [users.py:395](../../backend/users.py#L395).
- **Devuelve:** El usuario si las credenciales son correctas.
- **Efectos:** Si no se encuentra el usuario o la contraseña no verifica, lanza `PermissionError("Usuario o contraseña incorrectos.")`.

### `crear_sesion(usuario)`
- **Qué hace:** Crea una sesión web para un usuario autenticado. Ver [users.py:411](../../backend/users.py#L411).
- **Devuelve:** El token de sesión (`token_urlsafe(32)`).
- **Efectos:** Guarda en `sesiones_web[token]` los datos `username`, `persona`, `email`, `is_admin`.

### `obtener_cookie(headers, nombre)`
- **Qué hace:** Extrae el valor de una cookie de los headers HTTP. Ver [users.py:422](../../backend/users.py#L422).
- **Devuelve:** El valor de la cookie `nombre` o cadena vacía.

### `obtener_sesion(headers)`
- **Qué hace:** Recupera la sesión a partir de la cookie `session`. Ver [users.py:433](../../backend/users.py#L433).
- **Devuelve:** Los datos de sesión o `None`.

### `obtener_sesion_por_token(token)`
- **Qué hace:** Recupera la sesión directamente por su token. Ver [users.py:437](../../backend/users.py#L437).
- **Devuelve:** Los datos de sesión o `None`.

### `validar_acceso_sesion(sesion, evaluado, extra_permitidos=None)`
- **Qué hace:** Autoriza el acceso de una sesión a las evaluaciones de un evaluado. Ver [users.py:441](../../backend/users.py#L441).
- **Parámetros:** `sesion`, `evaluado`, `extra_permitidos` (lista opcional de evaluados adicionales permitidos — el mecanismo del rol CA/advisees).
- **Efectos:** Lanza `PermissionError` si no hay sesión, o si la sesión no es admin y el evaluado no coincide con su `persona` ni está en `extra_permitidos`. Los admin pasan siempre.

---

## `backend/web_server.py` — Web antigua integrada (WEB_MODE=legacy)

**Propósito:** Servidor HTTP integrado (basado en `http.server`) que sirve la web legacy: login/registro, home con generación de informes y trayectorias, y descarga protegida de archivos. Solo se usa cuando `WEB_MODE == "legacy"`. Ver [web_server.py:1](../../backend/web_server.py#L1).

### Clase `ReusableTCPServer(socketserver.TCPServer)`
- **Qué es:** Subclase de `TCPServer` con `allow_reuse_address = True` para poder reiniciar sin esperar a liberar el puerto. Ver [web_server.py:15](../../backend/web_server.py#L15).

### Clase `WebHandler(SimpleHTTPRequestHandler)`
- **Qué es:** Handler de peticiones que sirve estáticos desde `CARPETA_WEB` y gestiona las rutas de la web legacy. Ver [web_server.py:19](../../backend/web_server.py#L19).

#### `__init__(self, *args, **kwargs)`
- **Qué hace:** Inicializa el handler fijando `directory=config.CARPETA_WEB`. Ver [web_server.py:20](../../backend/web_server.py#L20).

#### `log_message(self, *args, **kwargs)`
- **Qué hace:** Silencia el logging por defecto del servidor HTTP. Ver [web_server.py:23](../../backend/web_server.py#L23).

#### `responder_html(self, contenido, status=200)`
- **Qué hace:** Responde con contenido HTML UTF-8 y el status indicado. Ver [web_server.py:26](../../backend/web_server.py#L26).

#### `redirect(self, destino, cookie=None)`
- **Qué hace:** Envía una redirección 303 a `destino`, opcionalmente fijando una cookie (`Set-Cookie`). Ver [web_server.py:34](../../backend/web_server.py#L34).

#### `servir_archivo(self, nombre_archivo, content_type)`
- **Qué hace:** Sirve un archivo de `CARPETA_WEB` con el `content_type` dado. Ver [web_server.py:41](../../backend/web_server.py#L41).
- **Efectos:** Devuelve 404 si el archivo no existe. Usa `os.path.basename` para evitar traversal de directorios.

#### `servir_archivo_protegido(self, ruta, query)`
- **Qué hace:** Sirve un archivo `informe_*`/`trayectoria_*` solo si la sesión tiene acceso al evaluado. Ver [web_server.py:54](../../backend/web_server.py#L54).
- **Efectos:** Valida el acceso con `validar_acceso_sesion` (403 si falla), comprueba que el nombre del archivo coincida con el slug del evaluado autorizado (403 si no), determina el content type (docx o HTML) y sirve el archivo.

#### `opciones_evaluados(self)`
- **Qué hace:** Genera las `<option>` del selector de evaluados según los permisos de la sesión. Ver [web_server.py:71](../../backend/web_server.py#L71).
- **Devuelve:** El HTML de opciones (solo las accesibles; los no-admin solo se ven a sí mismos). Si no hay tablas, una opción vacía.

#### `evaluado_usuario(self)`
- **Qué hace:** Determina el nombre de evaluado que corresponde al usuario logueado. Ver [web_server.py:82](../../backend/web_server.py#L82).
- **Devuelve:** El evaluado coincidente con su `persona`, o su `persona`, o cadena vacía si no hay sesión.

#### `pagina_error(self, titulo, mensaje, status=500)`
- **Qué hace:** Renderiza una página de error HTML sencilla (con escape). Ver [web_server.py:91](../../backend/web_server.py#L91).

#### `pagina_login(self, mensaje="")`
- **Qué hace:** Renderiza la página de login con estilo igeneris (usa `config.IGENERIS_CSS`). Ver [web_server.py:95](../../backend/web_server.py#L95).

#### `pagina_registro(self, mensaje="")`
- **Qué hace:** Renderiza la página de registro. Ver [web_server.py:102](../../backend/web_server.py#L102).

#### `pagina_home(self)`
- **Qué hace:** Renderiza la home autenticada con las herramientas de "Informe" y "Trayectoria". Ver [web_server.py:109](../../backend/web_server.py#L109).
- **Efectos:** Redirige a `/login` si no hay sesión. Para admin muestra el selector de persona; para no-admin usa un campo oculto con su propio evaluado.

#### `do_GET(self)`
- **Qué hace:** Enruta las peticiones GET. Ver [web_server.py:127](../../backend/web_server.py#L127).
- **Rutas:** `/login`, `/register`, `/logout` (borra cookie), `/users.json` (404 explícito por seguridad), `/informe*` y `/trayectoria*` (archivo protegido), `/` e `/index.html` (home); el resto delega en `SimpleHTTPRequestHandler.do_GET`.

#### `do_POST(self)`
- **Qué hace:** Procesa formularios POST. Ver [web_server.py:144](../../backend/web_server.py#L144).
- **Rutas permitidas:** `/login`, `/register`, `/generar`, `/trayectoria` (resto = 404).
- **Efectos:** Limita el body a 1 000 000 bytes. Para `/register` crea el usuario y redirige a login; para `/login` autentica y fija cookie de sesión; para `/generar` llama a `generar_archivos_informe` (indicando si vino de caché); para `/trayectoria` llama a `generar_archivo_trayectoria`. Valida el acceso al evaluado antes de generar. Gestiona `PermissionError` (403) y otros errores (500 con log).

### `iniciar_servidor_web()`
- **Qué hace:** Arranca el servidor web legacy. Ver [web_server.py:186](../../backend/web_server.py#L186).
- **Efectos:** Crea `CARPETA_WEB` si no existe y sirve indefinidamente en `config.PUERTO_WEB`. Si el puerto está ocupado (`OSError`), registra un error explicando cómo cambiar el puerto (`$env:PUERTO_WEB="8001"`).
- **Notas:** Es la función que `main.py` lanza en un hilo cuando `WEB_MODE == "legacy"`.

---

## `backend/create_users_from_employees.py` — Crea usuarios web desde la lista de empleados

**Propósito:** Script/CLI que crea (o repara) usuarios web a partir de la "Lista de empleados" de Notion, generando usernames y contraseñas temporales y volcándolos a un CSV. Ejecutable con `--apply` para escribir en Notion (por defecto es dry-run). Ver [create_users_from_employees.py:1](../../backend/create_users_from_employees.py#L1).

### `_sin_acentos(valor)`
- **Qué hace:** Elimina los acentos/diacríticos de un texto. Ver [create_users_from_employees.py:13](../../backend/create_users_from_employees.py#L13).
- **Devuelve:** El texto sin marcas de acento (normalización NFD, descartando categoría `Mn`).

### `_parte_usuario(valor)`
- **Qué hace:** Deja solo los caracteres alfanuméricos (sin acentos) de un texto. Ver [create_users_from_employees.py:20](../../backend/create_users_from_employees.py#L20).
- **Devuelve:** Cadena solo con caracteres `isalnum`.

### `username_base(nombre)`
- **Qué hace:** Genera un username base a partir de un nombre. Ver [create_users_from_employees.py:25](../../backend/create_users_from_employees.py#L25).
- **Devuelve:** Toma como máximo las dos primeras palabras, capitaliza la inicial de cada una y concatena (p.ej. "Ana López" → "ALopez"). Cadena vacía si no hay partes válidas.

### `username_unico(nombre, usados)`
- **Qué hace:** Genera un username único no colisionante con los ya usados. Ver [create_users_from_employees.py:36](../../backend/create_users_from_employees.py#L36).
- **Parámetros:** `nombre`, `usados` (set de claves normalizadas ya en uso).
- **Devuelve:** El username base o, si colisiona, con sufijo numérico (`base2`, `base3`, …). Añade la clave a `usados`.

### `password_temporal()`
- **Qué hace:** Genera una contraseña temporal. Ver [create_users_from_employees.py:49](../../backend/create_users_from_employees.py#L49).
- **Devuelve:** `"Cambio-" + secrets.token_urlsafe(8)`.

### `crear_usuarios(apply=False, output=None, password=None)`
- **Qué hace:** Lógica central: lee empleados, crea usuarios nuevos, repara los existentes sin contraseña, opcionalmente aplica en Notion y genera un CSV. Ver [create_users_from_employees.py:53](../../backend/create_users_from_employees.py#L53).
- **Parámetros:** `apply` (si escribe en Notion), `output` (ruta del CSV), `password` (contraseña temporal común; si se pasa, se valida su robustez).
- **Devuelve:** Tupla `(empleados, creados, actualizados, saltos, output)`.
- **Efectos:**
  - Lee `obtener_registros_empleados()` y los deduplica por nombre normalizado.
  - Para cada empleado: si ya existe un usuario asociado a esa `persona`, actualiza su email si cambió; si ese usuario ya tiene salt+hash lo salta (registra si actualizó email), y si no tiene credenciales las genera (reparación). Si no existe usuario, crea uno nuevo con username único.
  - Si `apply` y hubo cambios, persiste con `guardar_usuarios`.
  - Escribe un CSV (por defecto `dashboard_web/usuarios_web_creados.csv`) con `Nombre`, `Usuario`, `Email`, `Password temporal` para creados y actualizados.

### `main()`
- **Qué hace:** Punto de entrada CLI. Ver [create_users_from_employees.py:139](../../backend/create_users_from_employees.py#L139).
- **Parámetros CLI:** `--apply` (escribir en Notion), `--output` (ruta CSV), `--password` (contraseña común).
- **Efectos:** Llama a `crear_usuarios` y muestra un resumen (`APLICADO`/`DRY-RUN`, contadores y ruta del CSV). Si no se usó `--apply`, recuerda que no se ha escrito en Notion. Lista los saltados con su motivo.
- **Notas:** Ejecutable directo (`if __name__ == "__main__"`). Por defecto es dry-run seguro.

---

## `migration_notion.py` — Script de migración de la estructura Notion

**Propósito:** Script de **un solo uso** (`python migration_notion.py`) que reorganiza la estructura de Notion a la nueva arquitectura (TO-DO / TO-SEE y subpáginas), moviendo y renombrando las BD/páginas existentes. Es idempotente: si algo ya está en su sitio, lo omite sin error. Carga el `.env` antes de importar el backend. Ver [migration_notion.py:1](../../migration_notion.py#L1).

El docstring del archivo ([migration_notion.py:12](../../migration_notion.py#L12)) documenta el árbol de destino completo (Evaluaciones continuas → TO-DO / TO-SEE con todas sus subpáginas y BD).

### Bloque de carga inicial
- Intenta cargar `.env` con `python-dotenv` (si no está instalado, asume que las vars ya están en el entorno) ([migration_notion.py:47](../../migration_notion.py#L47)) y añade el directorio raíz al `sys.path` ([migration_notion.py:54](../../migration_notion.py#L54)). Configura logging a nivel `INFO`.

### `crear_pagina(parent_id, nombre)`
- **Qué hace:** Crea una página con el nombre dado bajo `parent_id`. Ver [migration_notion.py:74](../../migration_notion.py#L74).
- **Devuelve:** El ID de la página creada.
- **Efectos:** Llama a `notion.pages.create` y registra por log.

### `mover_pagina(page_id, new_parent_id)`
- **Qué hace:** Mueve una página o BD al nuevo padre. Ver [migration_notion.py:84](../../migration_notion.py#L84).
- **Efectos:** Intenta `notion.pages.update`; si falla (porque es una BD), reintenta con `notion.databases.update`.

### `renombrar_pagina(page_id, nuevo_nombre)`
- **Qué hace:** Renombra una página. Ver [migration_notion.py:99](../../migration_notion.py#L99).

### `renombrar_bbdd(db_id, nuevo_nombre)`
- **Qué hace:** Renombra una base de datos. Ver [migration_notion.py:107](../../migration_notion.py#L107).

### `obtener_o_crear_pagina(parent_id, nombre)`
- **Qué hace:** Devuelve el ID de la página con ese nombre bajo `parent_id`; la crea si no existe. Ver [migration_notion.py:115](../../migration_notion.py#L115).
- **Devuelve:** El ID (existente o recién creado).

### `buscar_en_raiz(root_id, nombre)`
- **Qué hace:** Busca directamente bajo `root_id` por nombre (child_page o child_database). Ver [migration_notion.py:124](../../migration_notion.py#L124).
- **Devuelve:** El ID o `None`.

### `buscar_global(nombre)`
- **Qué hace:** Busca globalmente en Notion por nombre exacto (BD y páginas). Ver [migration_notion.py:129](../../migration_notion.py#L129).
- **Devuelve:** El ID del primer objeto cuyo título coincide (case-insensitive), o `None`.

### `mover_si_existe(nombre_buscar, nuevo_parent_id, nuevo_nombre=None, *, root_id=None, pagina_origen=None)`
- **Qué hace:** Busca un objeto y lo mueve (y opcionalmente renombra) al nuevo padre. Ver [migration_notion.py:151](../../migration_notion.py#L151).
- **Devuelve:** El ID del objeto movido, o `None` si no se encontró.
- **Efectos:** Busca por orden en `pagina_origen`, luego en `root_id`, luego globalmente. Mueve, intenta renombrar (como página y, si falla, como BD) y aplica `time.sleep(0.3)` para respetar los rate limits de Notion.

### `crear_bbdd_si_no_existe(parent_id, nombre, props)`
- **Qué hace:** Crea una BD bajo `parent_id` si no existe ya una con ese nombre. Ver [migration_notion.py:196](../../migration_notion.py#L196).
- **Devuelve:** El ID de la BD (existente o nueva), o `None` si falla la creación.

### `_props_preguntas_seguimiento_ca()`
- **Qué hace:** Devuelve el esquema de la BD "Preguntas seguimiento CA" (`Clave` title, `Texto` rich_text). Ver [migration_notion.py:220](../../migration_notion.py#L220).

### `_props_preguntas_eval_personal()`
- **Qué hace:** Devuelve el esquema de la BD "Preguntas evaluación personal" (`Clave` title, `Texto` rich_text). Ver [migration_notion.py:227](../../migration_notion.py#L227).

### `poblar_preguntas_seguimiento_ca(db_id)`
- **Qué hace:** Inserta las preguntas del flujo CA (`advisee`, `opinion`) en la BD, sin duplicar las existentes. Ver [migration_notion.py:234](../../migration_notion.py#L234).
- **Efectos:** Consulta las preguntas ya presentes y añade solo las que faltan; registra cada inserción por log.

### `main()`
- **Qué hace:** Ejecuta la migración completa en 7 fases. Ver [migration_notion.py:269](../../migration_notion.py#L269).
- **Efectos:**
  1. Obtiene la página raíz (`_parent_bbdd_referencia`); aborta con `sys.exit(1)` si falla.
  2. **Fase 1:** crea TO-DO, TO-SEE y subpáginas (Datos a Monitorizar, Datos opcionalmente modificables, Preguntas Chatbot, Resultados Evaluaciones, Activaciones de permisos).
  3. **Fase 2:** mueve a "Datos a Monitorizar" (Lista de empleados, Lista de CAs, Usuarios Web, Gestión de MiddleOffice).
  4. **Fase 3:** mueve a "Datos opcionalmente modificables" (Criterios de Evaluaciones, Evaluacion al finalizar proyecto, Ejemplos de Guia para bot).
  5. **Fase 4:** mueve "Preguntas" a "Preguntas Chatbot" (renombrando a mensual), crea "Preguntas seguimiento CA" y la puebla, y gestiona "Preguntas evaluación personal".
  6. **Fase 5:** mueve resultados (Evaluaciones Individuales, Seguimiento CA, Registros barbecho, Respuestas) renombrándolos.
  7. **Fase 6:** mueve a "Activaciones de permisos" (Acceso Individual Advisee, Acceso Evaluaciones Proyecto).
  8. **Fase 7:** mueve a TO-SEE (Informes finales, Objetivos Empleados).
  9. Imprime un resumen con los IDs importantes para el `.env`.
- **Notas:** Ejecutable directo (`if __name__ == "__main__"`). Tolera nombres antiguos alternativos (p.ej. "Lista CA"/"Lista de CAs", "Usarios web" con typo).