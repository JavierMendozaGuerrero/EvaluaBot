# EvaluaBot — imagen para Google Cloud Run (o cualquier runtime de contenedores).
#
# Construir:   docker build -t evaluabot .
# Probar local: docker run --rm -p 8080:8080 --env-file .env -e PORT=8080 evaluabot
#
# Notas:
# - El proceso arranca con `python bot.py`, igual que en local.
# - Escucha en el puerto que indique la variable PORT (Cloud Run usa 8080).
# - Toda la configuración (tokens de Slack/Notion/Claude, SMTP, etc.) llega por
#   variables de entorno. NO se copia ningún .env dentro de la imagen (ver .dockerignore);
#   en Cloud Run los secretos se inyectan desde Secret Manager.

FROM python:3.11-slim

# Salida de logs sin buffer (aparecen al instante en Cloud Logging) y sin .pyc.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# 1) Instalar dependencias primero para aprovechar la caché de capas de Docker:
#    mientras requirements.txt no cambie, esta capa no se reconstruye.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 2) Copiar el resto del código.
COPY . .

# Puerto por defecto de Cloud Run (informativo; el valor real llega en $PORT).
ENV PORT=8080
EXPOSE 8080

# Arranca el bot: abre Socket Mode con Slack, los ciclos programados y la API en $PORT.
CMD ["python", "bot.py"]
