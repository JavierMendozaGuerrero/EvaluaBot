# Despliegue backend/frontend

El proyecto queda separado asi:

- `backend/`: backend Python. Mantiene Slack Socket Mode, Notion, Claude, usuarios, permisos, informes y trayectorias.
- `frontend/`: frontend React/Vite preparado para Vercel.
- `backend/dashboard_web/`: salida generada por el backend. No se sube a GitHub salvo `.gitkeep`.

## Backend

El backend debe ir en un servidor persistente como Render, Railway, Fly.io, Cloud Run o un VPS. No es ideal para Vercel porque necesita quedarse corriendo para Slack y tareas programadas.

Por defecto, al ejecutar:

```bash
python bot.py
```

se levanta la API en:

```text
http://localhost:8000
```

Variables principales del backend:

```text
SLACK_BOT_TOKEN
SLACK_APP_TOKEN
NOTION_TOKEN
NOTION_DATABASE_ID
NOTION_PARENT_PAGE_ID
NOTION_USERS_DATABASE_ID      # opcional, si ya existe la base de usuarios web
NOTION_USERS_DATABASE_NAME    # opcional, por defecto: Usuarios web
ANTHROPIC_API_KEY
APP_MODE
FRONTEND_ORIGIN
```

En produccion, `FRONTEND_ORIGIN` debe ser la URL de Vercel, por ejemplo:

```text
https://evaluabot.vercel.app
```

La web antigua integrada en Python sigue disponible solo si arrancas con:

```powershell
$env:WEB_MODE="legacy"
python bot.py
```

## Frontend local

```bash
cd frontend
npm install
npm run dev
```

El frontend local usa por defecto:

```text
VITE_API_BASE_URL=http://localhost:8000
```

## Frontend en Vercel

En Vercel crea el proyecto apuntando a la carpeta `frontend`.

Configura esta variable:

```text
VITE_API_BASE_URL=https://URL-DE-TU-BACKEND
```

Vercel servira solo la interfaz React. Las llamadas a Notion, Slack y Claude pasan por el backend.
