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

## Cloud Run (GCP)

El `Dockerfile` sirve backend + React juntos y lee `PORT`, asi que la imagen se despliega tal cual en Cloud Run. El script `deploy-gcp-template.sh` automatiza build y deploy (equivalente a `deploy-template.sh` para el NAS).

**Restriccion importante:** Slack Socket Mode mantiene un WebSocket persistente y las tareas programadas (`ciclo_recordatorios_proyecto`, `enviar_evaluaciones_programadas`) corren en threads dentro del backend. Por eso el servicio se despliega con `min=max=1` y `--no-cpu-throttling` (no escala a cero). Coste orientativo: ~$40/mes en `europe-southwest1`. Cuando migremos a Slack Events API + Cloud Scheduler podra escalar a cero.

### Setup one-time

1. Instalar el SDK: <https://cloud.google.com/sdk/docs/install> (macOS: `brew install --cask google-cloud-sdk`).
2. Autenticarse y elegir proyecto:

   ```bash
   gcloud auth login
   gcloud auth application-default login
   gcloud projects create igeneris-evaluabot --name="Evaluabot"   # o usa uno existente
   gcloud config set project igeneris-evaluabot
   gcloud billing projects link igeneris-evaluabot --billing-account=XXXX-XXXX-XXXX
   ```

3. Habilitar APIs y crear el repo de imagenes:

   ```bash
   gcloud services enable run.googleapis.com artifactregistry.googleapis.com \
     cloudbuild.googleapis.com secretmanager.googleapis.com

   gcloud artifacts repositories create apps \
     --repository-format=docker --location=europe-southwest1
   ```

### Deploy con el script

```bash
cp deploy-gcp-template.sh deploy-gcp.sh
cp env.gcp.yaml.example env.gcp.yaml
# edita deploy-gcp.sh (GCP_PROJECT) y env.gcp.yaml (URLs, IDs, SMTP)
chmod +x deploy-gcp.sh

./deploy-gcp.sh --sync-secrets   # sube tokens del .env local al Secret Manager
./deploy-gcp.sh                  # build con Cloud Build + deploy a Cloud Run
```

El script imprime la URL del servicio al terminar. La primera vez actualiza `FRONTEND_ORIGIN` y `APP_PUBLIC_URL` en `env.gcp.yaml` con esa URL (o con tu dominio propio) y vuelve a lanzar `./deploy-gcp.sh`.

### Secretos vs env vars

- **Secret Manager** (subidos por `--sync-secrets`): `SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN`, `NOTION_TOKEN`, `ANTHROPIC_API_KEY`, `SMTP_PASSWORD`.
- **`env.gcp.yaml`** (en claro): URLs, IDs de Notion, SMTP host/user, `APP_MODE`, flags. No metas tokens aqui.
- `deploy-gcp.sh` y `env.gcp.yaml` estan en `.gitignore`. Los `.example`/`-template.sh` si van a git.

### Logs y rollback

```bash
gcloud run services logs tail evaluabot --region=europe-southwest1
gcloud run revisions list --service=evaluabot --region=europe-southwest1
gcloud run services update-traffic evaluabot --region=europe-southwest1 \
  --to-revisions=evaluabot-00003-abc=100
```
