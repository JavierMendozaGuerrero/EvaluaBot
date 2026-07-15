# EvaluaBot — imagen única que sirve backend (API + Slack) y el frontend React juntos.
#
# Construir:   docker build -t evaluabot .
# Probar local: docker run --rm -p 8001:8000 --env-file .env -e PORT=8000 evaluabot
#
# Notas:
# - Etapa 1 (Node): compila el frontend React a estáticos (frontend/dist).
# - Etapa 2 (Python): instala el backend, copia el código y el build del frontend, y
#   arranca `python bot.py`. FastAPI sirve la web React en "/" y la API en "/api".
# - Escucha en el puerto que indique la variable PORT (por defecto 8000).
# - Toda la configuración (tokens de Slack/Notion/Claude, SMTP, etc.) llega por
#   variables de entorno. NO se copia ningún .env dentro de la imagen (ver .dockerignore).

# ---------- Etapa 1: build del frontend React ----------
FROM node:20-slim AS frontend
WORKDIR /frontend
# Instalar deps primero (capa cacheada mientras package.json no cambie).
COPY frontend/package.json ./
RUN npm install
COPY frontend/ ./
RUN npm run build
# Resultado: /frontend/dist

# ---------- Etapa 2: backend Python ----------
FROM python:3.11-slim

# Salida de logs sin buffer (aparecen al instante) y sin .pyc.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# 1) Instalar dependencias primero para aprovechar la caché de capas de Docker:
#    mientras requirements.txt no cambie, esta capa no se reconstruye.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 2) Copiar el resto del código (el .dockerignore excluye node_modules y frontend/dist).
COPY . .

# 3) Copiar el build del frontend generado en la etapa anterior.
COPY --from=frontend /frontend/dist /app/frontend/dist

ENV PORT=8000
EXPOSE 8000

# Arranca el bot: abre Socket Mode con Slack, los ciclos programados y la API/web en $PORT.
CMD ["python", "bot.py"]
