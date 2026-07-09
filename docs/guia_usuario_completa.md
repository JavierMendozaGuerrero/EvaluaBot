# Guía de usuario completa — EvaluaBot

> **Documento maestro.** Reúne en un solo sitio todo lo que hace EvaluaBot, con un apartado
> para cada tipo de usuario (empleado, Career Advisor, manager de proyecto, administrador) y
> un apartado técnico para quien lo opera.
>
> Las cadencias y comportamientos están verificados contra el código; cada sección enlaza a los
> archivos que la implementan para facilitar el mantenimiento. Si el código cambia, actualiza aquí.
>
> Guía breve previa (solo bot de Slack): [guia_usuario.md](guia_usuario.md). Este documento la amplía y la incluye.

---

## Índice

1. [¿Qué es EvaluaBot?](#1-qué-es-evaluabot)
2. [Conceptos clave y glosario](#2-conceptos-clave-y-glosario)
3. [Los roles y quién ve qué](#3-los-roles-y-quién-ve-qué)
4. [El calendario de evaluaciones](#4-el-calendario-de-evaluaciones)
5. [Guía para el EMPLEADO](#5-guía-para-el-empleado)
6. [Guía para el CAREER ADVISOR (CA)](#6-guía-para-el-career-advisor-ca)
7. [Guía para el MANAGER DE PROYECTO](#7-guía-para-el-manager-de-proyecto)
8. [Guía para el ADMINISTRADOR](#8-guía-para-el-administrador)
9. [Los informes explicados](#9-los-informes-explicados)
10. [Privacidad y permisos](#10-privacidad-y-permisos)
11. [Idiomas (español / inglés)](#11-idiomas-español--inglés)
12. [Guía técnica y de operación](#12-guía-técnica-y-de-operación)
13. [Preguntas frecuentes (FAQ)](#13-preguntas-frecuentes-faq)
14. [Apéndices](#14-apéndices)

---

## 1. ¿Qué es EvaluaBot?

EvaluaBot es la **plataforma interna de evaluación de personas de Igeneris**. Automatiza la recogida
periódica de feedback entre compañeros y su síntesis en informes, combinando cuatro piezas:

- **Slack** — el bot habla con cada empleado por mensaje directo (DM) y recoge las evaluaciones.
- **Notion** — es la "base de datos": ahí se guarda absolutamente todo (empleados, respuestas, objetivos, permisos).
- **Claude (IA de Anthropic)** — redacta borradores de informes a partir de los datos, siempre con citas trazables.
- **Web (React)** — el panel donde consultar objetivos, informes, y donde CAs, managers y admins gestionan todo.

La idea de fondo: **el empleado casi no tiene que entrar a ninguna web**. El bot le avisa por Slack
cuando toca evaluar, hace las preguntas una a una y guarda las respuestas. La web existe sobre todo
para consultar resultados y para las tareas de CA / manager / admin (y como alternativa a Slack).

**Cuatro flujos de evaluación conviven en el sistema:**

| Flujo | Quién participa | Dónde | Cada cuánto |
|-------|-----------------|-------|-------------|
| **Evaluación mensual de proyecto** | Todos los empleados | Slack (o web) | Cada 4 semanas |
| **Seguimiento personal** | Todos los empleados | Slack (o web) | Cada 2 semanas |
| **Revisión de Career Advisor** | Solo quien es CA de alguien | Slack (o web) | Cada 4 semanas |
| **Evaluación estructurada de proyecto** | Miembros de un proyecto activado | Solo web | Cuando el manager la activa |

Y por encima de todo, una vez al año: la **evaluación anual**, que un CA construye ayudándose de un
borrador que genera Claude.

---

## 2. Conceptos clave y glosario

- **Empleado / evaluado**: cualquier persona de la empresa dada de alta en la *Lista de empleados* de Notion.
- **Evaluador**: quien da feedback (todos son evaluadores de sus compañeros de proyecto).
- **Career Advisor (CA)**: mentor asignado a uno o varios empleados (sus *advisees*). Consolida el feedback y hace el informe anual.
- **Advisee**: empleado asignado a un CA.
- **Manager de proyecto**: quien "activa" las evaluaciones estructuradas de un proyecto y gestiona su equipo.
- **Administrador**: acceso total a todos los datos e informes (hoy, principalmente **Ana Hernanz**).
- **Ciclo**: cada tanda periódica de evaluaciones (mensual, personal, CA).
- **Hilo (thread)**: en Slack todo se responde **dentro del hilo** de la notificación, no en el canal.
- **Barbecho**: periodo en el que un empleado no está en proyecto; registra qué labores hace.
- **Grace period (2 días)**: ventana tras guardar una evaluación en la que aún puedes modificarla.
- **Borrador vs informe final**: Claude genera un **borrador**; el CA lo revisa, lo edita y publica la **versión final**.
- **Citas `[E#] [O#] [P#] [S#] [B#]`**: etiquetas que enlazan cada frase del informe a su evidencia original.
- **Modo prueba / producción**: ver [§12](#12-guía-técnica-y-de-operación). En prueba el bot manda a una sola persona y repite rápido; en producción sigue el calendario real.

---

## 3. Los roles y quién ve qué

Un mismo empleado puede acumular varios roles (p. ej. ser empleado, CA de dos personas y manager de un proyecto).
El sistema muestra en la web solo las secciones que corresponden a sus roles.

| Rol | Qué puede hacer | Qué ve de otros |
|-----|-----------------|-----------------|
| **Empleado** | Responder sus evaluaciones, ver sus objetivos y sus informes finales (si el CA le dio acceso) | **Solo lo suyo** |
| **Career Advisor** | Todo lo de empleado + revisar a sus advisees, poner objetivos, notas de reuniones, generar/subir informe anual, dar acceso al informe | Las evaluaciones **de sus advisees** |
| **Manager de proyecto** | Todo lo de empleado + activar evaluaciones de su proyecto y gestionar su equipo | El **estado de evaluaciones de su proyecto** (quién ha evaluado a quién, sin ver el contenido privado) |
| **Administrador** | Acceso completo: cualquier empleado, cualquier informe | **Todos los datos** |

> **Principio de privacidad**: las evaluaciones mensuales y el seguimiento personal de un empleado
> solo los ve **su CA** (y el admin). Un compañero nunca ve lo que otro escribió.

---

## 4. El calendario de evaluaciones

### 4.1. Cadencias (modo producción)

| Ciclo | Para quién | Frecuencia | Recordatorio si sigue pendiente | Fecha límite |
|-------|------------|-----------|--------------------------------|--------------|
| **Seguimiento personal** | Todos | Cada **2 semanas** | Semanal | Hasta la siguiente notificación |
| **Evaluación mensual de proyecto** | Todos | Cada **4 semanas** | Semanal | Hasta la siguiente notificación |
| **Revisión de CA** | Solo CAs | Cada **4 semanas** | Semanal | Hasta la siguiente notificación |

**Día y hora de envío en producción**: **viernes a las 10:00 (hora de Madrid)**
(`DIA_ENVIO_PRODUCCION=4`, `HORA_ENVIO_PRODUCCION=10:00` en [config.py](../backend/config.py)).

### 4.2. De dónde salen las fechas

La **fecha de inicio de cada ciclo no está en el código**: se configura en Notion, en la página
**«Calendario evaluaciones»**. El bot repite cada N semanas a partir de esa fecha, de modo que un
administrador puede reprogramar el calendario **sin tocar código**. Si no hay fecha configurada, el
bot espera y reintenta cada hora.

### 4.3. Recordatorios y caducidad

- Mientras una evaluación siga **pendiente**, el bot manda un recordatorio **cada semana** en el mismo hilo.
- En cuanto respondes y se guarda en Notion, dejan de llegarte recordatorios de ese ciclo.
- Cuando llega la **siguiente** notificación del ciclo, **ya no** puedes contestar la anterior.

### 4.4. Bug conocido (pendiente de corregir)

El comportamiento *deseado* es que la revisión de CA empiece **una semana después** de la mensual.
Hoy el código lanza ambos ciclos desde la **misma** fecha de Notion, así que **arrancan a la vez**.
Ver [slack_bot.py](../backend/slack_bot.py) y [ca_reviews.py](../backend/ca_reviews.py).

### 4.5. Modo prueba vs producción (resumen)

- **Modo prueba** (valor por defecto): el bot manda los ciclos **al arrancar** y luego cada **30 días**
  (`INTERVALO_PRUEBA_DIAS=30`), **solo** al usuario de prueba (`SLACK_TEST_USER_ID`), ignorando el calendario de Notion.
- **Modo producción**: sigue el calendario de Notion y envía a **todos** los empleados el viernes 10:00.

---

## 5. Guía para el EMPLEADO

Como empleado interactúas con EvaluaBot de dos formas equivalentes: **por Slack** (lo habitual) o
**por la web** (misma pregunta, mismo guardado). Elige la que prefieras.

### 5.1. Reglas de oro en Slack

- Responde **siempre dentro del hilo** de la notificación, nunca en el canal.
- Escribe **`SOS`** en cualquier momento para **cancelar** la evaluación en curso (podrás reempezar escribiendo cualquier mensaje).
- El bot **no es conversacional**: hace preguntas concretas y registra respuestas simples.
- Muchas preguntas traen **botones** y un **ejemplo** («Ver ejemplo») para orientarte.

### 5.2. Evaluación mensual de proyecto (Slack)

**Cuándo**: cada 4 semanas recibes un DM:

> 📍 *Tienes una evaluación mensual pendiente.*
> _Esta evaluación es totalmente privada, solo podrá verla el CA de la persona evaluada._
> _Si en algún momento quieres cancelar, escribe SOS en el hilo._
> 👉 *¿Quieres ver un ejemplo antes de empezar?* [✅ Sí] [❌ No]

Pulsa **✅ Sí** para ver el ejemplo en el propio hilo y empezar, o **❌ No** para
empezar directamente (no hace falta escribir ningún mensaje para arrancar).

**Pasos:**

1. **¿A qué área perteneces?** → *Negocio*, *MiddleOffice* o *Palantir* (botones).
2. Si es *Negocio* o *Palantir*: **¿estás en proyecto o en barbecho?**
   - **🏗️ En proyecto** → sigues con la evaluación normal.
   - **⏸️ En barbecho** → el bot te pregunta *«¿Qué labores estás realizando?»*; escribes tu respuesta,
     confirmas (**✅ Entregar** / **✏️ Modificar**) y termina. (El barbecho se usa luego como *Contribution to the firm*.)
3. **Escribe el nombre de un proyecto** en el que trabajas (podrás evaluar el resto después).
4. **Escribe el nombre de un miembro** de ese equipo. Si te equivocas de nombre, el bot te **sugiere** parecidos.
5. **Preguntas de evaluación** (dependen del área y de la relación jerárquica). En Negocio, por ejemplo:
   - **Del 1 al 4**, ¿cómo valoras su contribución al buen avance del proyecto? (botones 1–4).
   - **Un ejemplo concreto** que justifique tu valoración (texto libre).
6. **Resumen y confirmación**: el bot muestra tus respuestas y pregunta si las guardas.
   - **✅ Sí, guardar** → se guarda en Notion.
   - **✏️ Modificar** → eliges qué campo cambiar por número y lo reescribes.
7. **¿Más gente / más proyectos?** El bot te ofrece evaluar a otros miembros y otros proyectos.
   Cuando terminas: *«Perfecto, muchas gracias por tu tiempo ❤️. Ya puedes salir del hilo 👋»*.

**Modificar después (2 días)**: durante 2 días tras guardar, aparece **✏️ Modificar respuestas** para
cambiar cualquier evaluación reciente. Pasado el plazo: *«El plazo de modificación de 2 días ha expirado.»*

### 5.3. Seguimiento personal (Slack)

**Cuándo**: cada 2 semanas recibes un DM: *📝 «Tienes opción de seguimiento personal pendiente»* (privado, solo lo ve tu CA).

Es tu **oportunidad para**:

1. Explicar cómo estás ayudando en **Contribution to the firm**.
2. Contar cómo te acercas a tus objetivos → botón **📋 Ver mis objetivos**.
3. Señalar limitaciones respecto a los criterios de evaluación → botón **📊 Ver criterios** (por área y nivel).
4. Pedir ayuda o avisar de una dificultad → botón **🚨 Urgencia**.

**Sobre la 🚨 Urgencia** (importante): describes el problema en una frase, el bot te muestra un
**resumen para confirmar** (**✅ Enviar al CA** / **✏️ Modificar**) y **solo tras confirmar** le llega a tu CA por Slack:

> 🚨 *Urgencia de {tu nombre}* — *Descripción:* … — *Por favor, contacta con él/ella lo antes posible.*

> ⚠️ Si **no** pulsas el botón de urgencia, el problema **no** se notifica a nadie automáticamente:
> solo queda registrado en tu respuesta.

Después escribes tu comentario, confirmas (**✅ Sí, guardar** / **✏️ Modificar**), y puedes añadir más comentarios o terminar.

### 5.4. Contestar desde la web (alternativa a Slack)

En la web hay una sección **Evaluaciones** con dos pestañas — **Evaluación mensual** y **Evaluación
personal** — con un **chat idéntico** al de Slack:

> «Contestar aquí es exactamente igual que contestar en Slack. Tus respuestas se guardan en el mismo sitio y en el mismo formato.»

Cada pestaña indica su estado: ✅ **Completada** (ya la hiciste este ciclo), ✏️ **Editable** (dentro
de los 2 días de margen) o **Próx.** (aún no disponible).

### 5.5. Tu Dashboard en la web

Al entrar (ver login en [§5.7](#57-acceso-a-la-web-login-y-cuenta)) verás un panel de tres columnas:

- **Izquierda — «To-do»**: tus acciones pendientes según tus roles (activar evaluaciones de proyecto,
  evaluaciones por proyecto, *Mis advisees* si eres CA, *Gestionar mis proyectos* si eres manager, *Panel admin* si eres admin).
- **Centro**: tu foto (o iniciales) y tu nombre.
- **Derecha — «To-see»**:
  - **Mi puesto** — tu cargo.
  - **Mis objetivos** — desplegable con tus objetivos y KPIs.
  - **Mis informes** — si tu CA te ha dado acceso: **Abrir en web** o **Descargar Word** de tu informe final.

### 5.6. Otras páginas útiles para ti

- **Mis objetivos**: lista completa con fecha, CA, tipo, título, KPIs y descripción.
- **Historial de evaluaciones**: tabla con Fecha · Valoración · Justificación · Relación (superior / igual / inferior).
- **Evaluaciones por proyecto**: si eres miembro de un proyecto con evaluaciones activadas (ver [§7](#7-guía-para-el-manager-de-proyecto)).

### 5.7. Acceso a la web (login y cuenta)

- **Iniciar sesión**: usuario o email + contraseña, con opción **Recuérdame**.
- **Crear cuenta**: usuario + contraseña (mín. 8 caracteres, una mayúscula y un carácter especial; validación en vivo).
- **Verificación por email**: por seguridad se envía un **código de 6 dígitos** que caduca en **10 minutos**.
- **Olvidé mi contraseña**: introduces tu email corporativo y recibes un **enlace de restablecimiento** (válido 30 min).
- La contraseña se guarda cifrada (PBKDF2-SHA256 con salt), nunca en texto plano.

---

## 6. Guía para el CAREER ADVISOR (CA)

Como CA, además de todo lo de empleado, tienes la responsabilidad de **consolidar el feedback de tus
advisees** y construir su **evaluación anual**.

### 6.1. Revisión de CA (Slack) — cada 4 semanas

Recibes un DM: *📋 «CA: Tienes evaluación de advisees pendiente»* (privado, solo lo ves tú).

**Pasos:**

1. **Elige un advisee** (botones con sus nombres, o **❌ Terminar**).
2. El bot te muestra **las evaluaciones que ha recibido** desde tu última revisión (con fecha, evaluador, proyecto, nota y ejemplo).
   Si no hay novedades: *«no hay evaluaciones nuevas desde tu última revisión»* y vuelve al paso 1.
3. **Resumen con Claude (opcional)**: *«¿Quieres un resumen estructurado por competencias generado por Claude?»* (**Sí** / **No**).
   *(Se pide moderar el uso.)* Si dices que sí, Claude te da un resumen por competencias.
4. **Tu opinión**: escribes tu valoración (texto libre). Sirve para **reducir el sesgo** del resultado final.
5. **Confirmas** (**✅ Sí, guardar** / **✏️ Modificar**) y pasas al siguiente advisee.
   Al acabar: *«Ya has opinado sobre todos tus advisees. ¡Perfecto, gracias por tu tiempo! 🎉»*.

> El mismo flujo existe en la web (chat de **Revisión CA**), idéntico a Slack.

### 6.2. Mis advisees (web)

Desde el Dashboard → **Mis advisees**: una cuadrícula con la foto y el nombre de cada advisee. Al abrir uno entras en su **detalle**.

### 6.3. Detalle de un advisee (web)

Desde aquí gestionas todo lo relativo a esa persona:

- **Editar objetivos** — crea/edita/elimina objetivos (título obligatorio, tipo, KPIs, descripción). Hay historial por año y mes.
- **Registro de reuniones / Comentarios** — apuntas notas de vuestras reuniones; quedan con fecha en un historial.
- **Gestionar informe** (desplegable):
  - **Realizar Informe final**:
    - **Con ayuda de Claude** → abre el **asistente de evaluación anual** (ver [§6.4](#64-evaluación-anual-asistida-con-claude-web)).
    - **Manualmente** → descargas los PDF de evidencia (opiniones, evaluaciones de proyecto, seguimiento personal, evaluaciones mensuales) para redactarlo tú.
  - **Subir informe final** → subes el Word (.docx) con tu versión definitiva. Se guarda en Notion y se conservan las 2 versiones más recientes.
  - **Dar acceso a su informe / Revocar** → controla si el advisee puede ver su informe final publicado. **Por defecto no lo ve**: tú decides cuándo.
- **Ver información disponible** — descarga un PDF con toda la información recopilada de esa persona.

### 6.4. Evaluación anual asistida con Claude (web)

Es un **asistente paso a paso** pensado para que tú, el CA, **pienses de forma crítica antes de que la
IA te ancle el juicio**, manteniendo siempre el control humano. Fases:

1. **Confirmar identidad** — el asistente muestra a quién vas a evaluar y sus proyectos del año; confirmas.
2. **Lectura de evidencia por bloques** — la evidencia en bruto se revela **poco a poco** (por periodos cronológicos),
   para digerirla y captar la **evolución** del año (febrero ≠ noviembre), sin sobrecargarte.
3. **Bucle por cada dimensión** (gestión de proyecto, calidad técnica, trabajo en equipo, comunicación,
   relación con cliente, liderazgo si aplica, y contribution to the firm):
   - Ves los **criterios** del área.
   - **Escribes tu valoración** y la **guardas → se bloquea**.
   - **Solo entonces** se revela lo que redactó **Claude** para esa dimensión (con sus citas).
   - Si coincidís, avanzas; si **divergís**, decides: **la mía**, **la de Claude** o **fusión editada**
     (puedes pedir *«Sugerir fusión con IA»*). Todo queda en un **log de decisiones** (lo veis CA y admin, no el advisee).
4. **Objetivos del año siguiente** y **resumen final**.
5. **Generar borrador** → obtienes el Word para rellenar los huecos (notas, retribución) y subirlo como informe final.

> Puedes **guardar a medias y reanudar** más tarde. Publicar exige completar el bucle.
> Ver plan detallado en [plan-preguntas-previas-ca.md](plan-preguntas-previas-ca.md).

### 6.5. Publicar y compartir el informe

1. Generas el **borrador** (Claude) o lo redactas manualmente.
2. Lo revisas y editas (Claude deja en blanco notas, retribución, promoción… para que las completes tú).
3. **Subes la versión final** (.docx).
4. **Das acceso** al advisee cuando lo consideres: entonces (y solo entonces) él podrá verlo/descargarlo desde su Dashboard.

---

## 7. Guía para el MANAGER DE PROYECTO

Las **evaluaciones estructuradas de proyecto** son distintas de la mensual: usan escalas fijas y se hacen
**íntegramente en la web**. Las **activa un manager** para su equipo.

### 7.1. Activar evaluaciones de un proyecto

Dashboard → **Activar evaluaciones de proyecto**:

1. Indicas el **nombre del proyecto** (convención `AÑO_EMPRESA_NOMBRE`).
2. Marcas con checkboxes a los **miembros del equipo** (con buscador y contador de seleccionados).
3. **Activar evaluaciones** → a cada persona le llega una **notificación por Slack**:
   *«📋 Evaluaciones de proyecto activas para el proyecto {nombre}. Recuerda completarlas en la web.»*

### 7.2. Gestionar mis proyectos en activo

Dashboard → **Gestionar mis proyectos en activo**. Por cada proyecto ves una tarjeta con:

- Progreso: *«X de Y evaluaciones completadas»* + barra visual.
- Tabla de miembros: **Recibidas** (N/total) · **Autoevaluación** (✓/✗) · **Estado** (Completo/Pendiente,
  con tooltip de quién falta) · botón **×** para quitar a alguien.
- **+ Añadir miembro** (desplegable de empleados disponibles).

### 7.3. Los tipos de evaluación de proyecto y sus escalas

En la página **Evaluaciones por proyecto**, con barra de progreso *«X de Y completadas · Z%»*:

| Grupo | Tipo | Qué es |
|-------|------|--------|
| **Autoevaluación** | `autoevaluacion` | Valoras tu propio desempeño |
| **A tu manager** | `miembros_a_manager` | Valoras a tu responsable |
| **A tus compañeros / a tu equipo** | `mismos_miembros` / `manager_a_miembros` | Valoras a iguales o a subordinados |

**Tipos de pregunta** en el formulario:

- **Escala 1–5** (1 = carece de cumplimiento … 5 = cumple totalmente).
- **Tres opciones**: *Exceeds* (supera) / *Achieves* (cumple) / *Expects more* (necesita mejorar).
- **Abierta**: texto libre.

Al enviar: *«Evaluación guardada correctamente en Notion»*. Cada evaluado tiene su **Historial** (fecha, valoración, justificación, relación).

---

## 8. Guía para el ADMINISTRADOR

El administrador (hoy, principalmente **Ana Hernanz**) tiene acceso total.

### 8.1. Elegir cómo entrar

Al iniciar sesión, un admin ve una pantalla de bienvenida para elegir:

- **Administrador** — consulta evaluaciones e informes de **cualquier** empleado.
- **Perfil personal** — entra como un empleado normal (para hacer sus propias evaluaciones).

### 8.2. Panel de administrador

- **Buscador de empleados** por nombre + cuadrícula con fotos.
- Al abrir una persona: su ficha con **Ver informe final** (HTML) y **Descargar Word**.

### 8.3. Generación de informes

Desde la gestión de evaluaciones el admin puede:

- **Borrador de Claude**: elige a la persona y pulsa **Generar informe anual**; cuando está listo,
  **Abrir web** o **Descargar informe anual** (.docx).
- **Versión final CA**: consulta el informe final que subió el CA.

### 8.4. Tareas de administración de datos

Recuerda que **todo se administra en Notion** (no hay panel de administración de base de datos aparte):

- Alta/baja de **empleados** y sus **CAs** → *Lista de empleados*.
- **Calendario** de ciclos → página *«Calendario evaluaciones»*.
- **Preguntas** de los chatbots y **criterios** de evaluación → páginas correspondientes bajo *TO-DO*.
- **Usuarios web** → base *Usuarios Web*.

---

## 9. Los informes explicados

Hay dos tipos de informe generados por IA. Ambos **cachean** el resultado: si los datos de Notion no
han cambiado (misma huella SHA-256), se reutiliza lo ya generado **sin volver a llamar a Claude**.

### 9.1. Informe mensual ([reports.py](../backend/reports.py))

Análisis de las evaluaciones mensuales de una persona. Genera **HTML** (web) y **Word** con: resumen
ejecutivo, métricas, fortalezas, riesgos/áreas de mejora, recomendaciones y conclusión. Bilingüe (ES/EN).

### 9.2. Informe anual ([skill_informes_anual.py](../backend/skill_informes_anual.py))

Es el documento central de la evaluación anual. Reproduce la **plantilla oficial «EVALUACIÓN ANUAL» de
Igeneris** en Word, y una versión HTML con evidencia clicable.

**Las 5 fuentes de datos** (cada una con su prefijo de cita):

| Fuente | Cita | Qué es |
|--------|------|--------|
| Opiniones del CA | `[O#]` | Notas del CA + resúmenes del chatbot |
| Evaluaciones mensuales | `[E#]` | Con jerarquía líder / equipo / sin nivel |
| Evaluaciones de proyecto | `[P#]` | Todas las recibidas |
| Seguimiento personal | `[S#]` | Comentarios personales |
| Barbecho | `[B#]` | Labores sin proyecto (→ contribution to the firm) |

**Dimensiones** (siempre): gestión del proyecto, calidad técnica, trabajo en equipo, comunicación,
relación con el cliente. **Liderazgo** (desarrollo de talento, motivación, referente) se añade solo para
**Sr Associate / Manager / Director**. Más: *contribution to the firm* y *resultado global*.

**Trazabilidad (anti-invención)** — garantías estructurales, no solo confianza en el modelo:

1. **Citas obligatorias**: cada frase termina con la(s) etiqueta(s) de su fuente; `temperature=0`.
2. **Validación por código**: un bullet **sin cita válida se elimina** automáticamente.
3. **Pasada de verificación**: una 2ª llamada audita si la cita *realmente* respalda la frase y **avisa** (no borra) → el CA decide.
4. **Evidencia offline**: como el CA/advisee no entra a Notion, cada cita enlaza a un **anexo «Fuentes»** dentro del propio informe.
5. **Panel de revisión** (solo en el borrador HTML): lista avisos y bullets descartados antes de publicar.

**Evolución temporal**: el contexto va en orden cronológico con su mes; Claude describe la **trayectoria,
no la media** (más peso a lo reciente, cita ambos momentos cuando algo mejora o empeora).

### 9.3. Borrador → final

- `informe_anual_{slug}.*` = **borrador** (lo ven solo CA y admin).
- El CA lo edita y **sube** la versión final; el advisee la ve **solo cuando el CA activa el acceso**.

---

## 10. Privacidad y permisos

| Rol | Qué ve |
|-----|--------|
| **Empleado** | Solo su chat con el bot, sus respuestas, sus objetivos y su informe (si el CA lo publicó y le dio acceso) |
| **Career Advisor** | Las evaluaciones **de sus advisees** + urgencias que le manden + el log de decisiones de la eval anual |
| **Manager de proyecto** | El **estado** de evaluaciones de su proyecto (quién ha completado), no el contenido privado |
| **Administrador** | **Todos** los datos en Notion e informes |

Puntos clave:

- Las evaluaciones mensuales y el seguimiento personal **solo los ve el CA** de la persona evaluada.
- El acceso del advisee a su propio informe está **cerrado por defecto**; lo abre el CA cuando quiere.
- Toda la persistencia pasa por [notion_service.py](../backend/notion_service.py), que actúa como base de datos.
- Contraseñas cifradas (PBKDF2-SHA256 + salt). Sesiones con token; reset por email con enlace de 30 min.

---

## 11. Idiomas (español / inglés)

La web es **bilingüe (ES/EN)**. El idioma sale del campo **«Idioma»** del usuario en Notion (`/api/me`);
por defecto **español**. Se traducen los **textos fijos de la interfaz** (botones, títulos, mensajes del chat),
tanto en la web ([i18n.js](../frontend/src/i18n.js)) como en el backend ([i18n.py](../backend/i18n.py)).

> El **contenido escrito en Notion** (preguntas, objetivos, comentarios) **no se traduce**: se muestra tal como se guardó.

---

## 12. Guía técnica y de operación

### 12.1. Arquitectura

- **Backend Python** (Slack Bolt en **Socket Mode** + servidor web + tareas programadas). Punto de entrada: `python bot.py` → [backend/main.py](../backend/main.py), que lanza varios hilos: envíos, recordatorios, servidor web y la conexión de Slack.
- **API REST** en el puerto **8000** ([api_server.py](../backend/api_server.py), sobre `BaseHTTPRequestHandler`, sin Flask).
- **Frontend React 19 + Vite** ([frontend/src/main.jsx](../frontend/src/main.jsx)), SPA de un solo archivo, navegación por hash. Preparado para Vercel.
- **Notion** como única base de datos, vía [notion_service.py](../backend/notion_service.py).
- **Claude** (`claude-sonnet-4-6`) para informes.
- **SMTP** para emails de reset de contraseña.

**Archivos clave del backend:**

| Archivo | Qué cubre |
|---------|-----------|
| [main.py](../backend/main.py) | Arranque; lanza los hilos de envío, recordatorios, web y Slack |
| [config.py](../backend/config.py) | Modo, cadencias, día/hora de envío, nombres de páginas de Notion, variables de entorno |
| [slack_bot.py](../backend/slack_bot.py) | Evaluación mensual de proyecto (Slack) + scheduler de envíos/recordatorios |
| [personal_eval.py](../backend/personal_eval.py) | Seguimiento personal + botón 🚨 Urgencia |
| [ca_reviews.py](../backend/ca_reviews.py) | Revisión de CA (incluye resumen IA con consentimiento) |
| [project_evals.py](../backend/project_evals.py) | Evaluaciones estructuradas de proyecto |
| [reports.py](../backend/reports.py) | Informe mensual (Claude) + caché |
| [skill_informes_anual.py](../backend/skill_informes_anual.py) | Informe anual con competencias, citas y verificación |
| [eval_anual_sesion.py](../backend/eval_anual_sesion.py) | Sesión interactiva de evaluación anual asistida |
| [users.py](../backend/users.py) | Auth PBKDF2, sesiones, reset por email, roles |
| [notion_service.py](../backend/notion_service.py) | Toda la persistencia en Notion |

### 12.2. Variables de entorno

Requeridas: `SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN`, `NOTION_TOKEN`, `NOTION_DATABASE_ID`, `NOTION_PARENT_PAGE_ID`, `ANTHROPIC_API_KEY`.

Importantes: `APP_MODE` (`prueba`/`produccion`), `SLACK_TEST_USER_ID` (solo prueba), `FRONTEND_ORIGIN`,
`APP_PUBLIC_URL`, `PUERTO_WEB` (8000), y las de **SMTP** (`SMTP_HOST/PORT/USER/PASSWORD/FROM/USE_TLS`).
Frontend: `VITE_API_BASE_URL`. Plantilla completa en [.env.example](../.env.example).

### 12.3. Modo prueba vs producción

- **`prueba`** (por defecto): envía **al arrancar** y cada **30 días**, **solo** a `SLACK_TEST_USER_ID`, ignorando el calendario de Notion.
- **`produccion`**: sigue el **calendario de Notion**, envía a **todos** el **viernes 10:00 (Madrid)**, con las cadencias 4/2 semanas por ciclo.

### 12.4. Estructura de Notion (nueva)

Bajo `NOTION_PARENT_PAGE_ID`, dos contenedores de nivel 1:

- **TO-DO** → *Datos a Monitorizar* (Lista de empleados, Usuarios Web), *Preguntas Chatbot*, *Datos opcionalmente modificables*.
- **TO-SEE** → *Resultados Evaluaciones* (Mensuales, CA, Barbecho), *Activaciones de permisos*.

Por empleado se crean bases como *Evaluaciones - {nombre}*, *Opiniones - {nombre}* y *Objetivos - {nombre}*.
Los nombres de página son configurables por variables de entorno (ver [config.py](../backend/config.py)).

### 12.5. Requisitos operativos

- Slack **Socket Mode** mantiene una conexión WebSocket permanente: **el servidor debe estar siempre encendido**.
- Si se apaga, el bot deja de enviar y responder. Los envíos perdidos **no se recuperan**: se reanuda en la siguiente fecha del calendario.
- Instalar dependencias: `pip install -r requirements.txt`. El backend va en un servidor persistente (Render, Railway, Fly.io, Cloud Run, VPS); **no** es ideal para Vercel.

### 12.6. Despliegue

- **Backend** en servidor persistente; configura `FRONTEND_ORIGIN` con la URL de Vercel en producción.
- **Frontend** en Vercel apuntando a la carpeta `frontend`, con `VITE_API_BASE_URL=https://TU-BACKEND`.
- Web local del backend en `http://localhost:8000`. Detalles en [DEPLOY.md](../DEPLOY.md).

### 12.7. Mapa de endpoints REST (resumen)

Todos bajo el puerto 8000. Protegidos por sesión salvo los públicos. Categorías principales
(lista completa en [api_server.py](../backend/api_server.py)):

- **Auth**: `/api/register`, `/api/login`, `/api/password-reset/{request,confirm}`, `/api/me`, `/api/health`.
- **Evaluación mensual/personal**: `/api/guardar-evaluacion-slack`, `/api/actualizar-evaluacion-slack`, `/api/guardar-evaluacion-personal`, `/api/urgencia-personal`, `/api/estado-ciclo-slack`, `/api/buscar-empleado-slack`.
- **Proyecto (manager)**: `/api/proyectos-manager`, `/api/estado-proyecto`, `/api/activar-evaluaciones-proyecto`, `/api/modificar-equipo-proyecto`, `/api/equipo-proyecto`, `/api/preguntas-evaluacion-proyecto`, `/api/guardar-evaluacion-proyecto`, `/api/evaluaciones-proyecto-{activas,completadas}`.
- **CA**: `/api/mis-advisees`, `/api/opiniones-ca`, `/api/notas-ca`, `/api/resumen-evaluaciones-advisee`, `/api/acceso-advisee{s,-individual}`.
- **Informes/documentos**: `/api/generar`, `/api/generar-anual`, `/api/generar-pdf-*`, `/api/trayectoria`, `/api/informe-final`, `/api/subir-informe-final`, `/api/files/{archivo}`.
- **Evaluación anual asistida**: `/api/eval-anual/{iniciar,confirmar-identidad,responder-area,confirmar-area,finalizar,estado,area}`.
- **Objetivos/perfiles**: `/api/objetivos` (GET/POST/DELETE), `/api/mi-perfil`, `/api/perfil-empleado`, `/api/criterios-evaluacion`, `/api/historial-evaluaciones`.

---

## 13. Preguntas frecuentes (FAQ)

**No me llega la evaluación por Slack.** En producción llegan los viernes a las 10:00 (Madrid) según el
calendario de Notion. Si el servidor estuvo apagado, el envío perdido no se recupera: espera a la
siguiente fecha. Verifica también que estás en la *Lista de empleados* de Notion.

**Me equivoqué al responder.** Tienes **2 días** para modificar tras guardar (botón *✏️ Modificar respuestas*
en Slack o estado *Editable* en la web). Pasado ese plazo, no se puede.

**Escribí un problema pero mi CA no se ha enterado.** El comentario solo se notifica si pulsaste el botón
**🚨 Urgencia** y **confirmaste** el envío. Si no, quedó solo registrado.

**¿Quién ve lo que escribo?** Tus evaluaciones mensuales y tu seguimiento personal **solo** los ve tu CA (y el admin). Nunca tus compañeros.

**No veo mi informe anual.** Tu CA tiene que **subir la versión final** y **darte acceso**. Hasta entonces no aparece.

**¿Puedo contestar sin usar Slack?** Sí: en la web, sección *Evaluaciones*. Es idéntico y se guarda en el mismo sitio.

**Olvidé mi contraseña.** Usa *Olvidé mi contraseña* con tu email corporativo; el enlace caduca en 30 minutos.

**¿El informe se inventa cosas?** No: cada frase debe llevar cita a una fuente real; las frases sin cita
válida se eliminan por código y un verificador avisa de las dudosas. La última palabra es siempre del CA.

**Cancelé sin querer con SOS.** Escribe cualquier mensaje en el mismo hilo para volver a empezar.

---

## 14. Apéndices

### 14.1. Escalas usadas

- **Evaluación mensual (Negocio)**: contribución al proyecto **1–4** + ejemplo (texto).
- **Evaluación estructurada de proyecto**: **1–5** (1 carece … 5 cumple), o **Exceeds / Achieves / Expects more**, o texto libre.
- **Relación jerárquica** (en historiales): superior / igual / inferior.

### 14.2. Criterios de evaluación (DTI)

Claude calibra el feedback según el **cargo** (lo positivo para un Analyst es lo mínimo esperado para un
Manager). Los criterios se **cargan en vivo desde Notion** por dimensión y cargo (Analyst, Associate,
Associate Sr, Manager, Director), y varían por grupo (Negocio / MiddleOffice / Palantir). Los criterios
**calibran** pero **no son citables**: las citas siempre apuntan a evaluaciones u opiniones. Referencia
completa (fallback del grupo Negocio) en [skills/eval-informes-rrhh.md](../skills/eval-informes-rrhh.md).

### 14.3. Documentos relacionados

- [guia_usuario.md](guia_usuario.md) — guía breve original centrada en el bot de Slack.
- [plan-preguntas-previas-ca.md](plan-preguntas-previas-ca.md) — diseño de la evaluación anual asistida.
- [README.md](../README.md) — puesta en marcha y configuración inicial.
- [DEPLOY.md](../DEPLOY.md) — despliegue backend/frontend.
- [skills/eval-informes-rrhh.md](../skills/eval-informes-rrhh.md) — detalle técnico del generador de informes anuales.
- [.env.example](../.env.example) — plantilla de variables de entorno.

---

> **Mantenimiento**: cuando cambie un flujo, actualiza la sección correspondiente y su enlace al archivo.
> Las cadencias, escalas y textos aquí descritos se verificaron contra el código; trátalos como
> observaciones a fecha de redacción y confírmalos si el código evoluciona.