## `frontend/src/main.jsx` — La aplicación web (React)

Documentación exhaustiva del frontend de EvaluaBot. Todo el código de la SPA vive en un único archivo de ~4.101 líneas: [main.jsx](../frontend/src/main.jsx). Esta guía cubre **todos** los componentes, páginas, hooks y funciones auxiliares que contiene.

> A lo largo del documento hay marcadores **📷 [Captura pendiente]** indicando qué pantalla conviene fotografiar en cada punto.

---

### Propósito y arquitectura

**Qué es.** Una *Single Page Application* (SPA) escrita en React 19 que sirve de interfaz web al backend de People Analytics de EvaluaBot (evaluaciones de proyecto, evaluaciones anuales, seguimiento de advisees, informes, etc.). Se monta con `createRoot` sobre `#root` ([main.jsx:4097](../frontend/src/main.jsx#L4097)).

**Un solo archivo, sin router.** No usa React Router. Toda la navegación entre pantallas se resuelve con **estado local en el componente raíz `App`** ([main.jsx:3921](../frontend/src/main.jsx#L3921)): una variable `page` (un objeto `{ type, ...props }`) que actúa como "ruta", más `adminMode` para el modo del administrador. El árbol se re-renderiza según esos valores mediante una cadena de `if (page?.type === "...")`.

**Navegación por hash — sólo para lo legal.** El `hash` de la URL (`#privacidad`, `#terminos`) se usa exclusivamente para las páginas legales y para la recuperación de contraseña. `getLegalDoc()` ([main.jsx:15](../frontend/src/main.jsx#L15)) lee `window.location.hash`; un listener de `hashchange` en `App` ([main.jsx:3931](../frontend/src/main.jsx#L3931)) actualiza el estado `legalDoc`. El resto de la navegación (dashboard, wizards, formularios) **no cambia la URL**: se hace con `navigate()`.

**Estado global.** No hay Redux ni Context. El estado "global" es simplemente el estado de `App`:
- `token` — token de sesión (Bearer). Se inicializa desde `localStorage` o `sessionStorage` ([main.jsx:3923](../frontend/src/main.jsx#L3923)).
- `user` — objeto de usuario devuelto por `/api/me`.
- `page` — pantalla actual.
- `adminMode` — `null | "personal" | "admin"` (selección de rol del admin).
- `completedEvals`, `slackEvalCompletadas` — memoria en memoria de qué evaluaciones se han completado en la sesión.
- `legalDoc` — documento legal abierto, si lo hay.

**Cómo llama a la API.** Todas las llamadas pasan por `apiRequest(path, { token, method, body })` ([main.jsx:50](../frontend/src/main.jsx#L50)), que hace `fetch` contra `API_BASE` (`VITE_API_BASE_URL` o `http(s)://<host>:8000`, [main.jsx:20](../frontend/src/main.jsx#L20)), añade `Authorization: Bearer <token>` y `Content-Type: application/json`, y lanza un `Error` con `data.error` si la respuesta no es OK. Existe además una capa de caché con TTL de 5 min sobre `sessionStorage` (`apiRequestCached`, [main.jsx:94](../frontend/src/main.jsx#L94)) con patrón *stale-while-revalidate*. Las descargas de ficheros (`.docx`, PDF) no usan `apiRequest`: hacen `fetch` directo y crean un `Blob` + enlace de descarga.

**Autenticación (token).** El login (`/api/login`) devuelve `{ token, user }`. Según el check "Recuérdame" se guarda en `localStorage` (persistente) o `sessionStorage` (sesión). En cada arranque, `App` valida el token contra `/api/me`; si falla, limpia caché y token. `handleLogout()` ([main.jsx:3960](../frontend/src/main.jsx#L3960)) borra ambos almacenamientos, la caché y todo el estado.

**i18n.** La internacionalización se importa desde `./i18n` ([main.jsx:8](../frontend/src/main.jsx#L8)): `t(clave, params)` traduce, `setLang` / `getLang` fijan/leen el idioma, `nombreMes` da el nombre del mes. El idioma se establece a partir de `user.idioma` tras login y tras `/api/me`. **Ojo:** los tres chats conversacionales (`ChatEvalPersonal`, `ChatEvalCA` y partes de otros) tienen **muchos textos en español "hardcodeados"**, no traducidos con `t()`.

**Barra de carga global.** Un pequeño *store* (`_loading`, [main.jsx:29](../frontend/src/main.jsx#L29)) cuenta peticiones en curso y el componente invisible `TopLoadingBar` ([main.jsx:3649](../frontend/src/main.jsx#L3649)) traduce ese progreso a variables CSS que pintan una barra sobre `.nav`.

---

### Mapa de navegación

Las "rutas" se controlan con `page.type` en `App` ([main.jsx:3921](../frontend/src/main.jsx#L3921)). Antes de llegar a esa cadena, hay tres guardas de nivel superior:

| Condición | Pantalla |
|---|---|
| `legalDoc` presente | `LegalPage` |
| sin `token` / sin `user` / hay token de reset en URL | `AuthScreen` |
| admin con `adminMode === null` | `AdminRoleSelect` |
| admin con `adminMode === "admin"` | `AdminPanel` |

Rutas por `page.type` (navegación interna, **no** cambian la URL):

| `page.type` | Componente destino |
|---|---|
| `null` (por defecto) | `Dashboard` |
| `advisees-list` | `AdviseesList` |
| `advisee-detail` | `AdviseeDetail` |
| `mis-objetivos` | `MisObjetivosPage` |
| `objetivos` | `ObjetivosPage` |
| `subir-informe` | `SubirInformePage` |
| `eval-anual` | `EvaluacionAnualWizard` |
| `activar-evaluaciones-proyecto` | `ActivarEvaluacionesProyectoPage` |
| `mis-proyectos-activos` | `MisProyectosActivosPage` |
| `evaluaciones-proyecto` | `EvaluacionesProyectoPage` |
| `evaluaciones-slack` | `EvaluacionesSlackPage` |
| `historial-evaluaciones` | `HistorialEvaluacionesPage` |
| `formulario-evaluacion-proyecto` | `FormularioEvaluacionProyecto` |

Hashes de URL reconocidos: `#privacidad`, `#terminos` (páginas legales); `?reset=<token>` / `#reset=<token>` / `/reset/<token>` (restablecer contraseña, detectado por `getResetToken()`, [main.jsx:118](../frontend/src/main.jsx#L118)).

> **Nota:** el enunciado pedía documentar `EvalAnualLogPage`. **Ese componente no existe en el código**; el flujo de evaluación anual se resuelve enteramente en `EvaluacionAnualWizard`.

---

### Referencia de componentes/pantallas

#### `PasswordInput`
- **Qué es / cuándo aparece:** campo de contraseña reutilizable con botón de ojo para mostrar/ocultar. [main.jsx:129](../frontend/src/main.jsx#L129).
- **Quién lo ve (rol):** cualquiera, dentro de `AuthScreen`.
- **Qué muestra:** un `<input>` de tipo `password`/`text` y un botón de alternancia (icono de ojo).
- **Acciones:** el botón conmuta `visible` (estado local) → cambia el `type` del input.
- **Props:** `value`, `onChange`, `placeholder`, `required`, `minLength`.
- **Endpoints:** ninguno.
- **Marcador de captura:**
> 📷 **[Captura pendiente: campo de contraseña con el icono de ojo, en estado oculto y visible]**

---

#### `Footer`
- **Qué es / cuándo aparece:** pie de página presente en casi todas las pantallas. [main.jsx:154](../frontend/src/main.jsx#L154).
- **Quién lo ve:** todos.
- **Qué muestra:** copyright "© {año} Igeneris" y enlaces a "Privacidad" (`#privacidad`) y "Términos" (`#terminos`).
- **Acciones:** los enlaces cambian el hash → abren `LegalPage`.
- **Props / estado:** ninguno.
- **Endpoints:** ninguno.

---

#### `LegalContent` y `LegalPage`
- **Qué es / cuándo aparece:** `LegalPage` ([main.jsx:227](../frontend/src/main.jsx#L227)) muestra la Política de privacidad o los Términos cuando el hash es `#privacidad` o `#terminos`. `LegalContent` ([main.jsx:181](../frontend/src/main.jsx#L181)) es el renderizador de Markdown ligero (encabezados, listas, negrita y enlaces internos) usado dentro.
- **Quién lo ve:** todos (accesible incluso sin login).
- **Qué muestra:** navbar con logo y botón "Volver", más el texto legal cargado de `./legal/privacidad.md` / `./legal/terminos.md` (importados con `?raw`).
- **Acciones:** "Volver" → `onBack` (`closeLegal`, limpia el hash y vuelve a la pantalla anterior).
- **Props:** `LegalPage` recibe `doc` (`"privacidad"`/`"terminos"`) y `onBack`.
- **Endpoints:** ninguno (contenido embebido en el bundle).
- **Marcador de captura:**
> 📷 **[Captura pendiente: página legal — p. ej. Política de privacidad con el navbar y el botón Volver]**

---

#### `AuthScreen`
- **Qué es / cuándo aparece:** pantalla de acceso. Se muestra cuando no hay `token`/`user` o cuando la URL trae un token de reset. [main.jsx:707](../frontend/src/main.jsx#L707).
- **Quién lo ve:** usuarios no autenticados.
- **Qué muestra:** un formulario que cambia según el `mode` (estado local): `login`, `register`, `forgot`, `reset`, `verify-code`. Incluye logo, título/descripción según modo, mensajes de error/éxito, y enlaces legales al pie (sólo en login).
  - **login:** usuario/email + contraseña + check "Recuérdame" + enlace "He olvidado mi contraseña".
  - **register:** usuario + contraseña + repetir contraseña, con validación de contraseña fuerte (`isStrongPassword`: ≥8 chars, mayúscula y símbolo) y coincidencia.
  - **forgot:** email para pedir enlace de restablecimiento.
  - **reset:** nueva contraseña + repetir (si hay token de reset en la URL).
  - **verify-code:** campo numérico de 6 dígitos cuando el backend responde `VERIFICACION_REQUERIDA:<email>`.
- **Acciones y endpoints:**
  - Enviar en login → `POST /api/login`; guarda token en `localStorage`/`sessionStorage` según "Recuérdame" y llama `onLogin`.
  - Enviar en register → `POST /api/register`, vuelve a login.
  - Enviar en forgot → `POST /api/password-reset/request`.
  - Enviar en reset → `POST /api/password-reset/confirm`, limpia token y vuelve a login.
  - "He olvidado mi contraseña" → cambia a modo `forgot`. "Volver al inicio de sesión" → modo `login`.
- **Props / estado principal:** prop `onLogin`. Estado: `mode`, `form`, `rememberMe`, `error`, `message`, `loading`, `maskedEmail`; derivados de validación (`passwordInvalid`, `passwordsMismatch`, `canSubmit`).
- **Endpoints:** `/api/login`, `/api/register`, `/api/password-reset/request`, `/api/password-reset/confirm`.
- **Marcador de captura:**
> 📷 **[Captura pendiente: pantalla de login (con "Recuérdame"), y variantes de registro, "olvidé contraseña", "restablecer" y "verificar código"]**

---

#### `AdminRoleSelect`
- **Qué es / cuándo aparece:** pantalla de bienvenida para el **administrador**, que elige con qué rol entrar. Se muestra cuando `is_admin` y `adminMode === null`. [main.jsx:244](../frontend/src/main.jsx#L244).
- **Quién lo ve (rol):** sólo administradores.
- **Qué muestra:** navbar con nombre del usuario y logout; dos tarjetas grandes: "Administrador" y "Perfil personal".
- **Acciones:** clic en una tarjeta → `onChoose("admin")` o `onChoose("personal")` (fija `adminMode` en `App`). "Cerrar sesión" → `onLogout`.
- **Props:** `user`, `onChoose`, `onLogout`.
- **Endpoints:** ninguno.
- **Marcador de captura:**
> 📷 **[Captura pendiente: selector de rol del admin con las dos tarjetas Administrador / Perfil personal]**

---

#### `AdminPanel`
- **Qué es / cuándo aparece:** panel de administrador. Se muestra cuando `is_admin` y `adminMode === "admin"`. [main.jsx:276](../frontend/src/main.jsx#L276).
- **Quién lo ve (rol):** sólo administradores.
- **Qué muestra:** dos vistas:
  1. **Búsqueda de empleado:** buscador de texto y rejilla de tarjetas de empleados (foto + nombre).
  2. **Ficha del empleado seleccionado:** foto, nombre, cargo y botones para ver/descargar el **informe final** (HTML y Word).
- **Acciones y endpoints:**
  - Al montar → `GET /api/evaluados` (lista de evaluados).
  - Seleccionar empleado → `GET /api/perfil-empleado?nombre=...` (foto/cargo) y `GET /api/informe-final?evaluado=...`.
  - "Ver informe final" → abre `htmlUrl` en pestaña nueva (con token en query).
  - "Descargar Word" → `openFile` descarga el `.docx` vía `Blob`.
  - "Volver" → limpia selección o `onBack`.
- **Props / estado:** prop `token`, `onBack`. Estado: `evaluados`, `search`, `selected`, `informeFinal`, `statusMsg`.
- **Endpoints:** `/api/evaluados`, `/api/perfil-empleado`, `/api/informe-final`.
- **Marcador de captura:**
> 📷 **[Captura pendiente: buscador de empleados del admin (rejilla de tarjetas) y ficha de empleado con botones de informe]**

---

#### `MisObjetivosPage`
- **Qué es / cuándo aparece:** listado de los objetivos propios del usuario. Ruta `mis-objetivos`. [main.jsx:420](../frontend/src/main.jsx#L420).
- **Quién lo ve:** cualquier empleado (sus propios objetivos).
- **Qué muestra:** hero "Mis objetivos" + lista de objetivos (fecha, CA, tipo, título, KPIs, descripción).
- **Acciones:** sólo lectura. "Volver" → `onBack`.
- **Props / estado:** `token`, `persona`, `onBack`. Estado: `objetivos`, `loading`, `error`.
- **Endpoints:** `GET /api/objetivos?nombre=<persona>`.
- **Marcador de captura:**
> 📷 **[Captura pendiente: página "Mis objetivos" con la lista de objetivos]**

---

#### `ObjetivosPage`
- **Qué es / cuándo aparece:** edición de objetivos de un **advisee** (por parte de su CA). Ruta `objetivos`. [main.jsx:472](../frontend/src/main.jsx#L472).
- **Quién lo ve (rol):** Career Advisors (CA), desde la ficha del advisee.
- **Qué muestra:**
  - Hero con foto/nombre del advisee y un **formulario "Nuevo objetivo"**: campos Título, Tipo, KPIs, Descripción; "chips" de objetivos pendientes de guardar; botones "Añadir otro" y "Guardar".
  - **Historial** de objetivos del advisee, agrupado por **año → mes** (con `<details>` colapsables). Cada objetivo tiene botón "Eliminar".
- **Acciones y endpoints:**
  - "Añadir otro" → mete el objetivo del formulario en la lista `pendientes` (sin guardar aún).
  - "Guardar" → por cada objetivo pendiente (más el del formulario) hace `POST /api/objetivos` con `{ nombre, titulo, kpis, descripcion, tipo }`, y recarga.
  - "Eliminar" → confirma y hace `DELETE /api/objetivos` con `{ page_id }`.
  - Al montar / recargar → `GET /api/objetivos?nombre=<advisee>`.
- **Props / estado:** `token`, `advisee`, `caName`, `onBack`. Estado: `objetivos`, `form`, `pendientes`, `loading`, `saving`, `deleting`, `error`, `success`; memo `objetivosPorAnio`.
- **Endpoints:** `GET/POST/DELETE /api/objetivos`.
- **Marcador de captura:**
> 📷 **[Captura pendiente: formulario de nuevo objetivo con chips pendientes, e historial agrupado por año/mes con botón Eliminar]**

---

#### `ChatEvalProyecto`
- **Qué es / cuándo aparece:** chat conversacional para hacer la **evaluación mensual/de proyecto** desde la web (pestaña "Mensuales" de `EvaluacionesSlackSection`). [main.jsx:904](../frontend/src/main.jsx#L904).
- **Quién lo ve:** cualquier empleado que deba evaluar (Negocio, MiddleOffice, Palantir).
- **Qué muestra:** una interfaz de chat (burbujas bot/usuario) que guía por pasos (`step`): intro → elegir **área** (Negocio / MiddleOffice / Palantir) → proyecto → persona a evaluar (con sugerencias/autocompletado) → **preguntas** (valoración 1–4 o texto libre) → resumen y **confirmación** → guardar → "¿más personas?" / "¿más proyectos?".
- **Comportamiento clave:**
  - **Periodo de gracia (2 días):** guarda en `sessionStorage` las evaluaciones enviadas para poder **modificarlas** durante 2 días (`GRACE_MS`). Al remontar en gracia arranca en `step = "terminado"` con opción de modificar.
  - Autocompletado de empleados (`/api/todos-empleados`), y para MiddleOffice pide una lista concreta de evaluables.
  - Menú "Modificar" para cambiar persona, proyecto o cualquier respuesta antes de confirmar.
- **Acciones y endpoints:**
  - Al montar → `GET /api/todos-empleados`.
  - Elegir área / persona → `GET /api/buscar-empleado-slack?nombre=...&area=...` (devuelve empleado, relación, preguntas, sugerencias).
  - Confirmar (nuevo) → `POST /api/guardar-evaluacion-slack`.
  - Confirmar (edición) → `POST /api/actualizar-evaluacion-slack` con `page_id`.
- **Props / estado:** `token`, `user`, `onComplete`, `onNavigate`. Estado extenso: `msgs`, `step`, `area`, `proyecto`, `evaluadoNombre`, `relacion`, `preguntas`, `preguntaIdx`, `respuestas`, `evaluadosEnSesion`, `evaluacionesGuardadas`, `editandoPageId`, sugerencias, etc.
- **Endpoints:** `/api/todos-empleados`, `/api/buscar-empleado-slack`, `/api/guardar-evaluacion-slack`, `/api/actualizar-evaluacion-slack`.
- **Marcador de captura:**
> 📷 **[Captura pendiente: chat de evaluación mensual — selección de área con botones, y una pregunta con la escala 1–4]**

---

#### `ChatEvalPersonal`
- **Qué es / cuándo aparece:** chat de **seguimiento personal privado** (visible sólo para el CA del empleado). Pestaña "Personal" de `EvaluacionesSlackSection`. [main.jsx:1400](../frontend/src/main.jsx#L1400).
- **Quién lo ve:** cualquier empleado (su seguimiento propio).
- **Qué muestra:** chat guiado: intro → escribir comentario libre. Botones auxiliares: "📋 Ver mis objetivos", "📊 Ver criterios" (por área) y "🚨 Urgencia". Confirmación antes de guardar; opción de añadir otro comentario.
- **Nota:** casi todos los textos están **hardcodeados en español**, no vía `t()`.
- **Acciones y endpoints:**
  - "Ver mis objetivos" → `GET /api/objetivos?nombre=<persona>`.
  - "Ver criterios" → `GET /api/criterios-evaluacion?grupo=<negocio|palantir|middleoffice>`.
  - "Urgencia" → describe y "Enviar al CA" → `POST /api/urgencia-personal`.
  - Guardar comentario → `POST /api/guardar-evaluacion-personal`.
- **Props / estado:** `token`, `user`, `onComplete`. Estado: `msgs`, `step`, `comentario`, `inputVal`, `urgenciaVal`, `urgenciaDesc`, `loading`.
- **Endpoints:** `/api/objetivos`, `/api/criterios-evaluacion`, `/api/urgencia-personal`, `/api/guardar-evaluacion-personal`.
- **Marcador de captura:**
> 📷 **[Captura pendiente: chat de seguimiento personal con los botones Ver objetivos / Ver criterios / Urgencia]**

---

#### `ChatEvalCA`
- **Qué es / cuándo aparece:** chat de **revisión de advisees por el CA** (privado). [main.jsx:1626](../frontend/src/main.jsx#L1626).
- **Quién lo ve (rol):** Career Advisors.
- **Qué muestra:** chat que lista los advisees pendientes como botones; al elegir uno muestra un resumen de sus evaluaciones y pide una opinión; confirma y guarda; avanza al siguiente advisee hasta completarlos todos.
- **Nota:** textos en español hardcodeados.
- **Acciones y endpoints:**
  - Al montar → `GET /api/mis-advisees` (fallback a `adviseesProp`).
  - Elegir advisee → `GET /api/resumen-evaluaciones-advisee?advisee=...`.
  - Confirmar opinión → `POST /api/notas-ca` con `{ advisee, nota }`.
- **Props / estado:** `token`, `user`, `adviseesProp`, `onComplete`. Estado: `msgs`, `step`, `adviseeActual`, `opinion`, `advisees`, `guardados`, `loading`.
- **Endpoints:** `/api/mis-advisees`, `/api/resumen-evaluaciones-advisee`, `/api/notas-ca`.
- **Marcador de captura:**
> 📷 **[Captura pendiente: chat CA con la lista de advisees como botones y un resumen de evaluaciones]**

---

#### `HistorialEvaluacionesPage`
- **Qué es / cuándo aparece:** tabla con el histórico de evaluaciones de una persona en un proyecto. Ruta `historial-evaluaciones`. [main.jsx:1785](../frontend/src/main.jsx#L1785).
- **Quién lo ve:** quien haya evaluado (desde `EvaluacionesProyectoPage`).
- **Qué muestra:** título con nombre del evaluado y proyecto; tabla con columnas Fecha, Valoración (badge 1–5), Justificación y Relación (superior/igual/inferior).
- **Acciones:** sólo lectura. "Volver" → `onBack`.
- **Props / estado:** `token`, `evaluado`, `evaluador`, `proyecto`, `onBack`. Estado: `historial`, `error`. Helper local `formatFecha`.
- **Endpoints:** `GET /api/historial-evaluaciones?evaluado=...&evaluador=...&proyecto=...`.
- **Marcador de captura:**
> 📷 **[Captura pendiente: tabla de historial de evaluaciones con badges de valoración y relación]**

---

#### `EvaluacionesSlackSection`
- **Qué es / cuándo aparece:** contenedor con pestañas ("Mensuales" y "Personal") que monta el chat correspondiente. Se usa dentro de `EvaluacionesSlackPage`. [main.jsx:1862](../frontend/src/main.jsx#L1862).
- **Quién lo ve:** cualquier empleado.
- **Qué muestra:** dos pestañas laterales con marcadores de estado (✅ completada, ✏️ editable en periodo de gracia, "próximamente"); a la derecha, el chat activo (`ChatEvalProyecto` o `ChatEvalPersonal`) o un placeholder.
- **Comportamiento:** consulta el estado del ciclo y hace *merge* con lo completado en `sessionStorage`; una vez el usuario interactúa, ignora respuestas tardías de la API para no cambiar los ticks a mitad de conversación.
- **Acciones y endpoints:** al montar → `GET /api/estado-ciclo-slack`. Marca `completadas` en `sessionStorage` y notifica con `onCompletada`.
- **Props / estado:** `token`, `user`, `advisees`, `onNavigate`, `onCompletada`. Estado: `estadoCiclo`, `tipoActivo`, `completadas`; ref `interactuoRef`; memo `proyectoEnGracia`.
- **Endpoints:** `/api/estado-ciclo-slack`.
- **Marcador de captura:**
> 📷 **[Captura pendiente: sección con las pestañas Mensuales / Personal y sus ticks de estado]**

---

#### `DashNavItem`
- **Qué es:** ítem de navegación (fila clicable con hover) usado en la columna "To-do" del Dashboard. [main.jsx:1966](../frontend/src/main.jsx#L1966).
- **Props:** `label`, `onClick`, `disabled`. Estado: `hover`.
- **Endpoints:** ninguno.

#### `DashCollapsible`
- **Qué es:** bloque colapsable (título + chevron) usado en la columna "To-see" del Dashboard. [main.jsx:1988](../frontend/src/main.jsx#L1988).
- **Props:** `title`, `open`, `onToggle`, `children`.
- **Endpoints:** ninguno.

> 📷 **[Captura pendiente: ejemplo de bloque colapsable To-see abierto (p. ej. "Mis objetivos")]**

---

#### `Dashboard`
- **Qué es / cuándo aparece:** pantalla principal tras el login (ruta por defecto, `page === null`). [main.jsx:2005](../frontend/src/main.jsx#L2005).
- **Quién lo ve (rol):** todos. Un admin la ve con panel de administración incrustado (salvo en modo "Perfil personal", donde se comporta como un empleado normal — `isAdmin` se anula si `onBackToRoleSelect` está activo).
- **Qué muestra:** navbar con nombre, avatar y logout; nombre del perfil; y una rejilla de tres columnas:
  - **Izquierda — "To-do":** enlaces de acciones según rol/datos: "Activar evaluaciones de proyecto", lista colapsable de "Evaluaciones de proyecto" activas, "Mis advisees", "Gestionar proyectos", y (admin) "Panel de administración".
  - **Centro:** foto de perfil (o iniciales).
  - **Derecha — "To-see":** "Mi rol" (cargo), colapsable "Mis objetivos" y colapsable "Mis informes" (abrir HTML / descargar Word del informe final si hay acceso).
  - **Panel admin embebido** (si admin y sección "admin" activa): selector de persona evaluada, alternancia "Borrador de Claude" / "Final del CA", generación de informe anual (`/api/generar`, `/api/generar-anual`) y acceso al informe final.
  - **Modal de opiniones del CA** cuando `opinionesModal` está activo.
- **Acciones y endpoints (principales):**
  - Carga inicial (varios `apiRequestCached`): `/api/evaluados`, `/api/mis-advisees`, `/api/evaluados-anual` (admin), `/api/acceso-advisees`, `/api/informe-final`, `/api/mi-perfil`, `/api/objetivos`, `/api/evaluaciones-proyecto-activas`, `/api/proyectos-manager`.
  - `generate()` → `POST /api/generar`; `generateAnual()` → `POST /api/generar-anual`; `downloadAnual()` descarga el `.docx`.
  - `loadOpiniones()` → `GET /api/opiniones-ca?advisee=...`.
  - `toggleAcceso()` → `POST /api/acceso-advisees`.
  - `openFile()` abre HTML o descarga Word.
  - Navegación: los ítems del To-do llaman a `onNavigate({ type: ... })`.
- **Props / estado:** `token`, `user`, `onLogout`, `onNavigate`, `onBackToRoleSelect`. Estado muy amplio (evaluados, advisees, perfil, misObjetivos, proyectosActivos, proyectosManager, opinionesModal, adminModo, informes, flags de colapsables, etc.).
- **Endpoints:** todos los listados arriba.
- **Marcador de captura:**
> 📷 **[Captura pendiente: Dashboard completo con las columnas To-do / foto / To-see; y variante de admin con el panel de administración y el modal de opiniones del CA]**

---

#### `SubirInformePage`
- **Qué es / cuándo aparece:** subida del **informe final** (Word) de un advisee. Ruta `subir-informe`. [main.jsx:2448](../frontend/src/main.jsx#L2448).
- **Quién lo ve (rol):** CA, desde la ficha del advisee.
- **Qué muestra:** hero con foto/nombre del advisee; si ya hay versión, botones para abrir/descargar la actual; formulario de subida de fichero `.doc/.docx`.
- **Acciones y endpoints:**
  - Al montar → `GET /api/informe-final?evaluado=...` (versión actual).
  - Subir → `POST /api/subir-informe-final` (multipart `FormData` con `evaluado` y `archivo`).
  - Abrir/descargar → `openFile`.
- **Props / estado:** `token`, `advisee`, `onBack`. Estado: `file`, `status`, `links`, `uploading`, `informeActual`.
- **Endpoints:** `/api/informe-final`, `/api/subir-informe-final`.
- **Marcador de captura:**
> 📷 **[Captura pendiente: página de subida de informe final con el input de fichero y la versión actual]**

---

#### `AdviseesList`
- **Qué es / cuándo aparece:** rejilla con todos los advisees del CA. Ruta `advisees-list`. [main.jsx:2565](../frontend/src/main.jsx#L2565).
- **Quién lo ve (rol):** Career Advisors.
- **Qué muestra:** tarjetas de advisee (foto + nombre).
- **Acciones:** clic en un advisee → `onNavigate({ type: "advisee-detail", advisee, advisees })`. "Volver" → `onBack`.
- **Props:** `token`, `advisees`, `onBack`, `onNavigate` (no tiene estado propio).
- **Endpoints:** ninguno (recibe la lista por props desde el Dashboard).
- **Marcador de captura:**
> 📷 **[Captura pendiente: rejilla de "Mis advisees" con tarjetas de foto+nombre]**

---

#### `AdviseeDetail`
- **Qué es / cuándo aparece:** ficha detallada de un advisee. Ruta `advisee-detail`. [main.jsx:2596](../frontend/src/main.jsx#L2596).
- **Quién lo ve (rol):** Career Advisors.
- **Qué muestra:** foto/nombre del advisee y un panel de acciones con menús anidados:
  - "Editar objetivos" (→ `ObjetivosPage`).
  - "Gestionar informe" → despliega: "Realizar final" (→ "Con Claude" que abre `EvaluacionAnualWizard`, o "Manual" con descargas PDF por fuente), "Subir informe final" (→ `SubirInformePage`) y toggle "Dar/Quitar acceso" al advisee.
  - "Ver información disponible" (descarga PDF completo).
  - **Registro de reuniones / notas del CA:** formulario para añadir nota y su historial (con evaluaciones incluidas plegables).
- **Acciones y endpoints:**
  - Al montar → `GET /api/acceso-advisee-individual?advisee=...` y `GET /api/opiniones-ca?advisee=...`.
  - `toggleAccesoIndividual()` → `POST /api/acceso-advisee-individual`.
  - `descargarBorrador()` → `POST /api/generar` (y descarga `.docx`).
  - `generarOpiniones(formato)` → `POST /api/generar-opiniones-ca` (HTML o PDF).
  - `descargarFuentePdf(endpoint, ...)` → uno de: `/api/generar-opiniones-ca`, `/api/generar-pdf-evals-proyecto`, `/api/generar-pdf-seguimiento`, `/api/generar-pdf-evals-mensuales`, `/api/generar-pdf-completo`.
  - `guardarNota()` → `POST /api/notas-ca`.
- **Props / estado:** `token`, `advisee`, `advisees`, `onBack`, `onNavigate`. Estado abundante de flags de menús desplegados, cargas y errores.
- **Endpoints:** los listados arriba.
- **Marcador de captura:**
> 📷 **[Captura pendiente: ficha de advisee con el panel de acciones (Gestionar informe desplegado) y el registro de notas del CA]**

---

#### `MisProyectosActivosPage`
- **Qué es / cuándo aparece:** gestión de los proyectos donde el usuario es **responsable/manager**. Ruta `mis-proyectos-activos`. [main.jsx:2894](../frontend/src/main.jsx#L2894).
- **Quién lo ve (rol):** responsables de proyecto.
- **Qué muestra:** por cada proyecto, una tarjeta con barra de progreso (miembros completos / total) y una **tabla de miembros** (recibidas, autoevaluación ✓/✗, estado Completa/Pendiente, y ✕ para eliminar). Botón para **añadir** miembro (selector de empleados disponibles).
- **Acciones y endpoints:**
  - `cargarProyectos()` → `GET /api/proyectos-manager`; por cada uno `cargarEstado()` → `GET /api/estado-proyecto?proyecto=...`.
  - Al montar → también `GET /api/todos-empleados`.
  - Añadir/Eliminar miembro → `POST /api/modificar-equipo-proyecto` con `{ accion, proyecto, empleado }`.
- **Props / estado:** `token`, `user`, `onBack`. Estado: `proyectos`, `loading`, `estadoMap`, `todosEmpleados`, `añadirMap`, `añadirValor`, `accionMsg`.
- **Endpoints:** `/api/proyectos-manager`, `/api/estado-proyecto`, `/api/todos-empleados`, `/api/modificar-equipo-proyecto`.
- **Marcador de captura:**
> 📷 **[Captura pendiente: tarjeta de proyecto con barra de progreso, tabla de miembros y el desplegable para añadir miembro]**

---

#### `ActivarEvaluacionesProyectoPage`
- **Qué es / cuándo aparece:** formulario para **crear/activar** una tanda de evaluaciones de un proyecto. Ruta `activar-evaluaciones-proyecto`. [main.jsx:3084](../frontend/src/main.jsx#L3084).
- **Quién lo ve (rol):** responsables de proyecto (no admins).
- **Qué muestra:** campo "Nombre del proyecto" (con formato sugerido `2026_Empresa_NombreProyecto`) y un buscador con lista de empleados seleccionables mediante checkbox; contador de seleccionados; botón de activar. Tras enviar, muestra confirmación con opción de "activar otro" o volver.
- **Acciones y endpoints:**
  - Al montar → `GET /api/todos-empleados`.
  - Activar → `POST /api/activar-evaluaciones-proyecto` con `{ proyecto, empleados }`; llama `onActivado` (que refresca proyectos en el Dashboard).
- **Props / estado:** `token`, `user`, `onBack`, `onActivado`. Estado: `proyecto`, `todosEmpleados`, `seleccionados`, `busqueda`, `loading`, `enviado`, `status`.
- **Endpoints:** `/api/todos-empleados`, `/api/activar-evaluaciones-proyecto`.
- **Marcador de captura:**
> 📷 **[Captura pendiente: formulario de activación con el nombre del proyecto y la lista de empleados con checkboxes]**

---

#### `EvaluacionesProyectoPage`
- **Qué es / cuándo aparece:** listado de evaluaciones que el usuario debe hacer en un proyecto. Ruta `evaluaciones-proyecto`. [main.jsx:3270](../frontend/src/main.jsx#L3270).
- **Quién lo ve:** miembros del proyecto (y el manager, con distinta lista).
- **Qué muestra:** selector de proyecto (si hay varios), barra de progreso global y secciones agrupadas: "Autoevaluación", "Evaluaciones a manager" y "Evaluaciones a miembros". Cada fila indica Pendiente/Completada y, según el caso, botón "Historial".
- **Lógica de negocio:** calcula qué evaluaciones tocan según si la persona es el manager del proyecto o un miembro (`evaluacionesAHacer`). Marca como completadas las que estén en `completedEvals` (memoria de sesión) o en Notion.
- **Acciones y endpoints:**
  - Al cambiar de proyecto → en paralelo `GET /api/equipo-proyecto?proyecto=...` y `GET /api/evaluaciones-proyecto-completadas?proyecto=...`.
  - Clic en una fila pendiente → `onNavigate({ type: "formulario-evaluacion-proyecto", ... })`.
  - "Historial" → `onNavigate({ type: "historial-evaluaciones", ... })`.
- **Props / estado:** `token`, `user`, `proyectos`, `onBack`, `onNavigate`, `completedEvals`, `initialProyecto`. Estado: `proyectoSeleccionado`, `equipo`, `loadingEquipo`, `completadasNotion`; memo `evaluacionesAHacer`.
- **Endpoints:** `/api/equipo-proyecto`, `/api/evaluaciones-proyecto-completadas`.
- **Marcador de captura:**
> 📷 **[Captura pendiente: listado de evaluaciones de proyecto con barra de progreso y las secciones Autoevaluación / a manager / a miembros]**

---

#### `FormularioEvaluacionProyecto`
- **Qué es / cuándo aparece:** formulario para rellenar una evaluación concreta de proyecto. Ruta `formulario-evaluacion-proyecto`. [main.jsx:3420](../frontend/src/main.jsx#L3420).
- **Quién lo ve:** miembros del proyecto.
- **Qué muestra:** hero con proyecto y tipo de evaluación; si procede, selector de persona a evaluar; preguntas renderizadas según tipo: escala 1–5 (radio), radio de 3 opciones (Exceeds/Achieves/Expects more) y texto abierto. Al enviar, pantalla de confirmación con "Nueva evaluación" / "Volver".
- **Tipos de evaluación** (`TIPOS_EVAL_INFO`, [main.jsx:3263](../frontend/src/main.jsx#L3263)): autoevaluación, a miembros del mismo nivel, de miembros a manager (NPS), de manager a miembros.
- **Acciones y endpoints:**
  - Al montar → `GET /api/preguntas-evaluacion-proyecto?tipo=...`; si hay selector, `GET /api/todos-empleados`.
  - Enviar → `POST /api/guardar-evaluacion-proyecto` con `{ proyecto, tipo, evaluado, respuestas }`; llama `onEnviado` (marca la evaluación como completada en `completedEvals`).
- **Props / estado:** `token`, `user`, `proyecto`, `tipo`, `manager`, `evaluadoProp`, `onBack`, `onEnviado`. Estado: `preguntas`, `todosEmpleados`, `evaluado`, `respuestas`, `enviando`, `status`, `enviado`.
- **Endpoints:** `/api/preguntas-evaluacion-proyecto`, `/api/todos-empleados`, `/api/guardar-evaluacion-proyecto`.
- **Marcador de captura:**
> 📷 **[Captura pendiente: formulario de evaluación de proyecto con escala 1–5, radios de 3 opciones y pregunta abierta]**

---

#### `EvaluacionesSlackPage`
- **Qué es / cuándo aparece:** contenedor de página para las evaluaciones "en Slack" (mensual/personal). Ruta `evaluaciones-slack`. [main.jsx:3630](../frontend/src/main.jsx#L3630).
- **Quién lo ve:** cualquier empleado.
- **Qué muestra:** navbar + título "Evaluaciones en Slack" + el componente `EvaluacionesSlackSection`.
- **Acciones:** delega en `EvaluacionesSlackSection`. "Volver" → `onBack`.
- **Props:** `token`, `user`, `advisees`, `onBack`, `onNavigate`, `completadasApp`, `onCompletada`. Sin estado propio.
- **Endpoints:** ninguno directo (los usa la sección interna).
- **Marcador de captura:**
> 📷 **[Captura pendiente: página "Evaluaciones en Slack" con la sección de pestañas embebida]**

---

#### `TopLoadingBar`
- **Qué es / cuándo aparece:** componente **invisible** (`return null`) que renderiza la barra de progreso superior. Montado en la raíz junto a `App`. [main.jsx:3649](../frontend/src/main.jsx#L3649).
- **Quién lo ve:** todos (barra de carga en la parte superior).
- **Qué hace:** se suscribe al store `_loading` y anima variables CSS `--load-progress`/`--load-opacity` que pinta `.nav::after`, avanzando proporcionalmente a las peticiones ya completadas.
- **Props / estado:** ninguno (todo en un `useEffect`).
- **Endpoints:** ninguno.
- **Marcador de captura:**
> 📷 **[Captura pendiente: barra de carga superior en curso durante una petición]**

---

#### `EvaluacionAnualWizard`
- **Qué es / cuándo aparece:** asistente conversacional para la **evaluación anual asistida por IA** de un advisee. Ruta `eval-anual` (se llega desde AdviseeDetail → "Con Claude"). [main.jsx:3700](../frontend/src/main.jsx#L3700).
- **Quién lo ve (rol):** Career Advisors.
- **Qué muestra:** un flujo por pasos (`step`): `loading` → `identidad` (confirmar persona y ver proyectos del año) → `loop` (recorrer **áreas**: se muestra la evidencia que la IA consideró, se conversa con la IA aportando puntos, y se confirma cada área) → `resumen` (generar borrador) → `hecho` (abrir el borrador). Cabecera con año y progreso de áreas confirmadas, y botón "Info completa" (PDF con las 4 fuentes).
- **Acciones y endpoints:**
  - Iniciar → `POST /api/eval-anual/iniciar`.
  - Cargar área → `GET /api/eval-anual/area?evaluado=...&clave=...`.
  - Confirmar identidad → `POST /api/eval-anual/confirmar-identidad`.
  - Enviar puntos a la IA → `POST /api/eval-anual/responder-area`.
  - Confirmar área → `POST /api/eval-anual/confirmar-area`.
  - Finalizar (generar borrador) → `POST /api/eval-anual/finalizar`.
  - "Info completa" → `POST /api/generar-pdf-completo` (descarga PDF).
  - Abrir borrador HTML → abre `htmlUrl` con token en query.
- **Props / estado:** `token`, `advisee`, `onBack`. Estado: `est`, `step`, `error`, `busy`, `secIdx`, `area`, `input`, `evidOpen`, `finUrls`, `descInfo`. Helper interno `shell()` para el layout común.
- **Endpoints:** `/api/eval-anual/iniciar`, `/api/eval-anual/area`, `/api/eval-anual/confirmar-identidad`, `/api/eval-anual/responder-area`, `/api/eval-anual/confirmar-area`, `/api/eval-anual/finalizar`, `/api/generar-pdf-completo`.
- **Marcador de captura:**
> 📷 **[Captura pendiente: wizard de evaluación anual — paso "confirmar identidad", paso "área" con evidencia y conversación con la IA, y paso "borrador generado"]**

---

#### `App` (componente raíz)
- **Qué es:** componente raíz que gestiona sesión, idioma, navegación por estado y renderiza la pantalla adecuada. [main.jsx:3921](../frontend/src/main.jsx#L3921).
- **Qué hace:**
  - Inicializa `token` desde storage y lo valida con `GET /api/me` (fija idioma y usuario, o limpia sesión si falla).
  - Función `navigate(page, adminModeOverride)` para cambiar de pantalla; `backTo(page)` calcula el retorno según `page.from`; `closeLegal()` cierra las páginas legales limpiando el hash; `handleLogout()` cierra sesión.
  - Escucha `hashchange` para las páginas legales.
  - Cadena de guardas + `if (page?.type === ...)` que decide el componente a renderizar (ver **Mapa de navegación**).
- **Props / estado:** sin props. Estado: `token`, `user`, `page`, `adminMode`, `completedEvals`, `slackEvalCompletadas`, `legalDoc`.
- **Endpoints:** `/api/me` (validación de sesión).

---

### Funciones auxiliares y hooks

Funciones **no-componente** definidas en el módulo:

- **`getLegalDoc()`** ([main.jsx:15](../frontend/src/main.jsx#L15)) — devuelve `"privacidad"`/`"terminos"` según el hash, o `null`.
- **`apiUrl(path)`** ([main.jsx:22](../frontend/src/main.jsx#L22)) — concatena `API_BASE` + `path`.
- **Store de carga `_loading` + `subscribeLoading` / `_emitLoading` / `startLoading` / `stopLoading`** ([main.jsx:29-48](../frontend/src/main.jsx#L29)) — contador de peticiones en curso que alimenta `TopLoadingBar`.
- **`apiRequest(path, { token, method, body })`** ([main.jsx:50](../frontend/src/main.jsx#L50)) — helper central de `fetch` (JSON, Bearer, manejo de errores, envuelto en start/stopLoading).
- **Caché en sessionStorage: `_getCached` / `_setCache` / `clearApiCache` / `apiRequestCached`** ([main.jsx:71-107](../frontend/src/main.jsx#L71)) — caché con TTL de 5 min y patrón *stale-while-revalidate* (`onFresh`).
- **`isStrongPassword(password)`** ([main.jsx:109](../frontend/src/main.jsx#L109)) — valida contraseña fuerte (≥8, mayúscula, símbolo).
- **`initials(nombre)`** ([main.jsx:113](../frontend/src/main.jsx#L113)) — iniciales para el avatar.
- **`getResetToken()`** ([main.jsx:118](../frontend/src/main.jsx#L118)) — extrae el token de restablecimiento de query, hash o path.
- **`renderLegalInline(text)`** ([main.jsx:166](../frontend/src/main.jsx#L166)) — parsea negrita `**...**` y enlaces `[texto](#hash)` dentro de textos legales.
- **`renderMd(text)`** ([main.jsx:886](../frontend/src/main.jsx#L886)) — mini-renderer Markdown usado en las burbujas de chat (`*negrita*`, `_cursiva_`, saltos de línea).

Constantes de módulo: `LEGAL_DOCS` ([main.jsx:10](../frontend/src/main.jsx#L10)), `API_BASE` ([main.jsx:20](../frontend/src/main.jsx#L20)), `_CACHE_TTL` ([main.jsx:71](../frontend/src/main.jsx#L71)), `DASH_DIVIDER` ([main.jsx:2003](../frontend/src/main.jsx#L2003)), `TIPOS_EVAL_INFO` ([main.jsx:3263](../frontend/src/main.jsx#L3263)).

**Hooks:** el archivo **no define hooks personalizados** (`useXxx`); todo el estado usa los hooks estándar `useState`, `useEffect`, `useMemo` y `useRef`. Varios componentes definen funciones-helper *internas* (p. ej. `formatFecha` en `HistorialEvaluacionesPage`, `getResumen`/`botSay`/`userSay` en los chats, `openFile`/`downloadAnual` en `Dashboard`, `shell` en `EvaluacionAnualWizard`); estas viven dentro de su componente y se han descrito junto a él.