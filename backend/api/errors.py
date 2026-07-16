import logging

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from ..excepciones import ErrorIA
from ..ia import CONTACTO


MSG_ERROR_INESPERADO = (
    "Ha ocurrido un error inesperado y la acción no se ha completado. Vuelve a intentarlo; "
    f"si sigue fallando, avisa al responsable de la herramienta ({CONTACTO})."
)


def register_exception_handlers(app):
    @app.exception_handler(ErrorIA)
    async def _error_ia(request: Request, exc: ErrorIA):
        # El mensaje de ErrorIA ya está escrito para el usuario; el detalle técnico lo ha
        # dejado en el log quien la lanzó.
        # `code` viaja aparte para que el frontend pueda traducirlo (el mensaje va en español).
        # 503 invita a reintentar, así que solo vale para lo pasajero; lo definitivo (sin
        # saldo, API mal configurada) no se arregla reintentando y necesita que lo veamos.
        if exc.definitivo:
            logging.error("Fallo de IA no recuperable en %s [%s]: %s", request.url.path, exc.codigo, exc)
            return JSONResponse({"error": str(exc), "code": exc.codigo}, status_code=500)
        logging.warning("Fallo de IA en %s [%s]: %s", request.url.path, exc.codigo, exc)
        return JSONResponse({"error": str(exc), "code": exc.codigo}, status_code=503)

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
        # damos un mensaje genérico para no filtrar rutas ni internals. Aun así el
        # usuario debe entender qué hacer, así que le decimos a quién acudir.
        logging.exception("Error en API")
        return JSONResponse(
            {"error": MSG_ERROR_INESPERADO, "code": "error_inesperado"}, status_code=500
        )
