# Onboarding — Deploy en el NAS de igeneris

Pásale este documento a cada dev con sus datos personalizados.

## 1. Tus datos

| Dev | Usuario SSH | Carpeta en el NAS | Puerto local |
|---|---|---|---|
| Irene | `ipedros` | `/volume1/docker/evaluabot` | 8001 |
| Javier | `jmendoza` | `/volume1/docker/evaluabot` (compartida con Irene) | 8001 |
| Jaime | `jbarayazarra` | `/volume1/docker/transcripciones` | 8002 |

**NAS**: `10.0.100.3` (solo desde la red de oficina/VPN)

## 2. Primer login (una vez por dev)

Recibirás un email de Synology para poner tu contraseña. Después:

### a) Cambia tu contraseña
Entra en `https://10.0.100.3:5001` (o QuickConnect) → login → te pedirá poner tu contraseña definitiva.

### b) Copia tu clave SSH al NAS

Desde tu Mac (crea una si no tienes):

```bash
ls ~/.ssh/id_ed25519.pub 2>/dev/null || ssh-keygen -t ed25519 -N "" -f ~/.ssh/id_ed25519
ssh-copy-id TU_USUARIO@10.0.100.3
```

Sustituye `TU_USUARIO` por `ipedros` / `jbarayazarra` / `jmendoza`.

### c) Arregla permisos de .ssh (necesario en Synology)

```bash
ssh TU_USUARIO@10.0.100.3 'chmod 700 ~/.ssh && chmod 600 ~/.ssh/authorized_keys && chmod 755 ~'
```

### d) Verifica que entras sin password y tienes docker

```bash
ssh TU_USUARIO@10.0.100.3 'whoami && sudo /usr/local/bin/docker --version'
```

Debería devolver tu usuario y `Docker version 24.x.x`.

## 3. Estructura de tu app

En la raíz de tu proyecto necesitas al menos:

```
tu-app/
├── app/                    (o src/, según tu lenguaje)
│   └── main.py
├── requirements.txt        (o package.json, go.mod, etc.)
├── Dockerfile
├── docker-compose.yml
└── deploy.sh
```

Ejemplo mínimo de `Dockerfile` para FastAPI (Python):
```dockerfile
FROM python:3.13-slim
WORKDIR /code
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY ./app /code/app
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

Ejemplo de `docker-compose.yml`:
```yaml
services:
  api:
    build: .
    container_name: TU_APP
    ports:
      - "TU_PUERTO:8000"    # 8001=evaluabot, 8002=transcripciones
    restart: unless-stopped
```

## 4. Configurar tu deploy.sh

Copia `deploy-template.sh` a la raíz de tu repo como `deploy.sh` y edita las variables:

```bash
NAS_USER="ipedros"                              # tu usuario
REMOTE_DIR="/volume1/docker/evaluabot"          # tu carpeta
PROJECT_NAME="evaluabot"                        # nombre del contenedor
LOCAL_PORT="8001"                               # tu puerto asignado
```

## 5. Desplegar

```bash
chmod +x deploy.sh
./deploy.sh
```

## 6. Ver tu app

- Desde la LAN: `http://10.0.100.3:PUERTO/`
- Logs en vivo: `ssh TU_USUARIO@10.0.100.3 'sudo /usr/local/bin/docker logs -f TU_APP'`
- Reiniciar: `ssh TU_USUARIO@10.0.100.3 'sudo /usr/local/bin/docker restart TU_APP'`
- Entrar al contenedor: `ssh -t TU_USUARIO@10.0.100.3 'sudo /usr/local/bin/docker exec -it TU_APP bash'`

## 7. Reglas de convivencia

- **No toques carpetas que no sean la tuya** en `/volume1/docker/`.
- **Puertos asignados** — no cambies el tuyo:
  - evaluabot → 8001
  - transcripciones → 8002
- Si necesitas variables de entorno secretas, crea un `.env` local (nunca en git) y añádelo al `docker-compose.yml`:
  ```yaml
  services:
    api:
      env_file: .env
  ```
  El `deploy.sh` NO sube `.env` (está excluido). Súbelo una vez manualmente:
  ```bash
  scp -O .env TU_USUARIO@10.0.100.3:/volume1/docker/TU_APP/.env
  ```

## 8. Problemas comunes

- **"Permission denied"** → revisa el paso 2c (permisos de .ssh).
- **"docker: command not found"** → usa la ruta completa `/usr/local/bin/docker`.
- **Contenedor no arranca** → mira los logs: `sudo /usr/local/bin/docker logs TU_APP`
- **Puerto ocupado** → algún deploy anterior no bajó bien. Ejecuta `sudo /usr/local/bin/docker ps -a` y borra el contenedor viejo con `sudo /usr/local/bin/docker rm -f TU_APP`.

Para cualquier duda que no esté aquí → escribe a tech@igeneris.com.
