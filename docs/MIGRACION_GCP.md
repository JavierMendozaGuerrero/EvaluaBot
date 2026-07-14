# Migración a Google Cloud — Informe de arquitectura

Guía práctica y mínima para llevar **EvaluaBot** a Google Cloud Platform (GCP).

---

## 1. Qué es hoy la aplicación (en una frase)

Un **único proceso Python 3.11 siempre encendido** que mantiene una conexión
WebSocket a Slack, corre 7 tareas programadas en hilos y expone una API REST
(FastAPI) para un frontend React. **No usa base de datos SQL: los datos viven en
Notion.**

---

## 2. Componentes

| Componente | Qué es | Detalle |
|---|---|---|
| **Backend** | Python 3.11 + FastAPI + uvicorn | Punto de entrada `bot.py` → `backend/main.py` |
| **Conexión Slack** | Slack **Socket Mode** (WebSocket saliente) | ⭐ No necesita URL pública ni webhook entrante |
| **API REST** | FastAPI en el puerto `8000` (`PUERTO_WEB`) | La consume el frontend React |
| **Tareas programadas** | 7 hilos daemon (envíos + recordatorios) | Envían evaluaciones y recordatorios por calendario |
| **Base de datos** | **Notion** (vía `notion-client`) | Usuarios web, evaluaciones, empleados, preguntas |
| **IA** | Anthropic Claude (`anthropic`) | Genera informes |
| **Email** | SMTP Gmail | Solo recuperación de contraseña |
| **Frontend** | React 19 + Vite | Hoy pensado para Vercel |
| **Ficheros generados** | Carpeta local `backend/dashboard_web/` | Informes (html/pdf/docx), cachés y sesiones `.json` |

---

## 3. Los 3 puntos que condicionan TODA la migración

> Estos son los que hay que entender sí o sí antes de elegir servicio de GCP.

### 3.1. Es **stateful** y debe correr en **una sola instancia**
El estado vive en memoria (`backend/state.py`): sesiones web, conversaciones de
Slack en curso, evaluaciones pendientes. Además hay **una sola** conexión Socket
Mode a Slack. Consecuencia:

- **No se puede escalar horizontalmente** (nada de 2+ instancias).
- Debe estar **siempre encendido** con la **CPU siempre asignada** (los hilos
  trabajan aunque no entren peticiones HTTP).
- Al reiniciar se pierden las sesiones y conversaciones en curso (aceptable, pero
  a tener en cuenta).

### 3.2. Escribe en **disco local**
Genera y sirve ficheros en `backend/dashboard_web/`: informes html/pdf/docx,
cachés (`informe_*_cache.json`), sesiones anuales (`sesion_anual_*.json`) y config
de anonimato. En serverless el disco es **efímero** (se borra al reiniciar/desplegar).

### 3.3. Secretos en `.env`
Tokens de Slack, Notion, Anthropic y contraseña SMTP. En GCP van a **Secret Manager**,
no en el contenedor.

---

## 4. Arquitectura recomendada en GCP

**Opción A (recomendada): Cloud Run de una sola instancia.** Mantiene el modelo
serverless/gestionado pero respetando que la app es stateful.

```
                         ┌─────────────────────────────┐
  Slack  ◄──WebSocket────┤  Cloud Run (1 instancia)    │
  (Socket Mode)          │  min=1, max=1               │
                         │  CPU siempre asignada        │──► Notion API
  Navegador ──HTTPS────► │  (--no-cpu-throttling)       │──► Anthropic Claude
  (React)                │  Contenedor Python/FastAPI   │──► SMTP Gmail
                         └──────────┬──────────────────┘
                                    │ monta como volumen
                         ┌──────────▼──────────┐   ┌──────────────────┐
                         │  Cloud Storage      │   │  Secret Manager  │
                         │  (dashboard_web/)   │   │  (tokens .env)   │
                         └─────────────────────┘   └──────────────────┘

  Frontend React ──► Firebase Hosting  (o mantener en Vercel)
```

**Servicios GCP a usar:**

| Necesidad | Servicio GCP |
|---|---|
| Ejecutar el backend siempre encendido | **Cloud Run** (`min-instances=1`, `max-instances=1`, `--no-cpu-throttling`) |
| Guardar los ficheros generados | **Cloud Storage** (bucket) montado como volumen en `dashboard_web/` |
| Guardar los secretos | **Secret Manager** |
| Servir el frontend React | **Firebase Hosting** (o Cloud Storage + Cloud CDN, o seguir en Vercel) |
| Imagen del contenedor | **Artifact Registry** |
| Logs y métricas | **Cloud Logging / Monitoring** (ya incluido en Cloud Run) |

**Opción B (alternativa más simple mentalmente): una VM `e2-small` en Compute
Engine** con la app como servicio `systemd` y disco persistente. Encaja bien con
un proceso stateful siempre encendido; a cambio, gestionas tú el sistema operativo,
parches y despliegues. Cloud Run (Opción A) es más recomendable por mantenimiento.

---

## 5. Cambios necesarios en el código (mínimos)

1. **Puerto dinámico.** Cloud Run inyecta la variable `PORT` (normalmente `8080`).
   Hoy se usa `PUERTO_WEB` (default 8000). Basta con arrancar leyendo `PORT`:
   en el despliegue, poner `PUERTO_WEB=$PORT` — o ajustar `config.py` para que
   `PUERTO_WEB` lea `PORT` si existe. *(1 línea)*

2. **Ficheros generados a Cloud Storage.** Montar el bucket como volumen en la
   ruta de `CARPETA_WEB` (`backend/dashboard_web/`) — **sin tocar código**, se
   configura en el despliegue de Cloud Run (volumen gcsfuse). Alternativa: cambiar
   las lecturas/escrituras a la API de GCS (más trabajo).

3. **Secretos desde Secret Manager.** Cloud Run los inyecta como variables de
   entorno, así que `os.environ.get(...)` en `config.py` **sigue funcionando igual**.
   No hay que cambiar código, solo el despliegue.

4. **`Dockerfile`** (no existe todavía). Ejemplo mínimo:
   ```dockerfile
   FROM python:3.11-slim
   WORKDIR /app
   COPY requirements.txt .
   RUN pip install --no-cache-dir -r requirements.txt
   COPY . .
   CMD ["python", "bot.py"]
   ```

5. **CORS.** Poner `FRONTEND_ORIGIN` a la URL final del frontend (Firebase/Vercel).

> El resto del código (Slack, Notion, Claude, FastAPI) **no cambia**: son llamadas
> salientes que funcionan igual en GCP.

---

## 6. Variables de entorno a migrar a Secret Manager

De `.env` / `.env.example`:

```
SLACK_BOT_TOKEN, SLACK_APP_TOKEN          # Slack (Socket Mode)
NOTION_TOKEN                              # Notion
ANTHROPIC_API_KEY                         # Claude
SMTP_HOST, SMTP_PORT, SMTP_USER,          # Email (recuperar contraseña)
SMTP_PASSWORD, SMTP_FROM, SMTP_USE_TLS
```

Configuración no secreta (variables de entorno normales en Cloud Run):
`APP_MODE`, `FRONTEND_ORIGIN`, `APP_PUBLIC_URL`, `NOTION_PARENT_PAGE_ID`,
`NOTION_*_DATABASE_*`, `SLACK_CHANNEL_ID`, `PUERTO_WEB`/`PORT`, etc.

---

## 7. Checklist de migración

- [ ] Crear proyecto GCP + habilitar APIs (Cloud Run, Artifact Registry, Secret Manager, Cloud Storage)
- [ ] Escribir el `Dockerfile` y probar la imagen en local
- [ ] Subir todos los secretos a **Secret Manager**
- [ ] Crear un **bucket de Cloud Storage** para `dashboard_web/`
- [ ] Desplegar en **Cloud Run** con: `--min-instances=1 --max-instances=1 --no-cpu-throttling`, volumen GCS montado en `dashboard_web/`, y los secretos enlazados
- [ ] Verificar que Slack conecta (Socket Mode) y que la API responde
- [ ] Desplegar el **frontend** (Firebase Hosting) y ajustar `VITE_API_BASE_URL` + `FRONTEND_ORIGIN`
- [ ] Probar un flujo completo: evaluación en Slack → guardado en Notion → informe en la web

---

## 8. Coste orientativo (muy aproximado)

Al ser 1 instancia pequeña siempre encendida: Cloud Run con CPU siempre asignada
ronda unos **pocos € al mes** (equivalente a una VM `e2-small`). Cloud Storage y
Secret Manager, céntimos. El gasto real dependerá del uso de la **API de Claude**,
que es aparte (facturación Anthropic).

---

## 9. Resumen ejecutivo (TL;DR)

- Es **un proceso Python siempre encendido, de una sola instancia** → **Cloud Run
  con `min=max=1` y CPU siempre asignada** (o una VM).
- **No hay base de datos que migrar**: los datos están en Notion.
- **Secretos** → Secret Manager. **Ficheros generados** → bucket de Cloud Storage.
- **Frontend** React → Firebase Hosting (o seguir en Vercel).
- Cambios de código: **casi ninguno** (Dockerfile + puerto dinámico + montar bucket).
```
