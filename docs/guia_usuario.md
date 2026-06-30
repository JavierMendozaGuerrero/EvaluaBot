# Guía de usuario — EvaluaBot

> Documento vivo. Las cadencias y comportamientos descritos están verificados contra el código.
> Cada apartado enlaza a los archivos que lo implementan para facilitar el mantenimiento.

---

## 1. Bot de Slack

### 1.1. Qué es

Es un bot automático que vive en el workspace de Slack de Igeneris. Cada empleado tiene un
chat privado (DM) con él, y a través de ese chat el bot gestiona los ciclos de evaluación
**sin que el empleado tenga que entrar a ninguna web**.

El bot no es conversacional ni "inteligente": solo manda notificaciones cuando toca evaluar
y registra respuestas simples. Siempre se responde **dentro del hilo** de la notificación,
no en el canal principal.

### 1.2. Qué hace (resumen)

| Ciclo | Para quién | Cada cuánto | Recordatorio | Hasta cuándo se puede responder |
|-------|------------|-------------|--------------|--------------------------------|
| **Seguimiento personal** | Todos los empleados | Cada **2 semanas** | Sí, cada **semana** mientras siga pendiente | Hasta la siguiente notificación |
| **Evaluación de proyecto (mensual)** | Todos los empleados | Cada **4 semanas** | Sí, cada **semana** mientras siga pendiente | Hasta la siguiente notificación |
| **Revisión de Career Advisor (CA)** | Solo empleados que son CA de alguien | Cada **4 semanas**, una semana después de la mensual | Sí, cada **semana** mientras siga pendiente | Hasta la siguiente notificación |

> **Nota sobre las fechas**: La fecha de inicio de cada ciclo **no está fijada en el código**.
> Se configura en Notion (página *«Calendario evaluaciones»*). El bot repite cada N semanas
> a partir de esa fecha. Esto permite a un administrador cambiar el calendario sin tocar código.
> Si no hay fecha configurada, el bot espera y reintenta cada hora.

> **Modo prueba vs producción**: Todo lo anterior aplica en **modo producción**. En modo prueba
> (el valor por defecto), el bot envía los tres ciclos al arrancar y luego cada 30 días, ignorando
> el calendario de Notion. Ver `APP_MODE` en [config.py](../backend/config.py).

### 1.3. Flujo de cada ciclo (lo que ve el empleado)

#### 1.3.1. Seguimiento personal (cada 2 semanas)

Es una pregunta abierta donde el empleado puede comentar **cualquier cosa que quiera que su
CA tenga en cuenta** en las evaluaciones finales: dificultades para acercarse a sus objetivos,
avances, o aportaciones relacionadas con *Contribution to the firm*.

Además incluye un botón **🚨 Urgencia** para avisar directamente a su CA:

1. El empleado pulsa **🚨 Urgencia** y escribe una descripción del problema.
2. El bot le muestra un **resumen para que confirme** antes de enviar nada (puede confirmar o modificar el texto).
3. Solo tras confirmar, el mensaje llega al CA en su propio chat con el bot.

> Importante: si el empleado **no** pulsa el botón de urgencia, el problema **no** se notifica
> automáticamente; solo queda registrado en su respuesta.

#### 1.3.2. Evaluación de proyecto (cada 4 semanas)

El empleado puede evaluar a los miembros con los que trabaja en cada uno de sus proyectos.
El bot hace las preguntas **una a una** en el hilo y, al terminar, muestra un **resumen** para
que el empleado confirme antes de guardar en Notion (puede confirmar o modificar una respuesta
concreta).

#### 1.3.3. Revisión de Career Advisor (cada 4 semanas, una semana después)

Los empleados que son CA de otra persona reciben una evaluación extra en la que pueden:

- Leer las evaluaciones que su *advisee* ha recibido.
- Opcionalmente, recibir un **resumen generado por IA (Claude)** además del dato en bruto.
  El resumen requiere que el CA dé su **consentimiento** (botón de permiso) antes de generarse.
- Dar su propia opinión, para **reducir el sesgo** del resultado final.

> ⚠️ **Pendiente de corregir en código (bug conocido)**: el comportamiento *deseado* es que la
> revisión de CA empiece **una semana después** de la evaluación mensual. Hoy el código lanza
> ambos ciclos desde la **misma** fecha de Notion (clave `proyecto_ca`, cada 4 semanas), por lo
> que arrancan a la vez. Ver [slack_bot.py:114](../backend/slack_bot.py#L114) y
> [ca_reviews.py:1031](../backend/ca_reviews.py#L1031).

### 1.4. Recordatorios y caducidad

- Mientras una evaluación siga **pendiente**, el bot envía un recordatorio **cada semana**.
  En cuanto el empleado la responde y se guarda en Notion, deja de recibir recordatorios de ese ciclo.
- Cuando llega la **siguiente** notificación de un ciclo, ya **no** se pueden contestar las anteriores.

### 1.5. Recogida de datos

Las respuestas se guardan en **Notion**, al que solo tienen acceso los administradores.
Actualmente la única administradora es **Ana Hernanz**. Toda la persistencia (lectura y escritura
en Notion) pasa por [notion_service.py](../backend/notion_service.py), que actúa como "base de datos".

### 1.6. Privacidad — quién ve qué

| Rol | Qué ve |
|-----|--------|
| Empleado | Solo su propio chat con el bot y sus propias respuestas |
| Career Advisor | Las evaluaciones de su *advisee* (y, si lo activa, el resumen IA) + urgencias que le manden |
| Administrador (Ana Hernanz) | Todos los datos en Notion |

### 1.7. Archivos que lo implementan

| Archivo | Qué cubre |
|---------|-----------|
| [backend/main.py](../backend/main.py) | Arranque: lanza los hilos de envío + recordatorios + servidor web + Slack Socket Mode |
| [backend/config.py](../backend/config.py) | Cadencias, modo (`APP_MODE`), día/hora de respaldo y nombres de páginas de Notion |
| [backend/slack_bot.py](../backend/slack_bot.py) | Flujo conversacional pregunta-a-pregunta de evaluación de proyecto; lanza el scheduler de envíos y recordatorios de proyecto |
| [backend/project_evals.py](../backend/project_evals.py) | Evaluaciones **estructuradas** por proyecto: autoevaluación, a mismos miembros, a manager y de manager a miembros |
| [backend/ca_reviews.py](../backend/ca_reviews.py) | Revisión del Career Advisor sobre su *advisee* (incluye resumen IA con consentimiento) |
| [backend/personal_eval.py](../backend/personal_eval.py) | Seguimiento personal quincenal + botón 🚨 Urgencia |
| [backend/notion_service.py](../backend/notion_service.py) | Toda la persistencia en Notion (la "base de datos") |

### 1.8. Tecnología y requisitos operativos

- Usa **Slack Bolt en Socket Mode**: el servidor Python mantiene una conexión WebSocket
  permanente con Slack. No hay webhooks.
- **El servidor debe estar siempre encendido** para que el bot funcione.
- Si el servidor se apaga, el bot deja de responder y de enviar. Los envíos perdidos **no se
  recuperan retroactivamente**: el bot se reanuda en la siguiente fecha calculada a partir del
  calendario de Notion (ver [siguiente_envio_calendario](../backend/notion_service.py#L3060)).
