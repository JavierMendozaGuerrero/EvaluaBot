#!/usr/bin/env bash
set -euo pipefail

# ============================================================
# Deploy a la VM de Compute Engine (evaluabot-vm)
# ============================================================
# Sustituye al antiguo deploy-gcp.sh (Cloud Run, ya retirado).
#
# Uso:  bash deploy-vm.sh          # build + deploy + verificacion
#
# Que hace:
#   1. Construye la imagen con Cloud Build y la sube a Artifact Registry.
#   2. Por SSH (tunel IAP, la oficina bloquea el 22 directo): actualiza
#      /opt/evaluabot/image.ref, hace pull y reinicia los contenedores
#      (app + caddy) con /opt/evaluabot/start.sh.
#   3. Comprueba que el bot arranca y que la web responde por HTTPS.
#
# NOTA: cada deploy REINICIA el bot -> las evaluaciones a medias se
# pierden (estado en memoria). Desplegar en horas de poca actividad.
#
# Infra (creada 2026-07-23):
#   VM:      evaluabot-vm (e2-small, Debian 12) @ europe-southwest1-a
#   IP fija: 34.175.215.232 (evaluabot-ip)  ->  evaluabot.igeneris.com (A, Nominalia)
#   App:     Docker evaluabot-app (env /opt/evaluabot/app.env, secretos de
#            Secret Manager) + evaluabot-caddy (HTTPS Let's Encrypt)
# ============================================================

GCP_PROJECT="igeneris-evaluabot"
GCP_ZONE="europe-southwest1-a"
VM_NAME="evaluabot-vm"
AR_IMAGE_BASE="europe-southwest1-docker.pkg.dev/${GCP_PROJECT}/apps/evaluabot"
DOMAIN="evaluabot.igeneris.com"

command -v gcloud >/dev/null || { echo "gcloud no esta instalado"; exit 1; }

TAG="vm-$(date +%Y%m%d-%H%M%S)"
IMAGE="${AR_IMAGE_BASE}:${TAG}"

echo "==> 1/3 Build & push: ${IMAGE}"
gcloud builds submit --project="${GCP_PROJECT}" --tag="${IMAGE}" .

echo "==> 2/3 Deploy en la VM (pull + restart contenedores)"
gcloud compute ssh "${VM_NAME}" \
  --project="${GCP_PROJECT}" --zone="${GCP_ZONE}" --tunnel-through-iap --quiet \
  --command="set -e
META='http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token'
TOKEN=\$(curl -s -H 'Metadata-Flavor: Google' \"\$META\" | python3 -c 'import sys,json;print(json.load(sys.stdin)[\"access_token\"])')
echo \"\$TOKEN\" | sudo docker login -u oauth2accesstoken --password-stdin https://europe-southwest1-docker.pkg.dev >/dev/null
sudo docker pull '${IMAGE}'
echo '${IMAGE}' | sudo tee /opt/evaluabot/image.ref >/dev/null
sudo bash /opt/evaluabot/start.sh"

echo "==> 3/3 Verificacion (la app tarda ~40s en arrancar; reintento hasta 2 min)"
BOT_OK=""
for i in $(seq 1 8); do
  sleep 15
  if gcloud compute ssh "${VM_NAME}" \
      --project="${GCP_PROJECT}" --zone="${GCP_ZONE}" --tunnel-through-iap --quiet \
      --command="sudo docker logs evaluabot-app 2>&1 | grep -q 'Bolt app is running'" 2>/dev/null; then
    BOT_OK="si"; break
  fi
  echo "  ... aun arrancando (intento ${i}/8)"
done
[[ -n "${BOT_OK}" ]] && echo "BOT OK (Bolt app is running)" \
  || { echo "ERROR: el bot no arranco en 2 min. Logs: sudo docker logs evaluabot-app"; exit 1; }
HTTP_CODE="$(curl -s -o /dev/null -w '%{http_code}' "https://${DOMAIN}" || echo 000)"
echo "Web https://${DOMAIN} -> HTTP ${HTTP_CODE}"
[[ "${HTTP_CODE}" == "200" ]] && echo "" && echo "OK Deploy completado: ${IMAGE}" || { echo "AVISO: la web no devuelve 200; revisa logs de caddy"; exit 1; }
