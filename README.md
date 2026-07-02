# Bot de Slack: evaluación periódica + respuestas guardadas en Notion

Este bot:
1. Envía "haz tu evaluación" a un canal de Slack cada 5 minutos.
2. Si alguien responde **en hilo** a ese mensaje, pregunta primero el proyecto
   y después el miembro evaluado. Guarda el resultado en una base de datos de
   Notion específica para esa persona. Si no existe, la crea.
3. Expone una web local para generar informes con Claude, descargar un Word
   y crear una trayectoria React navegable por fechas.
4. Anade registro/login: cada usuario puede generar contenidos sobre su propia tabla.

## 1. Configurar la app de Slack

Sigue los mismos pasos que ya hiciste (Socket Mode + tokens), pero añade
estos scopes en **OAuth & Permissions -> Bot Token Scopes**:

- `chat:write`
- `channels:history`
- `users:read`
- `lists:write`   <- solo si activas `SLACK_LISTAS_PENDIENTES_HABILITADO`, requiere workspace de Slack en plan de pago
- `lists:read`    <- ídem, opcional, solo para depurar/consultar la lista

Si añades un scope nuevo, tienes que volver a pulsar **"Reinstall to Workspace"**
para que el token tenga los permisos actualizados.

La lista de "Evaluaciones pendientes" (Slack Lists) está desactivada por
defecto (`SLACK_LISTAS_PENDIENTES_HABILITADO=false` en `.env`) porque requiere
plan de pago. Actívala solo cuando el bot esté en el workspace definitivo de
la empresa (que sí es de pago) — el resto del bot funciona igual con esto
desactivado.

## 2. Configurar Notion

1. Crea una integración en https://www.notion.so/my-integrations
2. Copia el "Internal Integration Secret" (empieza por `secret_` o `ntn_`)
3. Crea una base de datos de referencia (vista de tabla) con estas columnas EXACTAS:
   - `Name` (tipo Title, ya viene por defecto; se usa solo como titulo tecnico)
   - `Evaluador` (tipo Text)
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
$env:NOTION_EMPLOYEES_DATABASE_ID="id_de_la_pagina_evaluaciones_o_listas_de_datos"
$env:NOTION_DATA_LISTS_PAGE_NAME="Listas de datos"
$env:NOTION_EMPLOYEES_DATABASE_NAME="Lista de empleados"
$env:NOTION_USERS_DATABASE_NAME="Usuarios web"
$env:NOTION_PARENT_PAGE_ID="id_de_la_pagina_donde_crear_las_bases"
$env:ANTHROPIC_API_KEY="sk-ant-..."
$env:APP_MODE="prueba"       # prueba: ahora y cada 5 min
# $env:APP_MODE="produccion" # produccion: viernes 10:00 hora Madrid
```

## 5. Ejecutar

```bash
python bot.py
```

## Estructura del código

- `bot.py`: punto de entrada.
- `backend/config.py`: variables de entorno, preguntas y constantes.
- `backend/slack_bot.py`: envío de evaluaciones y conversación en hilos de Slack.
- `backend/notion_service.py`: creación, lectura y escritura de bases en Notion.
- `backend/api_server.py`: API JSON para el frontend React.
- `backend/web_server.py`: web antigua integrada en Python (`WEB_MODE=legacy`).
- `backend/reports.py`: generación de informes con Claude, Word, HTML y caché.
- `backend/users.py`: registro, login, sesiones y permisos.
- `backend/clients.py`: clientes de Slack, Notion y Claude.
- `backend/state.py`: estado compartido en memoria.
- `frontend/`: interfaz React preparada para Vercel.

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
- Los usuarios web se guardan en una base de Notion llamada `Usuarios web`
  (o la que configures con `NOTION_USERS_DATABASE_NAME`). Las contraseñas se
  guardan como `salt` + `password_hash`, no en texto plano.
- Entrar como otra persona para generar solo lo que le han evaluado a esa persona.

## 6. Probar

1. En modo `prueba`, el bot envía la primera pregunta al arrancar y luego cada 5 minutos.
2. **Responde en hilo** a ese mensaje (no en el canal directamente)
3. Responde primero el proyecto correspondiente
4. Responde el nombre del miembro evaluado
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

