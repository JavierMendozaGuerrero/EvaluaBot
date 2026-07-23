#!/usr/bin/env bash
set -euo pipefail

# ============================================================
# Deploy template para Google Cloud Run
# ============================================================
# Este archivo SÍ va en git; deploy-gcp.sh NO (está en .gitignore),
# porque cada entorno tiene su proyecto y sus valores.
#
# Uso:
#   cp deploy-gcp-template.sh deploy-gcp.sh
#   # edita la sección Config con tus datos
#   chmod +x deploy-gcp.sh
#
#   # Primera vez, después del setup one-time (ver DEPLOY.md § Cloud Run):
#   ./deploy-gcp.sh --sync-secrets   # sube los tokens del .env al Secret Manager
#   ./deploy-gcp.sh                  # build + deploy
#
#   # Deploys siguientes:
#   ./deploy-gcp.sh
#
# NOTA sobre coste: la app usa Slack Socket Mode (WebSocket persistente) +
# tareas programadas en threads dentro del backend. Por eso Cloud Run se
# despliega con min=max=1 instancias y CPU always allocated (no scale-to-zero).
# Coste orientativo: ~$40/mes en europe-southwest1. Cuando migremos Slack a
# Events API + Cloud Scheduler, esto podrá escalar a cero.
# ============================================================

# ---- Config (AJUSTA ESTOS VALORES) ----
GCP_PROJECT="TU-PROYECTO-GCP"           # p.ej. igeneris-evaluabot
GCP_REGION="europe-southwest1"          # Madrid
SERVICE_NAME="evaluabot"                # nombre del servicio Cloud Run
AR_REPO="apps"                          # repo Artifact Registry
IMAGE_NAME="evaluabot"                  # nombre de la imagen dentro del repo
LOCAL_ENV_FILE=".env"                   # solo para --sync-secrets: lee valores de aquí
ENV_VARS_FILE="env.gcp.yaml"            # env vars NO secretas que ve la app
# ----------------------------------------

# Secretos que se guardan en Secret Manager (nombre = env var name en la app).
# El resto (URLs, IDs de Notion, SMTP host/user…) van en env.gcp.yaml en claro.
SECRETS=(
  "SLACK_BOT_TOKEN"
  "SLACK_APP_TOKEN"
  "NOTION_TOKEN"
  "ANTHROPIC_API_KEY"
  "SMTP_PASSWORD"
)

require_gcloud() {
  command -v gcloud >/dev/null || { echo "gcloud no está instalado. Ver DEPLOY.md § Cloud Run"; exit 1; }
}

sync_secrets() {
  require_gcloud
  [[ -f "${LOCAL_ENV_FILE}" ]] || { echo "No existe ${LOCAL_ENV_FILE}"; exit 1; }

  echo "→ Sincronizando ${#SECRETS[@]} secretos desde ${LOCAL_ENV_FILE} al Secret Manager (${GCP_PROJECT})"
  for name in "${SECRETS[@]}"; do
    # Extrae valor del .env respetando comentarios y espacios; toma la última definición.
    value="$(grep -E "^${name}=" "${LOCAL_ENV_FILE}" | tail -n1 | sed -E "s/^${name}=//; s/^\"(.*)\"$/\1/; s/^'(.*)'\$/\1/")"
    if [[ -z "${value}" ]]; then
      echo "  · ${name}: (vacío en ${LOCAL_ENV_FILE}, se salta)"
      continue
    fi

    if gcloud secrets describe "${name}" --project="${GCP_PROJECT}" >/dev/null 2>&1; then
      echo "  · ${name}: añadiendo nueva versión"
    else
      echo "  · ${name}: creando secreto"
      gcloud secrets create "${name}" --project="${GCP_PROJECT}" --replication-policy=automatic >/dev/null
    fi
    printf "%s" "${value}" | gcloud secrets versions add "${name}" \
      --project="${GCP_PROJECT}" --data-file=- >/dev/null
  done
  echo "✓ Secretos sincronizados"
}

deploy() {
  require_gcloud
  [[ -f "${ENV_VARS_FILE}" ]] || { echo "Falta ${ENV_VARS_FILE} (copia env.gcp.yaml.example y edítalo)"; exit 1; }

  local tag="$(date +%Y%m%d-%H%M%S)"
  local image="${GCP_REGION}-docker.pkg.dev/${GCP_PROJECT}/${AR_REPO}/${IMAGE_NAME}:${tag}"

  echo "→ Build & push con Cloud Build: ${image}"
  gcloud builds submit --project="${GCP_PROJECT}" --tag="${image}" .

  # Monta la lista --set-secrets: SLACK_BOT_TOKEN=SLACK_BOT_TOKEN:latest,...
  local secrets_arg=""
  for name in "${SECRETS[@]}"; do
    secrets_arg+="${name}=${name}:latest,"
  done
  secrets_arg="${secrets_arg%,}"

  echo "→ Desplegando en Cloud Run (${SERVICE_NAME} @ ${GCP_REGION})"
  gcloud run deploy "${SERVICE_NAME}" \
    --project="${GCP_PROJECT}" \
    --region="${GCP_REGION}" \
    --image="${image}" \
    --platform=managed \
    --allow-unauthenticated \
    --port=8000 \
    --cpu=1 --memory=1Gi \
    --min-instances=1 --max-instances=1 \
    --no-cpu-throttling \
    --timeout=3600 \
    --concurrency=80 \
    --env-vars-file="${ENV_VARS_FILE}" \
    --set-secrets="${secrets_arg}"

  local url
  url="$(gcloud run services describe "${SERVICE_NAME}" \
    --project="${GCP_PROJECT}" --region="${GCP_REGION}" --format='value(status.url)')"
  echo ""
  echo "✓ Deploy completado"
  echo "  Servicio: ${url}"
  echo ""
  echo "Si FRONTEND_ORIGIN / APP_PUBLIC_URL en env.gcp.yaml no coinciden con esta URL,"
  echo "actualízalos (o pon tu dominio propio) y vuelve a lanzar ./deploy-gcp.sh"
}

case "${1:-}" in
  --sync-secrets) sync_secrets ;;
  "")             deploy ;;
  *)              echo "Uso: $0 [--sync-secrets]"; exit 1 ;;
esac
