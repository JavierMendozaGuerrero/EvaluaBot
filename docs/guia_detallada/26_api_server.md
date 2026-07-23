# `backend/api_server.py` — API REST (puerto 8000)

**Propósito:** Implementa la API REST que consume el frontend React de EvaluaBot. No usa Flask: está construida directamente sobre `http.server.BaseHTTPRequestHandler` con un servidor `ThreadingHTTPServer` (multihilo). Expone todos los endpoints `/api/...` para autenticación, gestión de evaluaciones (proyecto, personal, Slack), tutela de advisees (CA), generación de informes/PDF, objetivos, evaluación anual asistida, opiniones de CA y servido protegido de archivos generados. La clase principal es [`ApiHandler`](../backend/api_server.py#L97) y el arranque lo hace [`iniciar_api_backend()`](../backend/api_server.py#L1031).

**Autenticación:** Se resuelve en [`sesion_actual()`](../backend/api_server.py#L148). Acepta dos vías para el token de sesión:
- Cabecera HTTP `Authorization: Bearer <token>` (vía principal usada por el frontend).
- Parámetro de query `?token=<token>` (usado principalmente para servir archivos/descargas donde no se puede añadir cabecera, p. ej. enlaces directos).

El token se valida contra la capa de sesiones (`obtener_sesion_por_token`, del módulo `users`). Si no hay sesión válida, cada endpoint protegido lanza `PermissionError("Inicia sesión para acceder.")`, que se traduce a un HTTP 403. Los endpoints públicos (health, login, register, reset de contraseña) no exigen sesión.

**CORS:** Se configura en [`end_headers()`](../backend/api_server.py#L101), que se ejecuta en toda respuesta. El origen se refleja (echo) solo si pertenece a la lista blanca `{config.FRONTEND_ORIGIN, http://localhost:5173, http://127.0.0.1:5173}`; en caso contrario se devuelve `config.FRONTEND_ORIGIN`. Se permiten credenciales (`Access-Control-Allow-Credentials: true`), las cabeceras `Content-Type, Authorization` y los métodos `GET, POST, DELETE, OPTIONS`. Las preflight `OPTIONS` responden 204 en [`do_OPTIONS()`](../backend/api_server.py#L114).

**Control de acceso por rol:** Se combinan varios niveles:
- **Autenticado:** cualquier sesión válida.
- **CA (Career Advisor / tutor):** el acceso a datos de un `evaluado`/`advisee` se comprueba con `obtener_advisees(...)` (comparando nombres normalizados) y, en el caso de la evaluación anual y PDFs de fuente, con el helper [`_exigir_acceso_advisee()`](../backend/api_server.py#L155).
- **Admin:** `sesion.get("is_admin")` da acceso total y evita las comprobaciones de tutela.
- **Propio (self-service):** en informes/trayectoria el propio empleado puede ver su documento solo si su CA ha activado el acceso (`ca_tiene_acceso_activo` / `advisee_tiene_acceso_individual`).

**Cómo enruta las peticiones:** No hay router declarativo. Cada método HTTP (`do_GET`, `do_POST`, `do_DELETE`) parsea la ruta con `urllib.parse.urlparse(self.path).path` y encadena comparaciones `if ruta == "..."` (o `ruta.startswith(...)` para prefijos como `/api/eval-anual/` y `/api/files/`). Si ninguna coincide, responde 404. Todo el cuerpo va dentro de un `try/except` que captura `PermissionError` → 403 y cualquier otra excepción → 500 (registrando el traceback con `logging.exception`). Las respuestas se serializan con [`responder_json()`](../backend/api_server.py#L134), que aplica gzip si el cliente lo acepta y el cuerpo supera 1 KB.

---

## Referencia de endpoints

Acceso: **público** = sin sesión; **auth** = cualquier sesión válida; **CA** = requiere tutelar al advisee/evaluado (o ser admin); **self** = el propio empleado con acceso activado por su CA; **admin** = solo `is_admin`.

### GET

| Método | Ruta | Qué hace | Acceso | Función que lo maneja |
|--------|------|----------|--------|-----------------------|
| GET | `/api/health` | Devuelve `{"ok": true}` (chequeo de salud) | público | [`do_GET`](../backend/api_server.py#L170) |
| GET | `/api/me` | Devuelve la sesión actual + idioma de la persona; `{"user": null}` si no hay sesión | público (auth opcional) | [`do_GET`](../backend/api_server.py#L173) |
| GET | `/api/evaluados` | Lista de BBDD de evaluados (el usuario no-admin solo se ve a sí mismo) con foto | auth | [`do_GET`](../backend/api_server.py#L181) |
| GET | `/api/mis-advisees` | Advisees del CA (tutelados + con opiniones de CA), deduplicados, con datos de empleado | auth (CA) | [`do_GET`](../backend/api_server.py#L196) |
| GET | `/api/opiniones-ca` | Opiniones del CA sobre un `advisee` (query `advisee`) | auth | [`do_GET`](../backend/api_server.py#L214) |
| GET | `/api/mi-perfil` | Perfil del empleado de la sesión | auth | [`do_GET`](../backend/api_server.py#L228) |
| GET | `/api/perfil-empleado` | Perfil de cualquier empleado (query `nombre`) | admin | [`do_GET`](../backend/api_server.py#L235) |
| GET | `/api/criterios-evaluacion` | Criterios de evaluación por `grupo` (negocio/palantir/middleoffice) | auth | [`do_GET`](../backend/api_server.py#L246) |
| GET | `/api/objetivos` | Objetivos de una persona (query `nombre`) | auth | [`do_GET`](../backend/api_server.py#L257) |
| GET | `/api/evaluados-anual` | Empleados con evaluación anual pendiente/disponible | admin | [`do_GET`](../backend/api_server.py#L266) |
| GET | `/api/acceso-advisees` | Indica si el CA tiene el acceso global de advisees activado | auth (CA) | [`do_GET`](../backend/api_server.py#L275) |
| GET | `/api/acceso-advisee-individual` | Indica si un `advisee` concreto tiene acceso individual activo | auth (CA) | [`do_GET`](../backend/api_server.py#L283) |
| GET | `/api/informe-final` | URLs del informe final (docx/html) de un `evaluado`; con lógica self-service | CA / self / admin | [`do_GET`](../backend/api_server.py#L295) |
| GET | `/api/evaluaciones-proyecto-activas` | Proyectos con evaluación activa para la persona | auth | [`do_GET`](../backend/api_server.py#L340) |
| GET | `/api/evaluaciones-proyecto-completadas` | Evaluaciones completadas por la persona en un `proyecto` | auth | [`do_GET`](../backend/api_server.py#L348) |
| GET | `/api/todos-empleados` | Lista de nombres de todos los empleados (ordenada) | auth | [`do_GET`](../backend/api_server.py#L361) |
| GET | `/api/preguntas-evaluacion-proyecto` | Preguntas de evaluación por `tipo` (valida contra `LABELS_TIPOS`) | auth | [`do_GET`](../backend/api_server.py#L375) |
| GET | `/api/equipo-proyecto` | Empleados del equipo de un `proyecto` | auth | [`do_GET`](../backend/api_server.py#L387) |
| GET | `/api/proyectos-manager` | Proyectos que gestiona la persona como manager | auth | [`do_GET`](../backend/api_server.py#L396) |
| GET | `/api/estado-proyecto` | Estado de las evaluaciones de un `proyecto` | auth | [`do_GET`](../backend/api_server.py#L404) |
| GET | `/api/eval-anual/estado` | Estado de la sesión de evaluación anual de un `evaluado` | CA / admin | [`do_GET`](../backend/api_server.py#L426) |
| GET | `/api/eval-anual/area` | Datos de un área (`clave`) de la evaluación anual | CA / admin | [`do_GET`](../backend/api_server.py#L429) |
| GET | `/api/files/<archivo>` | Sirve un archivo generado protegido (docx/pdf/html) | CA / self / admin | [`servir_archivo_protegido`](../backend/api_server.py#L435) |
| GET | `/api/estado-ciclo-slack` | Indica si la persona ya completó las evaluaciones (proyecto/personal) del ciclo | auth | [`do_GET`](../backend/api_server.py#L438) |
| GET | `/api/buscar-empleado-slack` | Busca empleado y devuelve relación jerárquica + preguntas según `area` | auth | [`do_GET`](../backend/api_server.py#L470) |
| GET | `/api/resumen-evaluaciones-advisee` | Resumen de evaluaciones de un `advisee` para su CA | auth (CA) | [`do_GET`](../backend/api_server.py#L527) |
| GET | `/api/historial-evaluaciones` | Historial de evaluaciones entre `evaluado`/`evaluador` (opcional `proyecto`) | auth | [`do_GET`](../backend/api_server.py#L547) |

### POST

| Método | Ruta | Qué hace | Acceso | Función que lo maneja |
|--------|------|----------|--------|-----------------------|
| POST | `/api/notas-ca` | Guarda una nota de CA sobre un `advisee` | auth (CA) | [`do_POST`](../backend/api_server.py#L578) |
| POST | `/api/register` | Registra un nuevo usuario | público | [`do_POST`](../backend/api_server.py#L591) |
| POST | `/api/login` | Autentica y devuelve token + datos de usuario | público | [`do_POST`](../backend/api_server.py#L595) |
| POST | `/api/password-reset/request` | Solicita reset de contraseña por email | público | [`do_POST`](../backend/api_server.py#L602) |
| POST | `/api/password-reset/confirm` | Confirma el reset con token + nueva contraseña | público | [`do_POST`](../backend/api_server.py#L606) |
| POST | `/api/generar` | Genera informe (HTML + informe anual IGENERIS docx/html) de un `evaluado` | CA / admin | [`do_POST`](../backend/api_server.py#L614) |
| POST | `/api/generar-opiniones-ca` | Genera PDF/HTML del resumen de opiniones de CA | CA / admin | [`do_POST`](../backend/api_server.py#L641) |
| POST | `/api/generar-pdf-evals-proyecto` | Genera PDF de evaluaciones de proyecto (fuente) | CA / admin | [`do_POST`](../backend/api_server.py#L659) |
| POST | `/api/generar-pdf-seguimiento` | Genera PDF de seguimiento personal (fuente) | CA / admin | [`do_POST`](../backend/api_server.py#L659) |
| POST | `/api/generar-pdf-evals-mensuales` | Genera PDF de evaluaciones mensuales (fuente) | CA / admin | [`do_POST`](../backend/api_server.py#L659) |
| POST | `/api/generar-pdf-completo` | Genera PDF con la información completa (fuente) | CA / admin | [`do_POST`](../backend/api_server.py#L659) |
| POST | `/api/trayectoria` | Genera el HTML de trayectoria de un `evaluado` | CA / self / admin | [`do_POST`](../backend/api_server.py#L675) |
| POST | `/api/objetivos` | Crea/guarda un objetivo para una persona | auth (CA) | [`do_POST`](../backend/api_server.py#L692) |
| POST | `/api/generar-anual` | Genera el informe anual (docx/html) de un `evaluado` | admin | [`do_POST`](../backend/api_server.py#L705) |
| POST | `/api/eval-anual/iniciar` | Inicia la sesión de evaluación anual asistida | CA / admin | [`do_POST`](../backend/api_server.py#L725) |
| POST | `/api/eval-anual/confirmar-identidad` | Confirma la identidad del evaluado en la sesión anual | CA / admin | [`do_POST`](../backend/api_server.py#L728) |
| POST | `/api/eval-anual/responder-area` | Registra la respuesta de un área (`clave`, `texto`) | CA / admin | [`do_POST`](../backend/api_server.py#L731) |
| POST | `/api/eval-anual/confirmar-area` | Confirma un área de la evaluación anual | CA / admin | [`do_POST`](../backend/api_server.py#L735) |
| POST | `/api/eval-anual/finalizar` | Finaliza la sesión y devuelve URLs del informe anual | CA / admin | [`do_POST`](../backend/api_server.py#L739) |
| POST | `/api/acceso-advisees` | Activa/desactiva el acceso global de advisees del CA | auth (CA) | [`do_POST`](../backend/api_server.py#L748) |
| POST | `/api/acceso-advisee-individual` | Activa/desactiva el acceso individual de un `advisee` | auth (CA) | [`do_POST`](../backend/api_server.py#L756) |
| POST | `/api/subir-informe-final` | Sube (multipart) el docx del informe final y lo convierte a HTML | CA / admin | [`do_POST`](../backend/api_server.py#L767) |
| POST | `/api/activar-evaluaciones-proyecto` | Activa evaluaciones de proyecto para una lista de empleados | auth (manager) | [`do_POST`](../backend/api_server.py#L817) |
| POST | `/api/modificar-equipo-proyecto` | Añade o elimina un miembro de un proyecto | auth (manager) | [`do_POST`](../backend/api_server.py#L830) |
| POST | `/api/guardar-evaluacion-proyecto` | Guarda una evaluación de proyecto en Notion | auth | [`do_POST`](../backend/api_server.py#L844) |
| POST | `/api/urgencia-personal` | Notifica una urgencia personal (vía web) | auth | [`do_POST`](../backend/api_server.py#L863) |
| POST | `/api/guardar-evaluacion-personal` | Guarda la autoevaluación/comentario personal en Notion | auth | [`do_POST`](../backend/api_server.py#L872) |
| POST | `/api/guardar-evaluacion-slack` | Crea una evaluación (flujo Slack) en Notion; calcula relación jerárquica | auth | [`do_POST`](../backend/api_server.py#L887) |
| POST | `/api/actualizar-evaluacion-slack` | Actualiza una evaluación existente (`page_id`) en Notion | auth | [`do_POST`](../backend/api_server.py#L909) |

### DELETE

| Método | Ruta | Qué hace | Acceso | Función que lo maneja |
|--------|------|----------|--------|-----------------------|
| DELETE | `/api/objetivos` | Elimina un objetivo por `page_id` | auth | [`do_DELETE`](../backend/api_server.py#L948) |

---

## Referencia de funciones/métodos internos

### `ReusableTCPServer`

Clase servidor ([api_server.py:92](../backend/api_server.py#L92)) que hereda de `ThreadingHTTPServer`. Fija `allow_reuse_address = True` (permite reusar el puerto tras un cierre reciente, evitando "address already in use") y `daemon_threads = True` (los hilos de petición no bloquean el cierre del proceso).

---

#### `log_message(self, *args, **kwargs)`
- **Qué hace:** Sobrescribe el logging por defecto de `BaseHTTPRequestHandler` para silenciarlo (cuerpo vacío).
- **Parámetros:** Ignora todos los argumentos.
- **Devuelve/Responde:** Nada.
- **Efectos y control de acceso:** Evita que cada petición se imprima en consola/stderr.
- **Notas:** [api_server.py:98](../backend/api_server.py#L98).

#### `end_headers(self)`
- **Qué hace:** Añade las cabeceras CORS antes de cerrar la sección de cabeceras de toda respuesta.
- **Parámetros:** Ninguno.
- **Devuelve/Responde:** Cabeceras `Access-Control-Allow-Origin/-Credentials/-Headers/-Methods`; luego llama a `super().end_headers()`.
- **Efectos y control de acceso:** El origen se refleja solo si está en la lista blanca (`FRONTEND_ORIGIN`, `localhost:5173`, `127.0.0.1:5173`); si no, cae al `FRONTEND_ORIGIN`.
- **Notas:** [api_server.py:101](../backend/api_server.py#L101). Se ejecuta implícitamente en cada `send_response(...)` + `end_headers()`.

#### `do_OPTIONS(self)`
- **Qué hace:** Responde a las peticiones preflight CORS.
- **Parámetros:** Ninguno.
- **Devuelve/Responde:** HTTP 204 sin cuerpo (con las cabeceras CORS de `end_headers`).
- **Efectos y control de acceso:** Público; no valida sesión.
- **Notas:** [api_server.py:114](../backend/api_server.py#L114).

#### `leer_json(self)`
- **Qué hace:** Lee y deserializa el cuerpo JSON de la petición.
- **Parámetros:** Ninguno (lee de `self.rfile` y `Content-Length`).
- **Devuelve/Responde:** `dict` con el JSON parseado, o `{}` si no hay cuerpo.
- **Efectos y control de acceso:** Limita la lectura a 1 000 000 bytes (`min(Content-Length, 1_000_000)`) como protección frente a payloads enormes.
- **Notas:** [api_server.py:118](../backend/api_server.py#L118). Decodifica en UTF-8.

#### `leer_multipart(self)`
- **Qué hace:** Parsea un cuerpo `multipart/form-data` (subida de archivos).
- **Parámetros:** Ninguno (lee `Content-Type` y `Content-Length`).
- **Devuelve/Responde:** Un `cgi.FieldStorage` con los campos y ficheros del formulario.
- **Efectos y control de acceso:** Lee todo el cuerpo en memoria (`io.BytesIO`).
- **Notas:** [api_server.py:124](../backend/api_server.py#L124). Usado por `/api/subir-informe-final`.

#### `responder_json(self, payload, status=200)`
- **Qué hace:** Serializa `payload` a JSON y lo envía como respuesta.
- **Parámetros:** `payload` (objeto serializable), `status` (código HTTP, por defecto 200).
- **Devuelve/Responde:** Cuerpo JSON UTF-8 con `Content-Type: application/json; charset=utf-8` y `Content-Length`.
- **Efectos y control de acceso:** Aplica gzip (`Content-Encoding: gzip` + `Vary: Accept-Encoding`) si el cliente acepta gzip y el cuerpo supera 1024 bytes (nivel de compresión 5). `ensure_ascii=False` para conservar acentos.
- **Notas:** [api_server.py:134](../backend/api_server.py#L134). Es el emisor de respuestas de casi todos los endpoints.

#### `sesion_actual(self)`
- **Qué hace:** Resuelve la sesión del usuario a partir del token.
- **Parámetros:** Ninguno.
- **Devuelve/Responde:** El objeto sesión (`dict`) o `None`/falsy si el token es inválido o falta.
- **Efectos y control de acceso:** Prioriza la cabecera `Authorization: Bearer ...`; si no existe, usa el query param `?token=`. Delega la validación en `obtener_sesion_por_token`.
- **Notas:** [api_server.py:148](../backend/api_server.py#L148). Núcleo de la autenticación de toda la API.

#### `_exigir_acceso_advisee(self, sesion, evaluado)`
- **Qué hace:** Verifica que la sesión (CA) tutela al `evaluado`; si no, deniega.
- **Parámetros:** `sesion` (dict de la sesión), `evaluado` (nombre del evaluado).
- **Devuelve/Responde:** No devuelve nada si el acceso es válido; lanza `PermissionError` (→ 403) en caso contrario.
- **Efectos y control de acceso:** Admin (`is_admin`) pasa siempre. Para el resto, obtiene `obtener_advisees(persona, ca_aliases=[username, email])` y compara nombres normalizados.
- **Notas:** [api_server.py:155](../backend/api_server.py#L155). Usado por eval-anual y por los endpoints de PDF de fuente.

#### `do_GET(self)`
- **Qué hace:** Maneja todas las peticiones GET; enruta por `ruta` exacta o por prefijo.
- **Parámetros:** Ninguno (usa `self.path`).
- **Devuelve/Responde:** Respuestas JSON o archivos según endpoint (ver tabla GET). 404 si no coincide ninguna ruta.
- **Efectos y control de acceso:** Cada rama valida sesión y rol según corresponda. Envuelto en `try/except`: `PermissionError` → 403; otra excepción → 500 (con `logging.exception("Error en API GET")`).
- **Notas:** [api_server.py:166](../backend/api_server.py#L166). Destacan la lógica self-service de `/api/informe-final` ([L295](../backend/api_server.py#L295)), la construcción dinámica de preguntas por área/relación jerárquica en `/api/buscar-empleado-slack` ([L470](../backend/api_server.py#L470)) y el cálculo de completadas del ciclo Slack con fallback de 5 semanas en `/api/estado-ciclo-slack` ([L438](../backend/api_server.py#L438)).

#### `do_POST(self)`
- **Qué hace:** Maneja todas las peticiones POST; distingue cuerpo `multipart/form-data` de JSON.
- **Parámetros:** Ninguno (usa `self.path` y las cabeceras).
- **Devuelve/Responde:** Respuestas JSON según endpoint (ver tabla POST). 404 si no coincide.
- **Efectos y control de acceso:** Los endpoints públicos (`register`, `login`, `password-reset/request`, `password-reset/confirm`) y `notas-ca` se resuelven antes; a partir de [L611](../backend/api_server.py#L611) exige sesión para todo lo demás. Los generadores de informe/PDF validan tutela vía `obtener_advisees` / `_exigir_acceso_advisee` / `validar_acceso_sesion`. `try/except`: `PermissionError` → 403; otra → 500 (`logging.exception("Error en API POST")`).
- **Notas:** [api_server.py:568](../backend/api_server.py#L568). `/api/generar` intenta generar tanto informe HTML como informe anual y solo falla si no produce ninguno. Varios endpoints (`/api/generar-pdf-*`) comparten manejador mediante el diccionario `_GEN`. `guardar/actualizar-evaluacion-slack` calculan la relación jerárquica con `comparar_jerarquia` y mapean el área a su etiqueta Notion.

#### `do_DELETE(self)`
- **Qué hace:** Maneja peticiones DELETE (actualmente solo `/api/objetivos`).
- **Parámetros:** Ninguno (usa `self.path`; lee JSON del cuerpo manualmente).
- **Devuelve/Responde:** `{"ok": ...}` al eliminar; 400 si falta `page_id`; 404 si la ruta no coincide.
- **Efectos y control de acceso:** Exige sesión válida (403 si no). Elimina el objetivo con `eliminar_objetivo_persona(page_id)`. `try/except`: `PermissionError` → 403; otra → 500 (`logging.exception("Error en API DELETE")`).
- **Notas:** [api_server.py:939](../backend/api_server.py#L939).

#### `url_archivo(self, nombre_archivo, evaluado)`
- **Qué hace:** Construye la URL relativa para descargar un archivo protegido asociado a un `evaluado`.
- **Parámetros:** `nombre_archivo` (nombre del fichero generado), `evaluado` (nombre, se pasa como query).
- **Devuelve/Responde:** Cadena `"/api/files/<archivo-url-encoded>?evaluado=<...>"`.
- **Efectos y control de acceso:** Solo formatea; el control real de acceso ocurre al servir en `servir_archivo_protegido`.
- **Notas:** [api_server.py:963](../backend/api_server.py#L963). Usado por todos los endpoints que devuelven URLs de docx/pdf/html.

#### `servir_archivo_protegido(self, nombre_archivo, query)`
- **Qué hace:** Sirve un archivo generado (docx/pdf/html) desde `config.CARPETA_WEB` aplicando control de acceso y caché.
- **Parámetros:** `nombre_archivo` (ruta relativa tras `/api/files/`), `query` (query string con `evaluado`).
- **Devuelve/Responde:** El binario del archivo con su `Content-Type` (docx/pdf/html), `Cache-Control: private, max-age=300` y `ETag`. Devuelve 304 si el `If-None-Match` coincide; 404 si el archivo no existe; 403 en violaciones de acceso.
- **Efectos y control de acceso:** Requiere sesión. Determina el tipo de archivo por su prefijo respecto al `slug` del evaluado (borrador `informe_`/`informe_anual_`, `trayectoria_`, `informe_final_`, `opiniones_ca_`, o PDFs de fuente `evals_proyecto`/`seguimiento_personal`/`evals_mensuales`/`info_completa`). Si el nombre no corresponde a la persona autorizada → 403. Borradores, opiniones y PDFs de fuente son solo para CA/admin. Trayectoria e informe final permiten al propio empleado (`es_propio`) solo si su CA tiene el acceso activo (`ca_tiene_acceso_activo`). Usa `os.path.basename` para evitar path traversal.
- **Notas:** [api_server.py:967](../backend/api_server.py#L967). Lee el archivo completo en memoria antes de enviarlo.

---

### `iniciar_api_backend()`
- **Qué hace:** Punto de arranque del servidor de la API.
- **Parámetros:** Ninguno.
- **Devuelve/Responde:** No retorna (bloquea con `serve_forever()`).
- **Efectos y control de acceso:** Crea `config.CARPETA_WEB` si no existe (`os.makedirs(..., exist_ok=True)`) y levanta `ReusableTCPServer` en `0.0.0.0:config.PUERTO_WEB` con el handler `ApiHandler`. Si el puerto está ocupado (`OSError`), registra un error explicativo sugiriendo cambiar `PUERTO_WEB`.
- **Notas:** [api_server.py:1031](../backend/api_server.py#L1031). Escucha en todas las interfaces (`0.0.0.0`); el puerto por defecto documentado es 8000 (`config.PUERTO_WEB`).
