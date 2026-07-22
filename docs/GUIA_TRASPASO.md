# Guía técnica de traspaso — EvaluaBot

> Redactada el 2026-07-21 a partir de una lectura completa del código (backend Python, frontend React,
> configuración y despliegue). Todo lo afirmado cita ficheros reales del repo. Lo no verificable en el
> código está marcado como **⚠️ Pendiente de confirmar**.

---

## 1. El producto de un vistazo

**EvaluaBot** es la herramienta de People interna de Igeneris: recoge el feedback de los
empleados (evaluaciones entre compañeros, seguimiento personal y opiniones de los Career Advisors)
a través de un **bot de Slack** y una **web**, lo guarda todo en **Notion**, y ayuda a los Career
Advisors a convertir ese feedback en **informes de evaluación anual** redactados con ayuda de
**Claude (IA de Anthropic)**. Quien lo usa no tiene que perseguir formularios: el bot le avisa cuando
toca evaluar, y su responsable obtiene al final un informe anual documentado y con fuentes citadas.

---

## 2. Qué ve y hace el usuario final

Hay **dos superficies**: el bot de Slack (DMs automáticos) y la web (SPA React). Y **tres perfiles**:
empleado, Career Advisor (CA) y administrador.

### 2.1 El bot de Slack (todos los empleados)

Cada empleado tiene un DM con el bot. Toda la interacción es **en hilo** sobre el mensaje que envía el
bot, con botones y texto libre (admite **audio**: usa la transcripción de Slack). Tres ciclos:

| Ciclo | Quién lo recibe | Cadencia (producción) | Qué se hace |
|---|---|---|---|
| **Seguimiento personal** | Todos | Cada 2 semanas | Reflexión libre por tópicos (CTTF, objetivos, dificultades, trayectoria…). |
| **Evaluación mensual de proyecto** | Todos | Cada 4 semanas | Evalúa a los compañeros de cada proyecto: área (Negocio/MiddleOffice/Palantir) → proyecto → persona → preguntas una a una (valoración 1-5 con botones + justificación) → resumen → confirmar/modificar → se guarda en Notion. Si está "en barbecho", describe sus labores en su lugar. Hay botón "Estoy solo en el proyecto" para autoevaluarse. |
| **Revisión de Career Advisor** | Solo quien tiene advisees | Cada 4 semanas (7 días después de la mensual, `CA_OFFSET_DIAS`) | El CA elige un advisee, ve un resumen plegado de sus evaluaciones/seguimientos/objetivos (botón "Ver evaluaciones" que abre una ventana modal), opcionalmente pide un **resumen IA** (con botón de consentimiento) y escribe su **opinión consolidada**. |

Mientras una evaluación está pendiente, el bot **recuerda cada semana**; al llegar el siguiente ciclo,
la anterior caduca (el DM se edita a "caducada"). Botón **⬅️ Atrás** en todos los flujos, selector de
idioma (ES/EN/PT) en el propio DM, y escribir `sos` en el hilo cancela la evaluación en curso
(slack_bot.py:1578-1587).

### 2.2 La web (SPA servida por el propio backend; URL según despliegue — ver sección 8)

**Acceso**: login con usuario/contraseña (pantalla con registro por código de email — desactivado por
defecto —, "recuérdame" y recuperación de contraseña). Selector de idioma ES/EN/PT arriba a la derecha.

**Empleado** — pantalla principal `Dashboard` con dos columnas:
- *Mi perfil*: foto, cargo, país (editable), **mis objetivos** y **tareas pendientes** (badge con lo
  que le queda por responder: evaluaciones Slack, de proyecto, extra).
- *To-do*: hacer **evaluaciones de proyecto** (formulario web con preguntas dinámicas y borrador
  autosalvado) y **evaluaciones extra** (pedir/rellenar evaluaciones fuera de proyecto). Las
  evaluaciones de Slack (mensual/personal) **no se responden desde la web**: la tarea pendiente del
  Dashboard abre directamente el DM de Slack con un deeplink (main.jsx:3039,
  `backend/api/routers/personal_slack.py:55`).
- *To-see*: **su informe final** (web + descarga Word, solo si su CA le ha dado acceso), evaluaciones
  de proyecto recibidas (solo las liberadas: las top-to-bottom), histórico de proyectos terminados.

**Responsable de proyecto** — activa las evaluaciones de un proyecto (elige equipo y manager),
gestiona el equipo de sus proyectos activos y puede mandar recordatorios.

**Career Advisor** — rejilla de **advisees** y ficha `AdviseeDetail` por cada uno: editar objetivos,
registrar notas de reuniones (con **dictado por voz**), plan de acción (editable, con chat de dudas con
IA), descargar PDFs con toda la información disponible del advisee, y el flujo estrella: el
**asistente de evaluación anual** (`EvaluacionAnualWizard`) — recorre el informe área por área con
Claude como contraste crítico (o en modo manual), confirma cada área, genera el plan de acción, edita
el borrador y publica el informe final, decidiendo si el advisee puede verlo.

**Administrador** — al entrar elige "Admin" o "Perfil personal". El panel de admin muestra:
cumplimiento de evaluaciones (global y por persona), feedback confidencial (anónimo), configuración de
**anonimato de evaluadores** (global y por advisee), informes finales de cualquiera y descarga de PDFs
de fuentes.

---

## 3. Mapa: experiencia → código

El frontend es **un solo fichero**: [frontend/src/main.jsx](../frontend/src/main.jsx) (~6.200 líneas).
Las "pantallas" son componentes elegidos por una cadena de `if/else if` sobre `page.type` dentro de
`App()` (main.jsx ~6919-6973). Líneas verificadas a 2026-07-21 (pueden desplazarse al editar):

| Lo que ve el usuario | Componente (frontend/src/main.jsx) | Backend que lo sirve |
|---|---|---|
| Login/registro/reset | `AuthScreen` (~1405) | `backend/api/routers/auth.py` + `backend/users.py` |
| Dashboard del empleado | `Dashboard` (~2600) | `routers/perfiles.py`, `personal_slack.py`, `project_evals.py` |
| Evaluaciones de proyecto (lista + formulario) | `EvaluacionesProyectoPage` (~4905), `FormularioEvaluacionProyecto` (~5089) | `routers/project_evals.py` → `backend/project_evals.py` |
| Historial y detalle de evaluaciones hechas | `HistorialEvaluacionesPage` (~2284), `DetalleEvaluacionRealizadaPage` (~2368) | ídem |
| Activar/gestionar proyecto (manager) | `ActivarEvaluacionesProyectoPage` (~4660), `MisProyectosActivosPage` (~4388) | ídem |
| Evaluaciones extra | `SolicitarEvaluacionExtraPage` (~5517), `FormularioEvaluacionExtra` (~5669) | `routers/evaluaciones_extra.py` → `backend/evaluaciones_extra.py` |
| Tareas pendientes de Slack (deeplink al DM) | fila de tareas del `Dashboard` | `routers/personal_slack.py` (`/api/tareas-slack`, único endpoint del router) |
| Lista y ficha de advisees (CA) | `AdviseesList` (~3561), `AdviseeDetail` (~3796) | `routers/ca.py`, `perfiles.py` |
| Objetivos | `MisObjetivosPage` (~1055), `ObjetivosPage` (~1121) | `routers/ca.py` (`/api/objetivos`) |
| Notas/comentarios del CA (voz) | `RegistroComentariosPage` (~3613) | `routers/ca.py` (`/api/notas-ca`, `/api/opiniones-ca`) |
| Plan de acción | `PlanAccionPage` (~4185) | `routers/eval_anual.py` (`/api/eval-anual/plan*`) |
| Asistente de evaluación anual | `EvaluacionAnualWizard` (~5950) | `routers/eval_anual.py` → `backend/eval_anual_sesion.py` + `backend/skill_informes_anual.py` |
| Subir informe final | `SubirInformePage` (~3436) | `routers/reports.py` (`/api/subir-informe-final`) |
| Panel de administración | `AdminPanel` (~589), `AdminRoleSelect` (~557) | `routers/ca.py` (cumplimiento, confidencial), `admin.py` (anonimato), `reports.py` (PDFs) |
| Textos legales | `LegalPage` (~527) + `frontend/src/legal/*.md` | — (estático) |
| Documentos protegidos (PDF/HTML/Word) | `openAuthedFile` (~229) | `backend/api/files.py` (`/api/files/{...}`) |

Los tres flujos de Slack: mensual/proyecto en [backend/slack_bot.py](../backend/slack_bot.py),
seguimiento personal en [backend/personal_eval.py](../backend/personal_eval.py), revisión CA en
[backend/ca_reviews.py](../backend/ca_reviews.py). El botón Atrás está en
[backend/conversation_back.py](../backend/conversation_back.py).

---

## 4. Arquitectura y stack

**Un solo proceso Python** que arranca en `bot.py` → `backend/main.py` y levanta todo; el frontend
React se compila con Vite y **lo sirve el propio backend** en producción.

```
                        ┌──────────────────────────────────────────────┐
                        │           bot.py → backend/main.py            │
  Slack ⇄ WebSocket ───►│  Socket Mode (hilo principal, bloqueante)     │
  (Bolt, Socket Mode)   │  slack_bot / ca_reviews / personal_eval       │
                        │                                               │
                        │  8 hilos daemon:                              │
                        │   · 3 ciclos de ENVÍO (proyecto / CA / pers.) │
                        │   · 3 ciclos de RECORDATORIO Slack            │
                        │   · 1 ciclo de recordatorio WEB               │
                        │   · 1 API FastAPI (uvicorn :8000)             │◄── HTTP ── SPA React
                        └───────────────┬───────────────────────────────┘    (frontend/dist,
                                        │                                     servida por FastAPI)
                     ┌──────────────────┼───────────────────┐
                     ▼                  ▼                   ▼
                 Notion API        Anthropic API          SMTP
             (única "base de    (informes, resúmenes,  (reset contraseña,
              datos" real)       sesión anual)          código registro)
```

**Stack** ([requirements.txt](../requirements.txt), [frontend/package.json](../frontend/package.json)):

- **Backend**: Python 3.11 · Slack Bolt 1.28 (Socket Mode, sin webhooks) · FastAPI 0.139 + uvicorn ·
  notion-client 3.1 · anthropic 0.111 · python-docx + mammoth (Word) · reportlab + pillow (PDF) · pytest.
- **Frontend**: React 19 + Vite 7, **sin ninguna otra librería** (ni router, ni axios, ni gestor de
  estado). Fetch nativo + token Bearer.
- **Sin base de datos SQL**: Notion es la persistencia. Sin cron externo: hilos `while True + sleep`.
- **IA**: Claude `claude-sonnet-4-6` (informes, resúmenes, sesión anual) y `claude-haiku-4-5`
  (chat de dudas del plan de acción, [backend/eval_anual_sesion.py:1302](../backend/eval_anual_sesion.py)).

Decisión consciente: el backend es **síncrono** (Notion se pagina con ThreadPool donde hace falta);
no intentes "asyncificarlo" a la ligera.

---

## 5. Estructura del proyecto

```
prueba-slack/
├── bot.py                    # Punto de entrada (3 líneas → backend.main.main())
├── requirements.txt
├── Dockerfile                # Multi-stage: Node compila frontend → imagen Python lo sirve
├── docker-compose.yml        # Servicio "api", 8001:8000, env_file .env
├── deploy.sh / deploy-template.sh  # Deploy por SSH al NAS — LEGADO, NAS descartado (§8)
├── run_backend.ps1           # Arranque local en Windows (carga .env y lanza python bot.py)
├── .env / .env.example / .env.nas  # Config real / plantilla / valores del NAS
├── migration_notion.py       # One-off: reorganizó la estructura de Notion (idempotente)
├── traducciones_*.json       # Cachés de traducción de los scripts i18n (no runtime)
├── backend/
│   ├── main.py               # Arranque: valida config, lanza 8 hilos + Socket Mode
│   ├── config.py             # TODAS las env vars, constantes, CSS de marca, anti-inyección
│   ├── clients.py            # Clientes globales: slack_app, notion, anthropic (ClienteIA)
│   ├── state.py              # Estado conversacional en memoria (RLock); se pierde al reiniciar
│   ├── notion_service.py     # ★ Capa de datos completa sobre Notion (~4.800 líneas)
│   ├── slack_bot.py          # ★ Flujo Slack de evaluación mensual + router de TODOS los mensajes
│   ├── personal_eval.py      # Flujo Slack de seguimiento personal
│   ├── ca_reviews.py         # Flujo Slack del Career Advisor
│   ├── project_evals.py      # Evaluaciones estructuradas por proyecto (lado web)
│   ├── evaluaciones_extra.py # Evaluaciones fuera de proyecto
│   ├── eval_anual_sesion.py  # Sesión anual asistida por IA (estado en JSON de disco)
│   ├── eval_tracking.py      # Cumplimiento: qué evaluación se asignó/completó por ciclo
│   ├── reports.py            # Informe de evaluaciones con Claude + trayectoria + caché
│   ├── skill_informes_anual.py    # Informe anual IGENERIS (Claude + DOCX/HTML, anti-alucinación)
│   ├── skill_opiniones_ca.py      # PDF/HTML de opiniones del CA
│   ├── skill_pdfs_fuentes.py      # PDFs de fuentes en bruto
│   ├── skill_resumen_evaluacion.py# Resumen por competencias para el CA
│   ├── users.py              # Auth web: PBKDF2, sesiones hasheadas, registro, reset por email
│   ├── hierarchy.py          # Cargo → nivel; relación Top-Bottom/Bottom-Top/Same Level
│   ├── anonimato.py          # Anonimato de evaluadores (dashboard_web/anonimato.json)
│   ├── ia.py                 # Envoltorio Anthropic: errores → ErrorIA + cola (máx. 3 análisis)
│   ├── i18n.py / i18n_pt.py  # Textos del bot/informes ES-EN / overlay PT
│   ├── recordatorios_web.py  # Recordatorios duraderos de evals lanzadas desde la web
│   ├── slack_lists.py        # Slack Lists "Evaluaciones pendientes" (opcional, off por defecto)
│   ├── slack_carga.py        # Animación "cargando" en hilos de Slack
│   ├── conversation_back.py  # Botón ⬅️ Atrás (pila de estados)
│   ├── api/
│   │   ├── app.py            # FastAPI: middlewares, routers, montaje de la SPA
│   │   ├── deps.py           # require_session / require_admin / exigir_acceso_advisee
│   │   ├── errors.py         # Excepción → JSON uniforme
│   │   ├── hardening.py      # Rate limits, límites de body, cabeceras de seguridad
│   │   ├── files.py          # /api/files/* con control de acceso por prefijo y rol
│   │   └── routers/          # auth, perfiles, ca, project_evals, evaluaciones_extra,
│   │                         # personal_slack, eval_anual, reports, admin
│   ├── dashboard_web/        # ⚠️ DATOS, no web: users.json, sesiones.json, anonimato.json,
│   │                         # sesion_anual_*.json, informes/PDFs generados, evaluabot.log
│   ├── tests/                # Suite pytest (API, permisos, privacidad, eval anual)
│   ├── create_users_from_employees.py  # Alta de usuarios web desde la Lista de empleados
│   ├── generar_i18n_pt.py    # Genera i18n_pt.py y frontend/src/pt.js con Claude
│   └── migracion_idioma_preguntas.py   # One-off: bilingüizó las preguntas en Notion
├── frontend/
│   ├── src/main.jsx          # ★ TODA la SPA (~6.200 líneas, ~40 componentes)
│   ├── src/i18n.js + pt.js   # i18n frontend (PT cargado bajo demanda)
│   ├── src/styles/           # globals.css (tokens Igeneris) + components.css; styles.css layout
│   ├── src/legal/*.md        # Privacidad y términos (solo ES)
│   └── vite.config.js        # build es2020, chunk separado para React; dev en :5173
├── skills/                   # Docs de referencia de los prompts de informes (NO se leen en
│                             # runtime: los prompts reales están embebidos en backend/skill_*.py)
├── scripts/diff_api_routes.py# QA de la migración a FastAPI (comparador de respuestas)
└── docs/                     # Documentación previa: arquitectura.md, guia_usuario*.md,
                              # guia_detallada/ (por módulo), MIGRACION_GCP.md, etc.
```

---

## 6. Componentes principales

- **`backend/notion_service.py`** — la "base de datos". Localiza/crea páginas y BDs de Notion **por
  nombre** (no por ID) bajo la página raíz, de forma idempotente (caché → children.list → search →
  crear); una BD por persona (`Evaluaciones - X`, `Opiniones - X`, `Objetivos - X`, `Seg Personal - X`,
  `Plan de acción - X`).
  Caché en memoria con TTL 300 s y locks. Todas las funciones devuelven valor neutro ante fallo de
  Notion (la app no se cae). Depende de `clients.py`, `config.py`, `utils.py`.
- **`backend/slack_bot.py`** — envío del ciclo mensual y **router único** de mensajes
  (`handle_message_events`, ~línea 1508): decide por `thread_ts` a qué flujo va cada mensaje. Máquina
  de estados en `state.conversaciones[user_id]`. Los botones fabrican eventos sintéticos de texto para
  converger con el texto libre. Depende de `hierarchy`, `notion_service`, `eval_tracking`, `state`,
  `conversation_back`, `slack_carga`, `i18n`.
- **`backend/ca_reviews.py` / `personal_eval.py`** — los otros dos flujos DM; misma mecánica.
- **`backend/project_evals.py`** — evaluaciones estructuradas por proyecto desde la web: activación
  (elige equipo/manager), cálculo en servidor del tipo de evaluación por jerarquía, guardado en las BDs
  `Activaciones Evaluaciones Proyectos`, `Autoevaluacion`, `Evaluacion Mismos Miembros`,
  `Evaluacion Miembros a Manager`, `Evaluacion Manager a Miembros` (docstring, líneas 1-17).
- **`backend/eval_anual_sesion.py` + `skill_informes_anual.py`** — evaluación anual: sesión
  conversacional por áreas con estado en `dashboard_web/sesion_anual_{slug}.json`, generación del DOCX/HTML
  con **anti-alucinación** (citas obligatorias `[E#]/[O#]/...`, validador de citas y segunda llamada
  "auditora"). El plan de acción confirmado se publica además en Notion (`Plan de acción - {Nombre}`).
- **`backend/users.py` + `api/deps.py`** — auth: PBKDF2-HMAC-SHA256 (120k iteraciones), sesiones con
  token opaco cuyo **hash** se persiste en `dashboard_web/sesiones.json`, identidad ligada al empleado
  por email (no al username). Reset y verificación de registro por SMTP.
- **`backend/eval_tracking.py`** — BD Notion "Evaluaciones recibidas y completadas": una fila por
  asignación (persona, tipo, ciclo). Alimenta el panel de cumplimiento del admin, las tareas del
  dashboard y los recordatorios web duraderos.
- **`backend/ia.py`** — único punto de entrada a Anthropic: mapea errores del SDK a `ErrorIA` con
  códigos legibles y limita a 3 análisis anuales simultáneos (espera máx. 180 s).
- **`backend/api/hardening.py`** — rate limit de auth (8/min·IP) y de generación (10/min·cliente),
  body ≤1 MB (15 MB al subir informe), cabeceras de seguridad.
- **`frontend/src/main.jsx`** — toda la SPA. `apiRequest` (Bearer), `apiRequestCached`
  (stale-while-revalidate en sessionStorage, TTL 5 min, prefijo `ebc_`), `openAuthedFile`
  (fetch+blob, el token nunca va en la URL), navegación propia con `history.pushState`.

---

## 7. Configuración y entorno

Toda la config se lee en [backend/config.py](../backend/config.py). La plantilla comentada es
[.env.example](../.env.example); `.env` (local) y `.env.nas` (valores del NAS) tienen las mismas 17 claves.

**Obligatorias** (el arranque falla o avisa sin ellas):
- `SLACK_BOT_TOKEN` (xoxb-…) y `SLACK_APP_TOKEN` (xapp-…, Socket Mode) — config.py:88-89.
- `NOTION_TOKEN` y `NOTION_PARENT_PAGE_ID` (página raíz de Notion donde vive todo) — validada en
  [backend/main.py](../backend/main.py) líneas 17-26.
- `ANTHROPIC_API_KEY` — sin ella arranca, pero sin informes ni resúmenes IA.

**Importantes**:
- `APP_MODE` — `prueba` (por defecto: envía al arrancar y cada 30 días, **solo** a
  `SLACK_TEST_USER_ID`, admite varios separados por coma) o `produccion` (según calendario de Notion, a todos).
- `NOTION_EMPLOYEES_DATABASE_ID` — BD "Lista de empleados". El resto de BDs se resuelven **por nombre**
  (defaults en config.py:104-123, sobreescribibles con `NOTION_*_PAGE_NAME` / `_DATABASE_NAME`).
- SMTP (`SMTP_HOST/PORT/USER/PASSWORD/FROM/USE_TLS`) — solo para reset de contraseña y código de
  registro. Probar con `python -m backend.test_smtp destinatario`.
- `FRONTEND_ORIGIN`, `CORS_EXTRA_ORIGINS`, `APP_PUBLIC_URL` (enlaces en emails), `VITE_API_BASE_URL`
  (vacío en producción = mismo origen).
- `REGISTRO_WEB_HABILITADO` (off: las altas se hacen con `backend/create_users_from_employees.py`),
  `SLACK_LISTAS_PENDIENTES_HABILITADO` (off: requiere Slack de pago), `PORT`/`PUERTO_WEB` (8000).

**Requisitos de la app de Slack** (README.md): Socket Mode activado; scopes `chat:write`,
`channels:history`, `users:read` (+ `lists:read/write` si se activan las Lists). Tras añadir un scope,
"Reinstall to Workspace".

**Requisitos de Notion**: integración interna cuyo secret es `NOTION_TOKEN`, **conectada** a la página
raíz (Connections en la página `NOTION_PARENT_PAGE_ID`) para que el bot pueda crear BDs debajo.

Ficheros `.env_ANTIGUO`, `.env.prueba.backup`, `.env_backup_2026-07-06_pre-migracion-empresa`: backups
históricos, no los usa nada.

---

## 8. Cómo ejecutarlo y desplegarlo

### Local (Windows)
```powershell
# Backend (carga .env y lanza python bot.py):
.\run_backend.ps1
# Frontend en modo dev (otra terminal):
cd frontend; npm install; npm run dev     # Vite en :5173, llama al backend en :8000
```
Con `APP_MODE=prueba` el bot envía los tres ciclos al arrancar, solo a `SLACK_TEST_USER_ID`.
Web: `http://localhost:5173` (dev) o `http://localhost:8000` si existe `frontend/dist` compilado.
API docs: `http://localhost:8000/docs`.

### Producción
> ⚠️ **El despliegue en el NAS Synology está DESCARTADO** (decisión de 07/2026): ya no es el
> despliegue de la app, aunque el repo conserva sus scripts (`deploy.sh`, `deploy-template.sh`,
> [ONBOARDING_DEVS.md](../ONBOARDING_DEVS.md), `docker-compose.yml` con el mapeo 8001:8000).
> **⚠️ Pendiente de confirmar dónde corre/correrá la app en producción.** El plan documentado es
> Google Cloud ([docs/MIGRACION_GCP.md](MIGRACION_GCP.md)): Cloud Run para el proceso y Cloud Storage
> para `dashboard_web`; el código ya tiene ganchos (variable `PORT` en `config.py:46`, comentario en
> [.dockerignore](../.dockerignore)). [DEPLOY.md](../DEPLOY.md) describe además la variante
> backend-en-PaaS + frontend en Vercel (`VITE_API_BASE_URL`).

**Lo que necesita cualquier despliegue** (independiente del destino):
- Contenedor: el [Dockerfile](../Dockerfile) es multi-stage — Node 20 compila `frontend/dist` y la
  imagen `python:3.11-slim` lo copia y arranca `python bot.py` (no hace falta Node fuera del build).
  `docker-compose.yml` de referencia: servicio `api`, `env_file: .env`, `restart: unless-stopped`.
- Un `.env` con las 17 claves (sección 7) en el entorno de ejecución; nunca dentro de la imagen
  (`.dockerignore` excluye `.env*`).
- ⚠️ **Proceso único siempre encendido**: Socket Mode es una conexión WebSocket permanente con Slack.
  Si se cae, no llegan mensajes ni envíos, y los envíos perdidos no se recuperan retroactivamente.
- Decidir dónde vive `backend/dashboard_web/` (sesiones, informes generados): dentro del contenedor se
  pierde en cada rebuild; el plan GCP lo lleva a Cloud Storage.

**Scripts del NAS (legado, por si se reutilizan como base):** `deploy.sh` empaqueta con tar, sube por
SSH y hace `docker-compose up -d --build` en destino, preservando el `.env` remoto. Ojo si se recicla:
su exclude de datos apunta a `./dashboard_web` pero la carpeta real es `backend/dashboard_web`, así que
los datos locales viajan en el paquete (no entran en la imagen gracias al `.dockerignore`, pero quedan
copiados en el filesystem destino).

---

## 9. Integraciones y datos

### Notion = la base de datos
Todo cuelga de la página raíz `NOTION_PARENT_PAGE_ID` ("Evaluaciones Continuas"), organizada en dos
contenedores (estructura completa en el docstring de [migration_notion.py](../migration_notion.py)):

```
Página raíz
├── TO-DO
│   ├── Datos a Monitorizar → Lista de empleados, Lista CA, Calendario evaluaciones,
│   │                          Deadlines evaluaciones, Usuarios Web, Gestión de MiddleOffice
│   ├── Datos opcionalmente modificables → Criterios de evaluaciones, Ejemplos de Guía
│   └── Preguntas Chatbot → Preguntas por área (Negocio/MO/Palantir/personal/seguimiento CA)
└── TO-SEE
    ├── Resultados Evaluaciones → "Evaluaciones - {nombre}" (una BD por empleado),
    │     "Opiniones - {advisee}", "Seg Personal - {nombre}", Barbecho, evaluaciones extra
    ├── Evaluaciones recibidas y completadas   (tracking de cumplimiento)
    ├── Activaciones de permisos → Solicitudes Evaluaciones Extra, accesos CA
    ├── Planes de acción → "Plan de acción - {Nombre}"
    └── Informes finales / Evaluaciones anuales / Log evaluación anual asistida
```

Claves del modelo: la **Lista de empleados** es el directorio maestro (nombre, email, Slack ID, cargo,
área, idioma, país, baja); la **Lista CA** relaciona cada CA con sus advisees (columnas A1, A2…);
el **Calendario evaluaciones** y **Deadlines** gobiernan los envíos (editables por el admin sin tocar
código); las **preguntas** dependen del área y de la relación jerárquica evaluador↔evaluado
(Top-Bottom / Bottom-Top / Same Level, calculada en [backend/hierarchy.py](../backend/hierarchy.py)).
El feedback de abajo hacia arriba es **confidencial** (sin autor, solo visible agregado/anónimo).

### Otras integraciones
- **Slack**: Bolt en Socket Mode ([backend/clients.py](../backend/clients.py)); tokens xoxb + xapp;
  Slack Lists opcional (`backend/slack_lists_config.json`, en .gitignore).
- **Anthropic**: `claude-sonnet-4-6` + `claude-haiku-4-5`, siempre a través de `ia.py`; instrucción
  **anti-inyección de prompts** (`config.INSTRUCCION_ANTIINYECCION`) en todas las llamadas que procesan
  texto de usuarios; prompt caching del system estático.
- **SMTP** (Gmail, TLS 587): solo auth (reset + código de registro).
- **Ficheros locales** (`backend/dashboard_web/`): sesiones, usuarios fallback, anonimato, sesiones
  anuales, artefactos generados (informes DOCX/HTML, PDFs, trayectorias) y sus cachés
  (`*_cache.json`, huella SHA-256 de los datos de Notion). En Docker viven **dentro del contenedor**
  (docker-compose.yml no declara volúmenes): un rebuild los pierde — ver Riesgos.

### Flujo end-to-end típico
1. El ciclo de envío abre DM a todos y anota la asignación en el tracking.
2. El empleado responde en el hilo → `guardar_en_notion` escribe en `Evaluaciones - {evaluado}` →
   `marcar_completada` cierra el tracking.
3. El CA recibe su DM cada 4 semanas, revisa y guarda su opinión en `Opiniones - {advisee}`.
4. En la web, el CA ejecuta el asistente anual → informe DOCX/HTML con citas verificadas → lo publica
   y da acceso al empleado, que lo ve en su Dashboard.

---

## 10. Mantenimiento

**Cambios que NO requieren tocar código** (los hace el admin en Notion):
- Preguntas de las evaluaciones (BDs bajo "Preguntas Chatbot"), criterios y ejemplos.
- Fechas de los ciclos ("Calendario evaluaciones") y plazos ("Deadlines evaluaciones") — los ciclos
  releen el calendario cada hora incluso mientras esperan.
- Altas/bajas de empleados (Lista de empleados; el bot cachea 5 min), asignación de advisees (Lista CA),
  idioma/país por empleado.

**Cambios habituales de código — dónde tocar:**
| Cambio | Dónde |
|---|---|
| Texto de la UI del bot / informes | `backend/i18n.py` (ES/EN) → regenerar PT con `python backend/generar_i18n_pt.py --backend` |
| Texto de la web | `frontend/src/i18n.js` → `--frontend` para regenerar `pt.js` |
| Un flujo del bot (mensual/personal/CA) | `slack_bot.py` / `personal_eval.py` / `ca_reviews.py` (máquinas de estado sobre `state.py`) |
| Un endpoint de la API | `backend/api/routers/*.py` (lógica en el módulo de dominio correspondiente) |
| Una pantalla de la web | `frontend/src/main.jsx` (buscar el componente por nombre) |
| Formato del informe anual / prompts | `backend/skill_informes_anual.py` (los prompts están embebidos en el .py; `skills/eval-informes-rrhh.md` es solo documentación de referencia) |
| Cadencias/offsets de envío | `backend/config.py` (líneas 21-42: `INTERVALO_PRUEBA_DIAS`, `DIA_ENVIO_PRODUCCION`, `HORA_ENVIO_PRODUCCION`, `CA_OFFSET_DIAS`, `PERSONAL_OFFSET_HORAS`, `RECHECK_CALENDARIO_SEGUNDOS`) |
| Alta de usuarios web | `python backend/create_users_from_employees.py` (DRY-RUN; `--apply` para ejecutar; genera CSV con contraseñas temporales) |

**Rutinas:**
- Tests: `pytest backend/tests` (mockean Notion; cubren permisos, privacidad, eval anual, tracking).
  `test_preguntas.py` (raíz) y `backend/test_smtp.py` son utilidades manuales que tocan servicios reales.
- Logs: `backend/dashboard_web/evaluabot.log` (rotativo) o `docker logs -f evaluabot`.
- Tras cambiar preguntas/criterios en Notion no hay que reiniciar (caché TTL 5 min).
- Los informes cachean por huella de datos: si cambian los datos en Notion, se regeneran solos; para
  forzar, borrar el `*_cache.json` correspondiente en `dashboard_web/`.

---

## 11. Riesgos y puntos frágiles

1. **Estado conversacional en memoria** (`backend/state.py`): al reiniciar el proceso se pierden las
   conversaciones a medias en Slack y los `ts` de los mensajes raíz — los recordatorios "en memoria"
   de ese ciclo dejan de funcionar hasta el siguiente envío (los web sí sobreviven vía Notion).
2. **`dashboard_web/` vive dentro del contenedor**: [docker-compose.yml](../docker-compose.yml) no
   monta ningún volumen, así que en cualquier despliegue en contenedor cada rebuild **pierde** sesiones
   web (los usuarios se desloguean), sesiones anuales a medio hacer (`sesion_anual_*.json`), informes
   generados y `users.json`. Notion no pierde nada, pero una sesión anual sin finalizar sí. Al montar
   el despliegue definitivo, resolver esto (volumen persistente o Cloud Storage como prevé el plan GCP).
3. **Proceso único y siempre encendido**: Socket Mode + scheduling por hilos. Si el contenedor cae en
   la ventana de un envío, ese envío no se recupera. No hay healthcheck ni supervisión más allá de
   `restart: unless-stopped`.
4. **Dos monolitos gigantes**: `frontend/src/main.jsx` (~6.200 líneas) y `backend/notion_service.py`
   (~4.800). Funcionan, pero cualquier cambio exige buscar bien; no hay tests del frontend.
   (Nota: el código huérfano de la funcionalidad retirada "responder las evaluaciones de Slack desde
   la web" — componentes `EvaluacionesSlackPage`/`ChatEval*` y 5 endpoints — se **eliminó el
   2026-07-21**: −808 líneas en main.jsx, `personal_slack.py` reducido a `/api/tareas-slack`, 81
   claves i18n purgadas; build y 318 tests en verde tras la limpieza.)
5. **Notion como BD**: sin transacciones, rate limits (429) y latencia. El código lo mitiga
   (idempotencia, cachés, degradación elegante), pero operaciones masivas pueden ser lentas.
   Historial conocido de split-brain al mover páginas a mano (ver
   [docs/diagnostico_paginas_vacias_notion.md](diagnostico_paginas_vacias_notion.md)): **no mover/renombrar
   las páginas estructurales de Notion sin revisar ese documento**.
6. **El portugués elegido desde la web no persiste**: la web sí se muestra en PT (catálogo
   `frontend/src/pt.js` cargado bajo demanda) y si la columna Idioma de Notion tiene PT — puesto desde
   Slack o por el admin — `/api/me` lo devuelve y la web arranca en portugués sin problema. Lo roto es
   la persistencia **desde la rueda de idioma de la web**: `POST /api/set-idioma` solo acepta `es`/`en`
   y convierte `pt` en `es` ([backend/api/routers/auth.py:86-87](../backend/api/routers/auth.py)), o
   sea que elegir PT en la web **escribe "ES" en Notion** (puede incluso pisar un PT puesto desde
   Slack), y al recargar, `/api/me` sobrescribe la elección manual (main.jsx ~6875) y la web vuelve a
   español. Dentro de la sesión se ve en PT; se pierde al recargar salvo que Notion tenga PT. Arreglo:
   añadir `"pt"` a la tupla de `set_idioma` en auth.py.
7. **Sin despliegue de producción definido** (07/2026): el NAS está descartado y el destino final
   (previsiblemente GCP) aún no está montado. Hasta entonces, el bot solo funciona si alguien lo
   ejecuta (local o donde sea) — y los scripts `deploy*.sh` del repo apuntan al NAS descartado, con un
   bug de exclusión que sube los datos locales de `backend/dashboard_web` al destino (ver sección 8)
   si se reutilizan tal cual.
8. **Documentación antigua con datos obsoletos**: el `README.md` describe el producto primitivo
   (canal + cada 5 min, `web_server.py` — ya no existe), y `docs/arquitectura.md` /
   `docs/guia_usuario*.md` aún mencionan funciones eliminadas o corregidas (el botón 🚨 Urgencia del
   seguimiento personal ya no existe en el código; el "bug" del desfase del ciclo CA está corregido —
   `ca_reviews.py:1649` aplica `CA_OFFSET_DIAS=7` vía `notion_service.py:5068`). Ante cualquier
   conflicto, manda el código.
9. **Secretos**: `.env` con tokens reales en las máquinas de los devs y el NAS; sin gestor de secretos.
   El registro web está desactivado — las contraseñas temporales se reparten vía CSV generado
   (`dashboard_web/usuarios_web_creados.csv`): borrar ese CSV tras repartirlas.
10. **Costes IA**: los informes llaman a Claude; hay caché por huella y cola de 3 simultáneos, pero un
    uso intensivo del asistente anual consume API. El modelo está fijado en código
    (`claude-sonnet-4-6`) — actualizarlo requiere editar los módulos `skill_*` y `reports.py`.
11. **Sin CI/CD**: no hay pipeline; los tests se ejecutan a mano y el deploy es un script personal.

---

*Documentación complementaria: [docs/arquitectura.md](arquitectura.md) (técnico profundo, 2026-07-14),
[docs/guia_usuario_completa.md](guia_usuario_completa.md) (por tipo de usuario) y
[docs/guia_detallada/](guia_detallada/) (función por función). Son útiles para profundizar, pero
contienen puntos ya desfasados (ver riesgo 8): ante discrepancia con esta guía o con el código,
manda el código. Esta guía fue verificada afirmación por afirmación contra el código el 2026-07-21.*
