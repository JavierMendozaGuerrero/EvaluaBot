# Arquitectura de EvaluaBot — Documento técnico en profundidad

> People Analytics interno de Igeneris: recogida de feedback de empleados vía **Slack**,
> almacenamiento en **Notion** (única "base de datos") y generación de informes con **Claude (Anthropic)**,
> más una **web (React + FastAPI)** para CAs y administración.
>
> Documento generado a partir de una lectura completa del código (backend Python + frontend React).
> Fecha: 2026-07-14.

---

## 1. Visión de conjunto

EvaluaBot es **un solo proceso Python** que arranca desde `bot.py → backend/main.py` y levanta,
en threads daemon, todos los subsistemas; más un **frontend React** independiente que se compila con Vite
y habla con el backend por HTTP.

```
                          ┌─────────────────────────────────────────────┐
                          │                bot.py (1 proceso)            │
                          │                                              │
   Slack  ⇄  WebSocket ──►│  Socket Mode (hilo principal, bloqueante)    │
 (Bolt, Socket Mode)      │  slack_bot / ca_reviews / personal_eval      │
                          │                                              │
                          │  8 threads daemon:                           │
                          │   · 3 ciclos de ENVÍO (proyecto/CA/personal) │
                          │   · 3 ciclos de RECORDATORIO (Slack)         │
                          │   · 1 ciclo de recordatorio WEB              │
                          │   · 1 API FastAPI (uvicorn, puerto 8000)     │◄─── HTTP ─── React SPA (Vite :5173)
                          └───────────────┬──────────────────────────────┘
                                          │
                       ┌──────────────────┼───────────────────┐
                       ▼                  ▼                   ▼
                   Notion API       Anthropic API          SMTP
              (almacén de datos)  (informes/resúmenes)  (reset/registro)
```

**Componentes lógicos:**

| Subsistema | Transporte | Módulos principales | Estado |
|---|---|---|---|
| Canal Slack | WebSocket (Socket Mode) | `slack_bot`, `ca_reviews`, `personal_eval` | En memoria (dicts+locks) |
| Programación (scheduling) | Threads `while True` | los mismos + `recordatorios_web` | Calendario en Notion |
| API web | HTTP/JSON (FastAPI+uvicorn) | `backend/api/**` | Sesiones en memoria + disco |
| Datos | Notion REST | `notion_service` (y submódulos) | Notion es la BD |
| IA / informes | Anthropic REST | `reports`, `skill_*`, `eval_anual_sesion` | Caché en disco |
| Frontend | SPA estática | `frontend/src/main.jsx` | `local/sessionStorage` |

**Stack y versiones** (`requirements.txt`, `package.json`):

- **Backend:** Python 3.11.9 · FastAPI 0.139 · uvicorn 0.50 · Slack Bolt 1.28 · notion-client 3.1 ·
  anthropic 0.111 · python-docx 1.2 · reportlab 4.2 · mammoth 1.12 (docx→HTML) · pillow.
- **Frontend:** React 19 · React-DOM 19 · Vite 7 · `@vitejs/plugin-react` 5. **Sin** librerías extra
  (no react-router, no state manager, no cliente HTTP).

---

## 2. Arranque y ciclo de vida (`backend/main.py`)

`main()`:

1. `validar_configuracion()` — exige `NOTION_PARENT_PAGE_ID`; avisa (no aborta) si falta
   `ANTHROPIC_API_KEY` o `python-docx`.
2. `aplicar_estetica_notion()` — decora páginas/DBs de Notion (iconos, colores, descripciones).
3. `inicializar_bbdd_middleoffice()` — crea las tablas de MiddleOffice con datos por defecto si faltan.
4. Lanza **8 threads daemon**:
   - `enviar_evaluaciones_programadas` (proyecto), `ciclo_envio_ca`, `ciclo_envio_personal`
   - `ciclo_recordatorios_proyecto`, `ciclo_recordatorios_ca`, `ciclo_recordatorios_personal`
   - `ciclo_recordatorios_web`
   - `iniciar_api_backend` (uvicorn)
5. `start_socket_mode()` — **bloqueante**, ocupa el hilo principal, escucha eventos de Slack.

`config.APP_MODE` (`"produccion"` vs `"prueba"`, default `prueba`) decide en cada ciclo el comportamiento
de envío (ver §5).

Los clientes globales se inicializan una sola vez en `backend/clients.py`:
`slack_app` (Bolt), `notion` (NotionClient), `anthropic_client` (o `None` si no hay API key),
`Document` (python-docx o `None`).

---

## 3. Capa de datos: Notion como base de datos (`notion_service.py`, ~4.845 líneas)

No hay BD relacional ni ORM. **Todo se persiste en Notion.** `notion_service.py` es la capa de acceso a datos:
crea páginas/databases de forma idempotente, lee/escribe filas, mapea propiedades Notion↔dict y cachea en memoria.

### 3.1 Jerarquía de Notion

Todo cuelga de una **página raíz** (`NOTION_PARENT_PAGE_ID`). Debajo, dos contenedores organizativos
opcionales **`TO-DO`** y **`TO-SEE`**, y bajo ellos las páginas de sección (nombres configurables por env,
con defaults):

```
Página raíz (NOTION_PARENT_PAGE_ID)
├── TO-DO
│   ├── Datos a Monitorizar            → Lista de empleados, Lista CA, Calendario,
│   │   └── Gestión de MiddleOffice       Deadlines evaluaciones, Usuarios Web,
│   │        ├── Cargos de MiddleOffice    Cargos/Relaciones MO
│   │        └── Relaciones de evaluaciones MiddleOffice
│   ├── Datos opcionalmente modificables → Criterios de evaluaciones, Ejemplos de Guía
│   └── Preguntas Chatbot               → Preguntas Negocio / MiddleOffice / Palantir /
│                                          personal / seguimiento CA
└── TO-SEE
    ├── Resultados Evaluaciones
    │   ├── Resultados Evaluaciones Mensuales → "Evaluaciones - {nombre}"  (una DB por empleado)
    │   ├── Resultados Evaluaciones CA        → "Opiniones - {advisee}"    (una DB por advisee)
    │   ├── Resultados Barbecho
    │   ├── Resultados Seguimiento personal   → "Seguimiento - {nombre}"
    │   └── Resultados evaluaciones extra (fuera de proyecto)
    ├── Evaluaciones recibidas y completadas   (tracking de cumplimiento — eval_tracking)
    ├── Activaciones de permisos → Solicitudes Evaluaciones Extra, accesos CA
    └── Informes finales, Evaluaciones anuales, Log evaluación anual asistida
```

**Databases "por persona"** (título con sufijo `{nombre}`): `Evaluaciones - X`, `Opiniones - X`,
`Objetivos - X`, `Seguimiento - X`. La resolución de páginas por nombre desciende hasta 2 niveles y admite
ubicaciones "antiguas" y "nuevas" (retrocompatibilidad).

### 3.2 Esquema de las tablas clave

- **`Evaluaciones - {nombre}`** (evaluación mensual/proyecto recibida por esa persona): `Name`, `Evaluador`,
  `Proyecto`, `Fecha`, `Area` (select: Negocio/MiddleOffice/Palantir) y **6 columnas** Valoración/Justificación
  para superiores / iguales / inferiores. El feedback "de abajo a arriba" (relación *inferior*) se trata como
  **confidencial** (ver §7).
- **`Lista de empleados`**: directorio maestro. Columnas detectadas por listas de candidatos
  (Nombre/Empleado/Persona, Email, Slack ID, Cargo, Área, ID_usuario, Foto, Idioma, País, Baja).
- **`Lista CA`**: fila por CA con columnas `A1, A2, …` = sus advisees.
- **`Usuarios Web`**: `Name`, `Username`, `Persona`, `Email`, `Is admin`, `Salt`, `Password hash`, `Fecha alta`.
- **`Calendario evaluaciones`**: fechas base `personal` y `proyecto_ca`.
- **`Deadlines evaluaciones`**: `{tipo: días}` para mensual/personal/ca/proyecto/extra.
- **`Preguntas *`**: banco de preguntas por área y por relación jerárquica (Top-Bottom / Bottom-Top / Same Level).

### 3.3 Inicialización idempotente y estética

- `_parent_bbdd_en_pagina(nombre, crear=)` es el punto central que localiza (o crea) una página de sección.
- `aplicar_estetica_notion()` decora páginas/DBs (emoji, color, descripción).
- `inicializar_bbdd_middleoffice()` siembra `Cargos de MiddleOffice` y `Relaciones de evaluaciones MiddleOffice`.

**Patrón de creación idempotente** (repetido en casi todos los `_obtener_o_crear_*`):
1) mirar caché en memoria → 2) escanear hijos del parent con `children.list` (consistencia inmediata, evita
duplicados por el lag de `notion.search`) → 3) fallback a `notion.search` global → 4) crear solo si de verdad
no existe. Si la búsqueda falla (p. ej. 429), **no crea a ciegas**: reintenta en la siguiente llamada.
Soporta la API nueva de **data sources** y la clásica de databases (`_usa_data_sources`, `_data_source_id`, `_query_bbdd`).

### 3.4 Funciones públicas por área

| Área | Funciones representativas |
|---|---|
| Empleados / perfiles | `obtener_registros_empleados` (caché 5 min), `obtener_perfil_empleado`, `buscar_empleado_y_cargo`, `obtener_slack_id_por_nombre`, `sugerir_empleados_parecidos`, `obtener_paises_disponibles`, `invalidar_cache_empleados` |
| Evaluaciones mensuales/proyecto | `obtener_o_crear_bbdd_evaluado`, `guardar_en_notion`, `actualizar_en_notion`, `obtener_evaluaciones_por_evaluado`, `obtener_historial_mis_evaluaciones`, `guardar_barbecho_en_notion` |
| Career Advisor / advisees | `obtener_advisees` (lee A1…An de Lista CA), `obtener_ca_de_empleado`, `obtener_opiniones_ca_por_advisee`, `ca_tiene_acceso_activo`, `toggle_acceso_advisees`, `advisee_tiene_acceso_individual`, `toggle_acceso_advisee_individual` |
| Objetivos | `guardar_objetivo_persona`, `obtener_objetivos_persona`, `eliminar_objetivo_persona` |
| Feedback confidencial | `excluir_feedback_confidencial`, `obtener_feedback_confidencial_por_evaluado`, `obtener_todo_el_feedback_confidencial` (anonimizado) |
| Evaluación personal | `guardar_evaluacion_personal`, `obtener_comentarios_personales`, `evaluacion_personal_guardada_desde` |
| Informe final | `guardar_informe_final` (conserva solo los 2 más recientes), `obtener_informe_final_reciente` |
| Calendario / config | `obtener_config_calendario`, `obtener_frecuencias_evaluaciones`, `siguiente_envio_calendario` (cálculo puro) |
| Preguntas | `obtener_preguntas_desde_notion`, `obtener_preguntas_mo`, `obtener_preguntas_palantir`, `obtener_preguntas_personales` (con fallback ES si falta EN) |
| Idioma / país | `idioma_de_persona`, `idioma_por_sesion`, `idioma_por_slack_id`, `guardar_idioma_por_sesion` |
| Criterios | `obtener_criterios_evaluacion(grupo, idioma)`, `obtener_ejemplos_guia` |

### 3.5 Patrones técnicos

- **Caché:** solo **en memoria de proceso**. Dos variantes: (a) IDs de DB en dicts mutables `{"db_id": None}`
  que no caducan; (b) datos con **TTL de 300 s** (empleados, advisees, preguntas, frecuencias, criterios),
  cada uno con su `threading.Lock`. En error de red devuelve la caché previa (degradación elegante).
- **Paginación:** bucle `while True` con `start_cursor`/`has_more`/`next_cursor`, `page_size=100`.
- **Normalización de nombres:** `utils.normalizar_nombre` (minúsculas + colapsar espacios). Matching difuso propio
  para búsqueda de empleados (LCS, orden de letras, `SequenceMatcher` para proyectos).
- **Mapeo de propiedades:** helpers que abstraen el tipo de columna (title/rich_text/select/email/files…) y
  toleran esquemas variables (prueban listas de nombres candidatos).
- **Robustez:** casi toda función pública envuelve Notion en `try/except` + `logging.exception` y devuelve
  valor neutro (`[]`, `{}`, `None`, `False`) — **la app nunca se cae por un fallo de Notion**. Sin backoff
  explícito; frente a 429 la estrategia es "no duplicar" y reintentar luego.
- **Migraciones perezosas:** `_asegurar_propiedades_*` añade columnas que falten; sets a nivel de módulo
  garantizan sembrar defaults una sola vez por proceso, respetando ediciones/borrados manuales del admin.

---

## 4. Canal de Slack (Bolt, Socket Mode)

### 4.1 Cómo funciona

- `slack_app` (Bolt) se comparte entre módulos. **Socket Mode**: `SocketModeHandler(slack_app, SLACK_APP_TOKEN).start()`.
  No hay servidor HTTP para eventos: todo llega por WebSocket.
- Los handlers se registran **por decorador al importar el módulo** (por eso `main.py` importa los tres módulos).
- Hay **un único** `@slack_app.event("message")` → `handle_message_events` (en `slack_bot.py`), que enruta TODO el
  texto entrante. Además `message_changed` (transcripciones de audio) y decenas de `@slack_app.action(...)`
  (botones/selects/modales, con `action_id` literal o `re.compile`).
- **Enrutado por hilo:** el router compara `thread_ts` del mensaje con el `ts` raíz guardado de cada tipo:
  `ca_dm_ts` → CA, `personal_dm_ts` → personal, `evaluacion_dm_ts` → proyecto. Ignora todo lo que no sea DM
  (`channel` que empiece por `D`) y lo que no esté en hilo.
- **Botones → texto:** los handlers de acción fabrican un evento sintético `{text: "sí"/"modificar"/número}` y
  reinvocan el mismo `manejar_mensaje_*`, de modo que botones y texto libre convergen en una sola máquina de estados.

### 4.2 Estado en memoria (`state.py`)

Todo el estado conversacional vive en dicts por `user_id`, protegidos por `threading.RLock`:
`conversaciones`, `evaluaciones_dm_activas/_expiradas`, `evaluacion_dm_canal/_ts/_ts_anterior`,
`evaluacion_hora`, `evaluacion_ultimo_recordatorio`, más los equivalentes de CA y personal en sus módulos.
**Se pierde al reiniciar** (salvo las sesiones web, que sí se persisten — §6).

---

## 5. Scheduling (ciclos programados)

Cada ciclo es un `while True` en thread daemon.

- **Modo prueba** (`APP_MODE != produccion`): envía **inmediatamente** y luego cada `INTERVALO_PRUEBA_DIAS` (30);
  destinatario único `SLACK_TEST_USER_ID`.
- **Modo producción**: lee `obtener_config_calendario()` (Notion) → fecha base; `siguiente_envio_calendario(fecha, semanas)`
  calcula el próximo instante posterior a "ahora" y el ciclo `time.sleep` hasta él. Si falta la fecha, reintenta cada hora.

| Ciclo | Fecha base | Intervalo | Notas |
|---|---|---|---|
| `enviar_evaluaciones_programadas` (proyecto/mensual) | `proyecto_ca` | **4 semanas** | `enviar_una_evaluacion` abre DM a todos, publica el mensaje raíz, guarda estado, registra envío |
| `ciclo_envio_ca` | `proyecto_ca` | **4 semanas** | solo a quien **tiene advisees** |
| `ciclo_envio_personal` | `personal` | **2 semanas** | reflexión personal del propio empleado |
| `ciclo_recordatorios_proyecto` | — | cada 30 s, umbral **7 días** | autocierra si ya respondió (`evaluacion_proyecto_guardada_desde`) |
| `ciclo_recordatorios_ca` | — | cada 30 s, umbral **7 días** | autocierra con `_ca_guardo_desde` |
| `ciclo_recordatorios_personal` | — | cada 30 s, umbral **7 días** | el recordatorio va **dentro del hilo** |
| `ciclo_recordatorios_web` | tracking en Notion | cada hora, umbral **14 días** | evals de proyecto/extra lanzadas desde la web; sobrevive a reinicios |

> Nota: `config.DIA_ENVIO_PRODUCCION=4` (viernes) y `HORA_ENVIO_PRODUCCION=10:00` (`Europe/Madrid`) y la función
> `siguiente_envio_produccion` existen pero el envío real en producción se rige por el **calendario de Notion + intervalo**;
> la constante viernes-10:00 es esencialmente legado.

---

## 6. Flujos conversacionales (Slack)

### 6.1 Evaluación mensual de proyecto (`slack_bot.py`)

Máquina de estados sobre `state.conversaciones[user_id]["modo"]`, preguntas **una a una** en el hilo:

```
pre_inicial → esperando_area (negocio/middleoffice/palantir)
            → esperando_situacion (en proyecto / en barbecho)
                 · barbecho → esperando_labores_barbecho → confirmacion_barbecho → guarda y termina
            → esperando_proyecto (texto) → esperando_persona (empleado + cargo + jerarquía)
            → preguntando_area_secuencial (q1..qN; valoraciones = botones 1-5)
            → confirmacion (Guardar / Modificar) → guardar (guardar_en_notion)
            → ¿más personas? / ¿más proyectos? → terminado (ventana de 2 días para editar)
```

- La **relación jerárquica** evaluador↔evaluado (`hierarchy.comparar_jerarquia` → `tipo_relacion`:
  Top-Bottom / Bottom-Top / Same Level) determina qué preguntas se cargan y en qué columnas se guarda.
- Trabajo pesado de Notion **fuera del lock**, con `AnimacionCargando` (barra animada en el hilo, `slack_carga.py`).
- **Botón "Atrás"** (`conversation_back.py`): `push_historial` apila el estado antes de cada avance; `pop_historial`
  lo restaura; `limpiar_historial` tras guardar (punto sin retorno).
- **Audio:** `_gestionar_audio` detecta `audio/*`. La transcripción la hace **Slack** (no el bot): si está lista se
  inyecta como texto; si no, se guarda pendiente y, cuando llega `message_changed`, se reenvía el evento ya con texto.
  Timeout de aviso a los 3 min.

### 6.2 Career Advisor (`ca_reviews.py`)

DM solo a quien es CA de alguien. El CA elige un advisee y el bot le muestra, en mensajes **colapsables por tipo**:
evaluaciones de proyecto, mensuales, seguimiento personal y objetivos del advisee (desde la última opinión o 4 semanas
atrás). El anonimato de los evaluadores lo decide el módulo `anonimato`. Opcionalmente pide a **Claude** un resumen
(`generar_resumen_evaluacion`) antes de que el CA escriba su **opinión consolidada**, que se guarda en
`Opiniones - {advisee}` (`Name/Fecha/CA/Opinion/Resumen`).

### 6.3 Reflexión personal (`personal_eval.py`)

El propio empleado reflexiona (no evalúa a terceros). Elige un **tópico** (CTTF, Objetivos, Dificultades,
Trayectoria, Otro), escribe texto libre (o audio), confirma y se guarda con `guardar_evaluacion_personal`.
Modales interactivos leídos de Notion (objetivos, criterios, ejemplos) con patrón anti-`trigger_id`
(abrir "vista de carga" con `views_open` y luego `views_update`).

---

## 7. API web (FastAPI, `backend/api/`)

`api/app.py` monta `FastAPI()`, aplica middlewares e incluye ~10 routers. `api_server.py` es solo un shim de
compatibilidad (`from .api.app import app, iniciar_api_backend`). uvicorn escucha en `0.0.0.0:PUERTO_WEB` (8000).

### 7.1 Middlewares (orden de `app.py`)

`CORS` (orígenes de `config.CORS_ORIGINS`, credenciales, métodos GET/POST/DELETE/OPTIONS) →
`GZip` → `BodySizeLimit` (1 MB JSON / 15 MB para subir informe) → `GenerationRateLimit`
(10/min por token o IP en `/api/generar*`) → `AuthRateLimit` (8/min por IP en login/register/reset) →
`SecurityHeaders` (`nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`).

`errors.py` traduce excepciones a JSON uniforme: `PermissionError`→403, `ValueError`→400,
`RequestValidationError`→400, 404→`{"error":"No encontrado"}`, resto→500 genérico (detalle solo en logs).

### 7.2 Autenticación y sesiones (`users.py`, `api/deps.py`)

- **Contraseñas:** PBKDF2-HMAC-SHA256, 120.000 iteraciones, `salt` de 16 bytes; comparación con `hmac.compare_digest`.
  Política: ≥8 caracteres, 1 mayúscula, 1 especial.
- **Usuarios en Notion** (`Usuarios Web`), con **fallback a `users.json`** local si Notion falla. La identidad
  (`persona`) se **liga al empleado verificado por email**, no al username elegido (nadie puede reclamar los datos de otro).
- **Registro** (desactivado por defecto, `REGISTRO_WEB_HABILITADO`): 2 pasos con **código de 6 dígitos** al email del
  empleado (que debe estar en la Lista de empleados). Alta habitual: `create_users_from_employees.py`.
- **Sesiones:** token `secrets.token_urlsafe(32)`; en el servidor solo se guarda el **hash SHA-256** del token.
  TTL 12 h (30 días con "Recuérdame"). Persistidas en `sesiones.json` (solo el hash) para sobrevivir a reinicios.
  `Authorization: Bearer <token>` (nunca en la URL).
- **Reset de contraseña:** token de un solo uso (30 min) enviado por SMTP; al cambiar la contraseña se invalidan
  todas las sesiones del usuario.
- **Dependencias FastAPI:** `require_session` (403 si no hay sesión), `require_admin(mensaje)` (factory con mensaje
  propio), `exigir_acceso_advisee(session, evaluado)` (admin pasa; si no, el evaluado debe estar entre los advisees del CA).

### 7.3 Routers y responsabilidad

| Router | Prefijos representativos | Rol |
|---|---|---|
| `auth` | `/api/login`, `/logout`, `/register(/verify)`, `/password-reset/*`, `/me`, `/set-idioma`, `/set-pais` | Sesión, alta, idioma/país |
| `perfiles` | `/api/evaluados`, `/mis-advisees`, `/mi-perfil`, `/paises`, `/perfil-empleado`, `/todos-empleados` | Directorio y perfiles |
| `ca` | `/api/opiniones-ca`, `/objetivos`, `/acceso-advisees(-individual)`, `/notas-ca`, `/resumen-evaluaciones-advisee`, `/historial-evaluaciones`, `/feedback-confidencial(-todos)`, `/criterios-evaluacion`, `/cumplimiento-evaluaciones(-detalle)` | Todo lo del CA + cumplimiento admin |
| `project_evals` | `/api/evaluaciones-proyecto-*`, `/preguntas-evaluacion-proyecto`, `/equipo-proyecto`, `/activar-evaluaciones-proyecto`, `/modificar-equipo-proyecto`, `/recordatorio-proyecto`, `/guardar-evaluacion-proyecto`, `/borrador-*` | Evaluaciones estructuradas de equipo |
| `evaluaciones_extra` | `/api/evaluaciones-extra-*`, `/solicitar-evaluacion-extra`, `/guardar-evaluacion-extra` | Evaluaciones fuera de proyecto |
| `personal_slack` | `/api/tareas-slack`, `/estado-ciclo-slack`, `/buscar-empleado-slack`, `/guardar-evaluacion-slack`, `/actualizar-evaluacion-slack`, `/guardar-evaluacion-personal`, `/urgencia-personal` | Réplica web de los chats de Slack |
| `eval_anual` | `/api/evaluados-anual`, `/generar-anual`, `/eval-anual/*` | Evaluación anual asistida por IA |
| `reports` | `/api/informe-final`, `/generar`, `/generar-opiniones-ca`, `/generar-pdf-*`, `/trayectoria`, `/subir-informe-final` | Generación y descarga de documentos |
| `admin` | `/api/anonimato-evaluadores` | Config de anonimato (borra cachés de PDFs afectados) |
| `files` | `/api/files/{archivo:path}` | Sirve documentos protegidos (registrado el último, catch-all) |

### 7.4 Modelo de permisos y decisiones de servidor

- **Empleado:** solo ve lo suyo. **Career Advisor:** ve a sus advisees (`obtener_advisees`), añade objetivos/notas.
  **Admin:** acceso total.
- **El servidor decide, no el cliente:** en `/api/guardar-evaluacion-proyecto`, el **tipo** de evaluación se recalcula
  por jerarquía de empresa (`tipo_evaluacion_por_jerarquia`), no se confía en el que manda el cliente. Solo las
  top-to-bottom estrictas se liberan al evaluado (`Visible_evaluado`); las bottom-to-top y de mismo nivel quedan
  visibles solo para el CA.
- **`/api/files/{...}`** valida que el archivo pertenezca a la persona autorizada (prefijos por slug), que el rol
  tenga acceso, y limita a `.html/.pdf/.docx`. El **informe final** (HTML convertido de un .docx subido por el CA)
  se sirve con **CSP `default-src 'none'`** para neutralizar cualquier script incrustado. Descargas por `fetch`+blob
  (el token nunca viaja en la URL).
- **Anonimato** (`anonimato.py` + `dashboard_web/anonimato.json`): `global_anonimo` + lista `advisees_revelados`.
  `evaluadores_visibles_para_advisee()` decide si se muestran los nombres de los evaluadores. Cambiarlo purga las
  cachés de PDFs afectados.

### 7.5 Tracking de cumplimiento (`eval_tracking.py`)

Tabla plana **"Evaluaciones recibidas y completadas"**: una fila = "a la persona P se le asignó una evaluación de
tipo T en el ciclo C". `registrar_envio` (idempotente por persona/tipo/ciclo), `marcar_completada` (auto-cura si no
había envío), `resumen_ciclo_actual` / `detalle_por_persona` (panel admin), `pendientes_slack_de_persona` (deadlines
en el dashboard). El ciclo de 4 semanas se ancla en `proyecto_ca` del calendario. Recordatorios web duraderos
(proyecto/extra) usan `Fecha_recordatorio` para no repetir antes de 14 días.

---

## 8. Generación de informes con IA (`reports.py`, `skill_*`, `eval_anual_sesion.py`)

Todos los artefactos se escriben en `backend/dashboard_web/` (`config.CARPETA_WEB`) y se sirven vía `/api/files/`.
El nombre base sale de `slug_archivo(nombre)`.

### 8.1 Catálogo de documentos

| Documento | Entrada | ¿Claude? | Salida | Caché |
|---|---|---|---|---|
| Informe de evaluaciones | `generar_archivos_informe` | **Sí** (sonnet-4-6, 2200 tok) | `informe_{slug}.html` + `.docx` | Sí (`_cache.json`, huella SHA-256) |
| Trayectoria navegable | `generar_archivo_trayectoria` | No (formatea) | `trayectoria_{slug}.html` (render JS cliente) | No |
| Informe anual IGENERIS | `generar_informe_anual` | **Sí** (hasta 3 llamadas) | `informe_anual_{slug}.docx` + `.html` | Sí (huella `v:5`) |
| Resumen opiniones CA | `generar_resumen_opiniones_ca` | No | `opiniones_ca_{slug}.pdf` + `.html` | Sí (`v:1`) |
| PDFs de fuentes | `generar_pdf_evals_proyecto/_mensuales/_extra/seguimiento/completo` | No | `{prefijo}_{slug}.pdf` | No |
| Resumen por competencias | `generar_resumen_evaluacion` | **Sí** (sonnet-4-6, 1200 tok) | texto (se guarda en Notion) | En conversación |

### 8.2 Cómo se llama a Claude

- SDK `anthropic_client.messages.create(...)`; el texto se recompone de los bloques `type=="text"`.
- Modelos: **`claude-sonnet-4-6`** para informes/resúmenes/interpretación; **`claude-haiku-4-5`** solo para el chat de
  dudas del plan de acción.
- **Defensa anti-inyección** (`config.INSTRUCCION_ANTIINYECCION`) concatenada al `system` en **todas** las llamadas que
  procesan texto libre de usuarios.
- **Prompt caching:** el `system` estático (por cargo/idioma) se envía con `cache_control: ephemeral` (con reintento
  sin caché si falla).

Parámetros (informe anual, el más elaborado): `temperature=0`, `max_tokens` 1500–4000 según fase.

### 8.3 Anti-alucinación (exclusivo del informe anual)

1. **Mapa de fuentes citables** con IDs por tipo (`O#` opinión CA, `E#` mensual, `P#` proyecto, `S#` seguimiento,
   `B#` barbecho, `X#` extra). El `system` exige que **toda afirmación termine con una cita** `[E#]` o escriba
   "Sin información suficiente"; salida en **JSON estricto** por dimensión.
2. `_validar_citas` / `_filtrar_bullets_citados` eliminan bullets sin cita válida.
3. `_verificar_soporte` (segunda llamada, "auditor") marca afirmaciones no respaldadas por el texto de su cita →
   alimenta el panel de revisión ("avisar, no borrar").

### 8.4 Sesión anual asistida (`eval_anual_sesion.py`)

Asistente conversacional donde el CA recorre el informe **área por área** con Claude como "sparring crítico".
**Estado en disco** (`sesion_anual_{slug}.json` en `CARPETA_WEB`; cada endpoint relee el fichero). Fases:
`iniciar_sesion` → `confirmar_identidad` → por área: `obtener_area` (genera comentarios lazy) / `responder_area`
(sparring) / `generar_resumen_area` (criterio a criterio) / `confirmar_area` → plan de acción
(`obtener_plan_accion`, `pedir_cambios_plan`, `chatear_plan` con Haiku) → `finalizar_sesion` (exige todas las áreas
confirmadas; genera `.docx` + `.html` reusando la maquetación de `skill_informes_anual`; escribe log en Notion) →
borrador web editable (`obtener_borrador`, `guardar_borrador`, `generar_docx_borrador`).

### 8.5 Motores de salida

- **DOCX:** python-docx. El informe anual manipula **XML OOXML crudo** (`OxmlElement`/`qn`) para bordes, anchos,
  hyperlinks internos + bookmarks del anexo de fuentes, y bullets por nivel.
- **PDF:** reportlab (`platypus` + `SimpleDocTemplate`), fuentes Outfit con fallback a Helvetica, canvas custom
  `IGCanvas` para cabecera/pie.
- **HTML:** f-strings con `config.IGENERIS_CSS`, escapado con `html.escape`.
- **docx→HTML:** `mammoth` (solo para el informe final subido por el CA; se envuelve con `<meta charset>` y estilo,
  y se sirve con CSP restrictiva).
- **Caché en disco:** `{prefijo}_{slug}_cache.json` con `{huella}` = SHA-256 de los datos de Notion normalizados
  (versionada, incluye idioma/cargo/criterios). Se reutiliza solo si la huella coincide **y** existen los ficheros
  de salida. Sin TTL por tiempo. Trayectoria y PDFs de fuentes no cachean.

---

## 9. Frontend (React 19 + Vite, `frontend/src/main.jsx`)

### 9.1 Arquitectura del SPA

- **Un solo archivo** `main.jsx` (~6.800 líneas): infraestructura → helpers UI → ~45 componentes → `App` → `createRoot`.
  Monta 3 elementos hermanos: `<TopLoadingBar>`, `<LangToggle>`, `<App>`.
- **Sin router library.** El "router" es un `switch` sobre `page.type` en `App`; `navigate()` usa
  `history.pushState` + listener `popstate` (botón atrás del navegador). El **hash** solo para documentos legales
  (`#privacidad`/`#terminos`) y reset de contraseña.
- **Estado global** en `useState` dentro de `App` (`token`, `user`, `page`, `adminMode`, …). Único Context:
  `GoHomeContext`. Propagación a páginas por **props explícitas**.
- **API:** `API_BASE` = `VITE_API_BASE_URL` o `${protocol}//${hostname}:8000` (habla directo con el backend, sin proxy
  Vite → CORS lo gestiona el backend). `apiRequest()` añade `Authorization: Bearer`, lanza `Error` en `!ok`
  (cada página hace su `try/catch`). `apiRequestCached()` = stale-while-revalidate en `sessionStorage` (TTL 5 min,
  prefijo `ebc_`). `openAuthedFile()` abre PDFs/HTML con `fetch`+blob para no exponer el token en la URL.
- **Sesión:** token en `localStorage` (si "Recuérdame") o `sessionStorage`. Al montar y en cada cambio de token,
  `App` valida contra `/api/me`; si falla, logout implícito (no hay interceptor 401).
- **Gate de render:** legal → auth (sin sesión/reset) → `AdminRoleSelect` (admin sin modo) → `AdminPanel` → switch
  de páginas (default `Dashboard`).

### 9.2 Páginas por rol

- **Auth (sin sesión):** `AuthScreen` (login/register/verify/forgot/reset), `LegalPage`.
- **Empleado:** `Dashboard` (hub central: tareas, perfil/país, objetivos, evaluaciones recibidas, informes),
  `MisObjetivosPage`, `EvaluacionesProyectoPage`, `FormularioEvaluacionProyecto` (borrador en `localStorage`),
  `HistorialEvaluacionesPage`, `SolicitarEvaluacionExtraPage`, `FormularioEvaluacionExtra`.
- **Responsable de proyecto:** `ActivarEvaluacionesProyectoPage`, `MisProyectosActivosPage`.
- **Career Advisor:** `AdviseesList`, `AdviseeDetail` (ficha completa), `ObjetivosPage`, `RegistroComentariosPage`
  (dictado por voz), `PlanAccionPage`, `SubirInformePage`, `EvaluacionAnualWizard`.
- **Chats estilo Slack (web):** `EvaluacionesSlackPage` + `ChatEvalProyecto` / `ChatEvalPersonal` / `ChatEvalCA`.
- **Admin:** `AdminRoleSelect`, `AdminPanel` (cumplimiento, feedback confidencial anónimo, generación de PDFs).

### 9.3 i18n (ES/EN/PT)

Motor propio en `i18n.js`. `STRINGS` con ES/EN inline; **PT diferido** (`pt.js`, generado por
`backend/generar_i18n_pt.py`, cargado con `import()` la primera vez que se usa). `t(clave, vars)` con placeholders
`{nombre}` y fallback a ES / a la propia clave. La elección **manual** del usuario (`localStorage`) tiene prioridad
sobre el idioma de Notion (`/api/me`). Pub/sub para re-render global al cambiar idioma.

### 9.4 Build / estilos

- Vite: `manualChunks` separa React en su propio chunk (cache larga). `dev` en `:5173` con `--host 0.0.0.0`.
- **Sistema de diseño Igeneris** en cascada: `globals.css` (tokens: blanco, negro, un único acento `#F23C14`,
  fuente Outfit) → `components.css` (componentes `ig-*` compartidos) → `styles.css` (layout específico + alias
  legacy `--ink/--muted/--line`). Estilos estructurales por clase; puntuales/dinámicos inline con `var(--token)`.

---

## 10. Flujo de datos end-to-end (ejemplo)

**Evaluación de proyecto por Slack → informe anual del CA:**

1. Viernes según calendario, `enviar_una_evaluacion` abre DM y `registrar_envio` anota la asignación en el tracking.
2. El empleado responde en el hilo → máquina de estados → `guardar_en_notion` escribe una fila en
   `Evaluaciones - {evaluado}` con la relación jerárquica; `marcar_completada` cierra el tracking.
3. Cada 4 semanas el CA recibe su DM, revisa las evaluaciones de sus advisees y guarda su opinión en
   `Opiniones - {advisee}` (opcionalmente resumida por Claude).
4. En la web, el CA abre `AdviseeDetail` → `EvaluacionAnualWizard`: la sesión asistida (`eval_anual_sesion`) reúne
   las 7 fuentes de Notion, Claude interpreta con citas verificadas, el CA acuerda área por área y **finaliza** →
   `informe_anual_{slug}.docx/.html`.
5. El CA sube/publica el informe final (`guardar_informe_final`, conserva 2 versiones) y concede acceso al empleado
   (`toggle_acceso_advisee_individual`), que lo ve desde su `Dashboard`.

---

## 11. Seguridad — resumen

- Contraseñas PBKDF2 (120k iter) + salt; identidad ligada a empleado verificado por email.
- Sesiones: solo hash del token en servidor; TTL; invalidación al cambiar contraseña; token nunca en URL.
- Rate limiting (auth 8/min·IP, generación 10/min·cliente), límite de body, cabeceras de seguridad.
- Autorización por rol reforzada en servidor (tipos y visibilidad recalculados, no confiados al cliente).
- `/api/files` acotado por slug/rol/extensión; informe final con CSP `default-src 'none'`.
- **Defensa anti-inyección de prompts** en todas las llamadas a Claude que reciben texto de usuario.
- Anonimato configurable del feedback bottom-to-top (confidencial, sin autor).

---

## 12. Mapa de archivos (referencia rápida)

| Archivo | Líneas aprox. | Rol |
|---|---|---|
| `bot.py` / `backend/main.py` | 3 / 57 | Entry point + arranque de threads |
| `backend/config.py` | 129 | Env vars, constantes, CSS Igeneris, instrucción anti-inyección |
| `backend/clients.py` | 20 | Clientes globales Slack/Notion/Claude |
| `backend/state.py` | 21 | Estado en memoria thread-safe |
| `backend/notion_service.py` | 4.845 | **Capa de datos** (toda la API de Notion) |
| `backend/slack_bot.py` | 2.693 | Evaluación de proyecto por Slack + router de mensajes |
| `backend/ca_reviews.py` | 1.784 | Flujo Career Advisor |
| `backend/personal_eval.py` | 1.337 | Reflexión personal |
| `backend/project_evals.py` | 1.842 | Evaluaciones estructuradas de equipo (web) |
| `backend/eval_anual_sesion.py` | 928 | Sesión anual asistida por IA |
| `backend/skill_informes_anual.py` | 1.678 | Informe anual IGENERIS (Claude + DOCX/HTML) |
| `backend/skill_opiniones_ca.py` / `skill_pdfs_fuentes.py` / `skill_resumen_evaluacion.py` | 523 / 379 / 321 | Resúmenes y PDFs |
| `backend/reports.py` | 317 | Informe de evaluaciones + trayectoria + caché |
| `backend/users.py` | 653 | Auth, sesiones, registro, reset |
| `backend/eval_tracking.py` | 415 | Cumplimiento de evaluaciones |
| `backend/evaluaciones_extra.py` | 334 | Evaluaciones fuera de proyecto |
| `backend/recordatorios_web.py` | 62 | Recordatorios Slack de evals web |
| `backend/hierarchy.py` / `anonimato.py` / `utils.py` | 54 / 43 / 47 | Jerarquía de cargos, anonimato, utilidades |
| `backend/i18n.py` / `i18n_pt.py` | 507 / 290 | Traducciones backend |
| `backend/api/app.py` + `deps.py` + `errors.py` + `hardening.py` + `files.py` | — | Infraestructura FastAPI |
| `backend/api/routers/*.py` | — | ~10 routers por área |
| `frontend/src/main.jsx` | ~6.800 | SPA React completa |
| `frontend/src/i18n.js` + `pt.js` | 671 + 488 | i18n frontend |
| `frontend/src/styles/*.css` + `styles.css` | — | Sistema de diseño Igeneris |

---

*Fin del documento.*
