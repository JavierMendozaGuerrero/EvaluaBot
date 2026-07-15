import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from starlette.staticfiles import StaticFiles

from .. import config
from .errors import register_exception_handlers
from .hardening import (
    AuthRateLimitMiddleware,
    BodySizeLimitMiddleware,
    GenerationRateLimitMiddleware,
    SecurityHeadersMiddleware,
)
from .files import router as files_router
from .routers.admin import router as admin_router
from .routers.auth import router as auth_router
from .routers.ca import router as ca_router
from .routers.eval_anual import router as eval_anual_router
from .routers.evaluaciones_extra import router as evaluaciones_extra_router
from .routers.perfiles import router as perfiles_router
from .routers.personal_slack import router as personal_slack_router
from .routers.project_evals import router as project_evals_router
from .routers.reports import router as reports_router

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)
app.add_middleware(GZipMiddleware, minimum_size=1000)
app.add_middleware(BodySizeLimitMiddleware)
app.add_middleware(GenerationRateLimitMiddleware)
app.add_middleware(AuthRateLimitMiddleware)
app.add_middleware(SecurityHeadersMiddleware)

register_exception_handlers(app)

# El orden importa poco salvo para /api/files/{...:path}, que se registra el último
# para no competir con ninguna otra ruta literal bajo /api/files (no la hay hoy, pero
# así queda claro que es un catch-all dentro de su propio prefijo).
app.include_router(auth_router)
app.include_router(perfiles_router)
app.include_router(ca_router)
app.include_router(project_evals_router)
app.include_router(evaluaciones_extra_router)
app.include_router(personal_slack_router)
app.include_router(eval_anual_router)
app.include_router(reports_router)
app.include_router(admin_router)
app.include_router(files_router)


class _SPAStaticFiles(StaticFiles):
    """Sirve el build de React y, para rutas que no son un archivo real (navegación
    interna de la SPA), devuelve index.html en lugar de un 404."""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        if response.status_code == 404:
            return await super().get_response("index.html", scope)
        return response


# Se monta en "/" el ÚLTIMO: las rutas /api ya registradas arriba tienen prioridad, y este
# catch-all solo atiende lo que no sea API (la web React). Si no hay build (dev local sin
# `npm run build`), no se monta y el backend queda como solo-API.
if os.path.isdir(config.FRONTEND_DIST):
    app.mount("/", _SPAStaticFiles(directory=config.FRONTEND_DIST, html=True), name="spa")


def iniciar_api_backend():
    import asyncio
    import logging
    import os

    import uvicorn

    os.makedirs(config.CARPETA_WEB, exist_ok=True)
    server = uvicorn.Server(
        uvicorn.Config(app, host="0.0.0.0", port=config.PUERTO_WEB, access_log=False, log_level="info")
    )
    try:
        asyncio.run(server.serve())
    except (SystemExit, OSError):
        # Uvicorn hace sys.exit() internamente si el puerto ya está en uso (no deja
        # escapar un OSError "limpio"), así que capturamos ambos para dar el mismo
        # mensaje amistoso que daba el servidor http.server anterior.
        logging.error(
            "No se pudo iniciar la API en http://localhost:%s. "
            "Ese puerto parece estar ocupado. Cierra el otro proceso o arranca con: "
            '$env:PUERTO_WEB="8001"; python bot.py',
            config.PUERTO_WEB,
        )
