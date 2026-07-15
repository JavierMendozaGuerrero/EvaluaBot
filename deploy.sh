#!/usr/bin/env bash
set -euo pipefail

# ============================================================
# Deploy de EvaluaBot en el NAS Synology de igeneris (Irene)
#   chmod +x deploy.sh
#   ./deploy.sh
# ============================================================

# ---- Config ----
NAS_USER="ipedros"
NAS_HOST="10.0.100.3"                     # solo desde oficina o VPN
NAS_PORT="22"
REMOTE_DIR="/volume1/docker/evaluabot"
PROJECT_NAME="evaluabot"
LOCAL_PORT="8001"
# ----------------

DOCKER="/usr/local/bin/docker"
DOCKER_COMPOSE="/usr/local/bin/docker-compose"

echo "→ Asegurando directorio remoto ${REMOTE_DIR}"
ssh -p "${NAS_PORT}" "${NAS_USER}@${NAS_HOST}" "mkdir -p '${REMOTE_DIR}'"

echo "→ Sincronizando código (via tar+ssh)"
tar czf - \
  --exclude '.git' \
  --exclude '__pycache__' \
  --exclude '.venv' \
  --exclude '.env' \
  --exclude 'deploy.sh' \
  --exclude '.DS_Store' \
  --exclude '.claude' \
  --exclude 'node_modules' \
  --exclude 'frontend/dist' \
  --exclude '.pytest_cache' \
  . | ssh -p "${NAS_PORT}" "${NAS_USER}@${NAS_HOST}" \
    "rm -rf '${REMOTE_DIR}'/* '${REMOTE_DIR}'/.[!.]* 2>/dev/null; tar xzf - -C '${REMOTE_DIR}'"

echo "→ Reconstruyendo y reiniciando contenedor"
ssh -p "${NAS_PORT}" "${NAS_USER}@${NAS_HOST}" bash -s <<EOF
  set -euo pipefail
  cd "${REMOTE_DIR}"
  sudo ${DOCKER_COMPOSE} -p "${PROJECT_NAME}" up -d --build
  sudo ${DOCKER} image prune -f
EOF

echo "→ Estado del contenedor:"
ssh -p "${NAS_PORT}" "${NAS_USER}@${NAS_HOST}" \
  "sudo ${DOCKER} ps --filter name=${PROJECT_NAME} --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'"

echo ""
echo "✓ Deploy completado"
echo "  App:  http://${NAS_HOST}:${LOCAL_PORT}/"
