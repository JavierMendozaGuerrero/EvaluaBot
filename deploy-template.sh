#!/usr/bin/env bash
set -euo pipefail

# ============================================================
# Deploy template para NAS Synology igeneris
# ============================================================
# Este archivo SÍ va en git; deploy.sh NO (está en .gitignore),
# porque cada dev tiene su usuario, su carpeta y su puerto.
#
# Uso, una vez por dev:
#   cp deploy-template.sh deploy.sh
#   # edita la sección Config con tus datos
#   chmod +x deploy.sh
#   ./deploy.sh
#
# Alta de un dev nuevo (claves SSH, permisos, docker): ver ONBOARDING_DEVS.md
# ============================================================

# ---- Config (AJUSTA ESTOS VALORES) ----
NAS_USER="TU_USUARIO_DSM"            # p.ej. ipedros, jbarayazarra, jmendoza
NAS_HOST="10.0.100.3"                # IP del NAS (mismo para todos; solo oficina o VPN)
NAS_PORT="22"
REMOTE_DIR="/volume1/docker/TU_APP"  # p.ej. evaluabot o transcripciones
PROJECT_NAME="TU_APP"                # el mismo nombre que la carpeta
LOCAL_PORT="8001"                    # 8001=evaluabot, 8002=transcripciones
# ---------------------------------------

DOCKER="/usr/local/bin/docker"
DOCKER_COMPOSE="/usr/local/bin/docker-compose"

echo "→ Asegurando directorio remoto ${REMOTE_DIR}"
ssh -p "${NAS_PORT}" "${NAS_USER}@${NAS_HOST}" "mkdir -p '${REMOTE_DIR}'"

echo "→ Sincronizando código (via tar+ssh)"
# El .env vive solo en el NAS: se sube a mano una vez, no viaja en el tar y el
# borrado remoto lo respeta. Si se borra, docker-compose no levanta el contenedor.
tar czf - \
  --exclude './.git' \
  --exclude '__pycache__' \
  --exclude './.venv' \
  --exclude '.pytest_cache' \
  --exclude './.env' \
  --exclude './.env.*' \
  --exclude './.env_*' \
  --exclude './frontend/node_modules' \
  --exclude './frontend/dist' \
  --exclude './docs' \
  --exclude './deploy.sh' \
  --exclude './deploy-template.sh' \
  --exclude '.DS_Store' \
  --exclude './.claude' \
  . | ssh -p "${NAS_PORT}" "${NAS_USER}@${NAS_HOST}" \
    "cd '${REMOTE_DIR}' && find . -mindepth 1 -maxdepth 1 ! -name '.env' -exec rm -rf {} + ; tar xzf - -C '${REMOTE_DIR}'"

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
echo "  App:   http://${NAS_HOST}:${LOCAL_PORT}/"
echo "  Docs:  http://${NAS_HOST}:${LOCAL_PORT}/docs (si es FastAPI)"
