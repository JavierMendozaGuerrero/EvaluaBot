# Bot de Slack: evaluación periódica + respuestas guardadas en Notion

Este bot:
1. Envía "haz tu evaluación" a un canal de Slack cada 5 minutos.
2. Si alguien responde **en hilo** a ese mensaje, pregunta a quién está
   evaluando y guarda el resultado en una base de datos de Notion específica
   para esa persona. Si no existe, la crea.
3. Expone una web local para generar informes con Claude, descargar un Word
   y crear una trayectoria React navegable por fechas.
4. Añade registro/login: Ana puede ver todo como admin; el resto solo puede
   generar contenidos sobre su propia tabla.

## 1. Configurar la app de Slack

Sigue los mismos pasos que ya hiciste (Socket Mode + tokens), pero añade
estos scopes en **OAuth & Permissions -> Bot Token Scopes**:

- `chat:write`
- `channels:history`
- `users:read`   <- nuevo, necesario para obtener el nombre de quien responde

Si añades un scope nuevo, tienes que volver a pulsar **"Reinstall to Workspace"**
para que el token tenga los permisos actualizados.

## 2. Configurar Notion

1. Crea una integración en https://www.notion.so/my-integrations
2. Copia el "Internal Integration Secret" (empieza por `secret_` o `ntn_`)
3. Crea una base de datos de referencia (vista de tabla) con estas columnas EXACTAS:
   - `Name` (tipo Title, ya viene por defecto)
   - `Persona evaluada` (tipo Text)
   - `Persona que evalua` (tipo Text)
   - `Proyecto` (tipo Text)
   - `Satisfaccion` (tipo Text)
   - `Mejor aspecto` (tipo Text)
   - `Peor aspecto` (tipo Text)
   - `Fecha` (tipo Date)
4. En la página que contiene esa base de datos, pulsa "..." -> Connections -> añade tu integración.
   Esto permite que el bot cree nuevas bases de datos para cada persona evaluada.
5. Copia el ID de la base de datos desde la URL (los 32 caracteres
   alfanuméricos antes de `?v=`)
6. Si la base de datos de referencia no está dentro de una página normal, copia
   también el ID de la página donde quieres crear las bases nuevas y úsalo como
   `NOTION_PARENT_PAGE_ID`.

## 3. Instalar dependencias

```bash
pip install slack_bolt slack_sdk notion-client anthropic python-docx
```

## 4. Variables de entorno (PowerShell)

```powershell
$env:SLACK_BOT_TOKEN="xoxb-..."
$env:SLACK_APP_TOKEN="xapp-..."
$env:NOTION_TOKEN="secret_..."
$env:NOTION_DATABASE_ID="3800a3d98b8a804c97a8fe8667e9940c"
$env:NOTION_PARENT_PAGE_ID="id_de_la_pagina_donde_crear_las_bases"
$env:ANTHROPIC_API_KEY="sk-ant-..."
$env:ADMIN_NAME="Ana"
$env:ADMIN_ACCESS_CODE="una_clave_para_ana"
$env:APP_MODE="prueba"       # prueba: ahora y cada 5 min
# $env:APP_MODE="produccion" # produccion: viernes 10:00 hora Madrid
$env:REVIEW_BEFORE_SEND="true" # true: Ana revisa en la web antes de enviar a Slack
```

## 5. Ejecutar

```bash
python bot.py
```

## Estructura del código

- `bot.py`: punto de entrada.
- `app/config.py`: variables de entorno, preguntas y constantes.
- `app/slack_bot.py`: envío de evaluaciones y conversación en hilos de Slack.
- `app/notion_service.py`: creación, lectura y escritura de bases en Notion.
- `app/web_server.py`: login, registro, informes y trayectoria web.
- `app/reports.py`: generación de informes con Claude, Word, HTML y caché.
- `app/users.py`: registro, login, sesiones y permisos.
- `app/clients.py`: clientes de Slack, Notion y Claude.
- `app/state.py`: estado compartido en memoria.

La web de informes queda disponible en:

```text
http://localhost:8000
```

Desde ahí puedes:

- Elegir una persona evaluada/tabla de Notion.
- Generar un informe con Claude y descargarlo como Word para esa persona.
- Reutilizar el informe anterior si no hay evaluaciones nuevas, evitando llamadas innecesarias a Claude.
- Generar una trayectoria React con botones para pasar de una fecha a otra y ver la evaluación correspondiente de esa persona.
- Registrarte con usuario y contraseña. El usuario determina la tabla que puedes ver.
- Registrar `Ana` introduciendo también `ADMIN_ACCESS_CODE` para activar permisos admin.
- Entrar como `Ana` para generar informes globales o de cualquier persona.
- Entrar como otra persona para generar solo lo que le han evaluado a esa persona.

## 6. Probar

1. En modo `prueba`, el bot envía la primera pregunta al arrancar y luego cada 5 minutos.
2. **Responde en hilo** a ese mensaje (no en el canal directamente)
3. Responde primero el nombre de la persona evaluada
4. Responde el proyecto correspondiente
5. Al final, el bot mostrará un resumen de tus respuestas en el hilo.
6. Responde `sí` para guardar en Notion o `modificar` para cambiar una respuesta concreta.
7. Revisa Notion: debería aparecer o reutilizarse una base llamada
   `Evaluaciones - Nombre de la persona`, con una nueva fila con tu nombre,
   tus respuestas y la fecha

---

### Notas
- Solo se guardan respuestas dadas **en hilo** al mensaje de evaluación.
  Mensajes sueltos en el canal se ignoran.
- El bot solo recuerda los mensajes de evaluación enviados mientras está
  corriendo (si lo reinicias, pierde la memoria de los `ts` anteriores).
- En modo `produccion`, el bot envía la evaluación los viernes a las 10:00 hora de Madrid.
- Si `REVIEW_BEFORE_SEND=true`, el bot crea una evaluación pendiente y Ana debe aprobarla desde la web antes de que se mande a Slack.
