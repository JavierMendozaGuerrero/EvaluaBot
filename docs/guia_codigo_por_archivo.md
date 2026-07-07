# Guía del código — idea general por archivo (EvaluaBot)

> Este documento explica, **a grandes rasgos**, qué hace cada archivo del proyecto y qué papel juega
> en el conjunto. No entra al detalle de cada función (para eso está la carpeta opcional
> `docs/guia_detallada/`). El objetivo es que, leyendo un párrafo por archivo, entiendas el sistema entero.
>
> Guía funcional (qué ve y hace cada usuario): [guia_usuario_completa.md](guia_usuario_completa.md).

---

## Cómo encaja todo (visión de 1 minuto)

EvaluaBot tiene **cuatro piezas** que se comunican:

```
   Empleado
      │  (habla por DM)
      ▼
   SLACK  ──────────►  BACKEND (Python)  ──────────►  NOTION  (la base de datos)
   (bot)               · flujos de evaluación         (empleados, respuestas,
      ▲                · API REST (web)                objetivos, permisos…)
      │                · tareas programadas
   WEB (React) ────────┘        │
   (navegador)                  ▼
                            CLAUDE (IA)  →  informes con citas
                            SMTP (email) →  reset de contraseña
```

- **Slack** es por donde el empleado responde (el bot le pregunta y guarda).
- El **backend Python** es el cerebro: contiene los flujos de evaluación, la API que usa la web,
  y las tareas programadas que lanzan los ciclos.
- **Notion** es la única base de datos: ahí se guarda todo.
- **La web (React)** consulta y gestiona (objetivos, informes, tareas de CA/manager/admin).
- **Claude** redacta borradores de informes; **SMTP** manda los emails de recuperación de contraseña.

**Regla mental para saber dónde se toca cada cosa:**
- ¿Cambio en un flujo de Slack? → `slack_bot.py` / `personal_eval.py` / `ca_reviews.py`.
- ¿Cambio en los datos? → `notion_service.py`.
- ¿Cambio en la web? → `api_server.py` (backend) + `frontend/src/main.jsx` (frontend).
- ¿Cambio en informes/IA? → `reports.py` / `skill_informes_anual.py`.

---

## BACKEND

### Arranque e infraestructura

**`bot.py`** (raíz) — El **punto de entrada** del proyecto. Es un archivo mínimo: solo llama al arranque
real que vive en `backend/main.py`. Se ejecuta con `python bot.py`.

**`backend/main.py`** — El **director de orquesta del arranque**. Levanta a la vez, en hilos separados,
todo lo que el sistema necesita corriendo en paralelo: el envío programado de las evaluaciones, los
recordatorios, el servidor web/API y la conexión permanente con Slack (Socket Mode). Como Slack necesita
esa conexión siempre viva, **el servidor debe estar encendido 24/7**.

**`backend/config.py`** — La **configuración central**. Lee las variables de entorno (tokens de Slack,
Notion, Claude, SMTP…) y define las constantes clave: el modo (`prueba`/`produccion`), la frecuencia en
modo prueba (30 días), el día y hora de envío en producción (**viernes 10:00, hora de Madrid**), el puerto
web (8000) y los nombres de las páginas de Notion. Si cambias aquí el calendario o el modo, cambia el
comportamiento de todo el bot sin tocar más código.

**`backend/clients.py`** — Crea **una sola vez** los clientes de las APIs externas (Slack, Notion, Claude)
para que el resto del proyecto los reutilice. Evita reconectar en cada operación.

**`backend/state.py`** — El **estado en memoria** compartido entre hilos, protegido con un candado
(`threading.RLock`) para que no haya choques cuando varios hilos leen/escriben a la vez. Aquí se recuerda,
por ejemplo, qué mensajes de evaluación están activos. **Ojo:** al ser memoria, si el servidor se reinicia
se pierde (por eso los envíos perdidos no se recuperan).

**`backend/utils.py`** — **Utilidades pequeñas** de uso general, como convertir un nombre en un "slug"
(texto seguro para nombres de archivo, p. ej. *"Alonso Ballesteros" → "alonso_ballesteros"*).

**`backend/i18n.py`** — La **traducción del backend** (español/inglés) para los textos fijos que genera el
servidor (por ejemplo, títulos y prompts de los informes). El contenido escrito en Notion no se traduce.

**`backend/hierarchy.py`** — Decide la **relación jerárquica** entre quien evalúa y quien es evaluado
(superior / igual / inferior). Esto es importante porque las preguntas y el peso del feedback cambian
según si te evalúa tu líder, un compañero de tu nivel o alguien de tu equipo.

### La "base de datos": Notion

**`backend/notion_service.py`** — El archivo **más grande y más importante** del backend (~3.750 líneas).
Es la **capa de base de datos**: absolutamente toda la lectura y escritura en Notion pasa por aquí. Sabe
crear y consultar las bases por empleado (*Evaluaciones - {nombre}*, *Opiniones - {nombre}*,
*Objetivos - {nombre}*), guardar las respuestas de cada ciclo, leer la lista de empleados y sus CAs,
manejar los objetivos, el barbecho, los permisos de acceso a informes y el calendario de evaluaciones.
Si algo "se guarda" o "se lee", el código está aquí. No usa ningún ORM: habla directamente con la API de Notion.

### Flujos de evaluación por Slack

Cada uno de estos archivos maneja **un tipo de evaluación** de principio a fin: cuándo se envía, la
conversación paso a paso con el usuario y el guardado en Notion.

**`backend/slack_bot.py`** (~2.100 líneas) — La **evaluación mensual de proyecto**. Contiene tanto el
*scheduler* (decide cuándo mandar y los recordatorios) como toda la conversación: elegir área
(Negocio/MiddleOffice/Palantir), decir si estás en proyecto o en barbecho, elegir proyecto y compañero,
responder las preguntas (según área y jerarquía), ver el resumen, confirmar o modificar, y evaluar a más
gente o más proyectos. También gestiona la ventana de **modificación de 2 días** y el `SOS` para cancelar.

**`backend/personal_eval.py`** (~1.060 líneas) — El **seguimiento personal** (quincenal). Presenta al
empleado sus "oportunidades" (contar cómo contribuye a la firma, cómo avanza en sus objetivos, señalar
limitaciones frente a los criterios) e incluye el botón **🚨 Urgencia**, que —solo si se confirma— avisa
directamente al CA por Slack. También los botones *Ver mis objetivos* y *Ver criterios*.

**`backend/ca_reviews.py`** (~1.210 líneas) — La **revisión del Career Advisor**. Cada 4 semanas el CA
recibe, advisee por advisee, las evaluaciones que ha recogido esa persona; puede pedir (con consentimiento)
un **resumen por competencias generado por Claude** y luego escribe su propia opinión para reducir el
sesgo del resultado final. Guarda cada opinión en Notion.

**`backend/project_evals.py`** (~1.080 líneas) — Las **evaluaciones estructuradas de proyecto**, que se
hacen en la web (no en Slack). Gestiona los distintos **tipos** (autoevaluación, a compañeros del mismo
nivel, de miembros a manager, de manager a miembros), sus **escalas** (1–5, *Exceeds/Achieves/Expects more*,
preguntas abiertas) y cómo se activan, guardan y consultan. (Ver [Los tipos de evaluación](#los-tipos-de-evaluación-explicados).)
Para **acelerar la carga del dashboard**: las lecturas calientes de Notion (proyectos activos, equipo por
proyecto, proyectos de manager) salen todas de **una sola consulta cacheada** de la tabla de activaciones
(`_leer_activaciones_activas`, TTL 60s), y las evals completadas se cachean por proyecto
(`_leer_completadas_proyecto`); ambas cachés **se invalidan al escribir** (activar/añadir/eliminar miembro,
guardar eval). Además `obtener_progreso_proyectos_empleado` reúne equipo + completadas de todos los
proyectos activos de la persona en **una respuesta** (completadas en paralelo), lo que el frontend pedía
antes con 1 + 2N peticiones en cascada — servido por el endpoint `/api/proyectos-progreso`.

### Informes e IA (Claude)

**`backend/reports.py`** — Genera el **informe mensual** de una persona con Claude, a partir de sus
evaluaciones. Produce versión HTML (web) y Word, y **cachea** el resultado: si los datos no han cambiado,
reutiliza el informe en vez de volver a llamar (y pagar) a la IA.

**`backend/skill_informes_anual.py`** (~1.450 líneas) — El **generador del informe anual**, la joya del
sistema. Recopila **cinco fuentes** de datos (evaluaciones mensuales `[E]`, opiniones del CA `[O]`,
evaluaciones de proyecto `[P]`, seguimiento personal `[S]`, barbecho `[B]`), se las pasa a Claude, y este
redacta el informe por competencias **obligando a que cada frase lleve una cita** a su fuente. Después el
propio código **valida** las citas (borra lo que no esté respaldado) y un **verificador** avisa de frases
dudosas. El resultado es un Word con la plantilla oficial de Igeneris y un HTML con la evidencia clicable.
Está pensado para que la IA **no pueda inventar** y para que el CA tenga siempre la última palabra.
Para **reducir coste de API**, el `system` (instrucciones + formato, estático por cargo/idioma) se envía con
**prompt caching** (`cache_control: ephemeral`): entre informes generados en ráfaga solo se paga una vez ese
prefijo, con **la misma calidad** (mismo modelo y mismo input); si el caching no está disponible reintenta sin
él. La generación completa, además, ya está **cacheada por huella** de los datos (no vuelve a llamar a Claude si
nada cambió). Documentación ampliada: [skills/eval-informes-rrhh.md](../skills/eval-informes-rrhh.md).

**`backend/eval_anual_sesion.py`** — La **sesión interactiva de la evaluación anual asistida**. Guarda el
progreso del CA mientras revisa a un advisee área por área (identidad → evidencia por bloques → su
valoración se bloquea → ve la de Claude → decide mía/IA/fusión). Persiste en un JSON local
`sesion_anual_{slug}.json` para poder pausar y reanudar. Plan de diseño en [plan-preguntas-previas-ca.md](plan-preguntas-previas-ca.md).

**`backend/skill_opiniones_ca.py`** — Genera **PDF/resumen de las opiniones del CA** sobre un advisee (una
de las descargas de evidencia del panel del CA).

**`backend/skill_resumen_evaluacion.py`** — Produce un **resumen de una evaluación** (síntesis breve del feedback).
El flujo del CA (`llamar_claude` en `ca_reviews.py`) **memoriza el resumen en la conversación**: si el CA vuelve
atrás y reenvía el mismo texto en bruto, reutiliza el resumen ya generado en vez de re-llamar (y pagar) a Claude.

**`backend/skill_pdfs_fuentes.py`** — Genera los **PDFs de fuentes/evidencia** (evaluaciones mensuales, de
proyecto, seguimiento personal…) que el CA puede descargar para redactar el informe manualmente.

### Web / API y usuarios

**`backend/api_server.py`** (~1.040 líneas) — La **API REST** (puerto 8000) que consume la web React. No
usa Flask: es un servidor HTTP básico que enruta a mano. Expone unos ~50 endpoints para todo lo que hace la
web: login/registro, guardar evaluaciones, activar proyectos, panel del CA, generar y servir informes,
objetivos, evaluación anual asistida, etc. Cada endpoint **comprueba la sesión y el rol** (empleado / CA /
admin) antes de responder.

**`backend/web_server.py`** — La **web antigua** integrada en Python (se usa solo si arrancas con
`WEB_MODE=legacy`). Es un remanente anterior al frontend React; en el día a día no se usa.

**`backend/users.py`** (~450 líneas) — La **gestión de usuarios y seguridad**: registro, login, **sesiones**,
verificación por email y **reset de contraseña** (enlace por SMTP, válido 30 min). Guarda las contraseñas
cifradas con PBKDF2-SHA256 + salt (nunca en texto plano) y determina el **rol** de cada usuario (empleado,
CA, admin).

**`backend/create_users_from_employees.py`** — Script de utilidad para **crear las cuentas web** a partir de
la *Lista de empleados* de Notion (alta masiva inicial).

### Scripts y pruebas

**`migration_notion.py`** (raíz) — Script de **migración de la estructura de Notion** (reorganizó las
páginas a la nueva estructura TO-DO / TO-SEE). Se ejecuta puntualmente, no forma parte del funcionamiento diario.

**`backend/test_smtp.py`** — Comprueba que el **envío de emails** (SMTP) funciona.
**`test_preguntas.py`** (raíz) — Prueba de las **preguntas** de evaluación.

---

## FRONTEND

**`frontend/src/main.jsx`** (~4.100 líneas) — **Toda la aplicación web** en un solo archivo React (SPA sin
router; la navegación va por *hash* en la URL). Contiene unas **28 pantallas/componentes**: login y
registro (`AuthScreen`), selección de rol del admin, el `Dashboard` (columnas *To-do* / foto / *To-see*),
las páginas de objetivos, advisees y su detalle, subir informe final, el **asistente de evaluación anual**
(`EvaluacionAnualWizard`), la activación y gestión de proyectos, los formularios de evaluación de proyecto,
el historial, y los **chats** que replican Slack en la web (`ChatEvalProyecto`, `ChatEvalPersonal`,
`ChatEvalCA`). Gestiona el estado global, la sesión (token) y las llamadas a la API. *(Nota: no existe un
componente `EvalAnualLogPage`, aunque se mencionó en algún borrador.)*

**`frontend/src/i18n.js`** — La **traducción de la web** (español/inglés). Es un catálogo de textos
(botones, títulos, mensajes de los chats) con su versión en cada idioma. El idioma sale del perfil del
usuario en Notion; por defecto español. El contenido de Notion no se traduce.

**`frontend/src/styles/` y `styles.css`** — Los **estilos** siguiendo el sistema de diseño de Igeneris
(lienzo blanco, texto negro, un único acento rojo, sin sombras, esquinas redondeadas). Definen tanto los
componentes reutilizables como los ajustes por pantalla.

**`frontend/src/legal/`** — Los textos legales (`privacidad.md`, `terminos.md`) que la web muestra en el pie.

---

## Los tipos de evaluación (explicados)

El sistema maneja **cinco clases de evaluación**. Es la parte funcional más importante, así que aquí van una a una.

### 1. Evaluación mensual de proyecto
- **Quién:** todos los empleados. **Dónde:** Slack (o el chat equivalente en la web). **Cada:** 4 semanas.
- **Qué es:** valoras a los compañeros con los que trabajas en un proyecto. En Negocio, por ejemplo, das una
  nota **del 1 al 4** a su contribución al proyecto + un **ejemplo concreto**. Las preguntas cambian según
  el área (Negocio/MiddleOffice/Palantir) y según la relación jerárquica.
- **Privacidad:** solo la ve el **CA de la persona evaluada**.
- **Detalle especial:** si estás **en barbecho** (sin proyecto), en vez de evaluar registras qué labores
  haces (eso luego cuenta como *contribution to the firm*). Puedes **modificar durante 2 días**.
- **Código:** `slack_bot.py`.

### 2. Seguimiento personal
- **Quién:** todos los empleados. **Dónde:** Slack (o web). **Cada:** 2 semanas.
- **Qué es:** un espacio para que **tú** cuentes cómo contribuyes a la firma, cómo avanzas en tus objetivos
  y qué limitaciones tienes frente a los criterios. Incluye el botón **🚨 Urgencia** para avisar a tu CA.
- **Privacidad:** solo lo ve tu **CA**.
- **Código:** `personal_eval.py`.

### 3. Revisión del Career Advisor
- **Quién:** solo quien es **CA** de alguien. **Dónde:** Slack (o web). **Cada:** 4 semanas.
- **Qué es:** el CA revisa, advisee por advisee, el feedback recogido; opcionalmente pide un **resumen por
  IA (Claude)** y escribe su **propia opinión** para reducir el sesgo del resultado final.
- **Código:** `ca_reviews.py`.

### 4. Evaluación estructurada de proyecto
- **Quién:** los miembros de un proyecto que un **manager active**. **Dónde:** solo web. **Cuándo:** cuando el manager la activa.
- **Subtipos:**
  - **Autoevaluación** — valoras tu propio desempeño.
  - **A tus compañeros del mismo nivel** — valoras a iguales.
  - **De miembros a manager** — valoras a tu responsable.
  - **De manager a miembros** — el manager valora a su equipo.
- **Escalas:** **1–5** (1 = no cumple … 5 = cumple del todo), o **Exceeds / Achieves / Expects more**, o
  **preguntas abiertas** de texto.
- **Código:** `project_evals.py`.

### 5. Evaluación anual
- **Quién:** la construye el **CA** para cada advisee, una vez al año. **Dónde:** web.
- **Qué es:** el documento final que reúne todo el año. El CA puede hacerlo **manualmente** (descargando los
  PDF de evidencia) o **asistido por Claude**: la IA redacta un borrador **con citas trazables** a partir de
  las 5 fuentes, y el CA lo revisa área por área (comprometiéndose con su valoración **antes** de ver la de
  la IA, para no dejarse anclar), decide y publica. El empleado solo ve el informe cuando el CA le **da acceso**.
- **Código:** `skill_informes_anual.py` + `eval_anual_sesion.py` (+ `reports.py` para el mensual).

---

## Referencia profunda (opcional)

Si algún día quieres el detalle **función por función** de algunos módulos, en la carpeta
`docs/guia_detallada/` hay fichas generadas para: `notion_service`, `slack_bot`, `ca_reviews`,
`api_server`, informes y skills auxiliares, infraestructura y el frontend. No es necesario para entender el
sistema —para eso basta este documento— pero está ahí como material de consulta técnica.

---

> **Mantenimiento:** si mueves responsabilidades entre archivos, actualiza el párrafo correspondiente.
> Descripciones verificadas contra el código a fecha de redacción.