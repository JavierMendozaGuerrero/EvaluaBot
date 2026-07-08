import logging

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


def register_exception_handlers(app):
    @app.exception_handler(PermissionError)
    async def _permission_error(request: Request, exc: PermissionError):
        return JSONResponse({"error": str(exc)}, status_code=403)

    @app.exception_handler(ValueError)
    async def _value_error(request: Request, exc: ValueError):
        return JSONResponse({"error": str(exc)}, status_code=400)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError):
        mensaje = "; ".join(
            f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()
        )
        return JSONResponse({"error": mensaje or "Datos inválidos."}, status_code=400)

    @app.exception_handler(StarletteHTTPException)
    async def _http_exception(request: Request, exc: StarletteHTTPException):
        # El original siempre devuelve exactamente este cuerpo para cualquier ruta no encontrada.
        if exc.status_code == 404:
            return JSONResponse({"error": "No encontrado"}, status_code=404)
        detail = exc.detail if isinstance(exc.detail, str) else "Error"
        return JSONResponse({"error": detail}, status_code=exc.status_code)

    @app.exception_handler(Exception)
    async def _generic_error(request: Request, exc: Exception):
        # El detalle del error queda en el log del servidor; al cliente solo le
        # damos un mensaje genérico para no filtrar rutas ni internals.
        logging.exception("Error en API")
        return JSONResponse({"error": "Error interno del servidor."}, status_code=500)
