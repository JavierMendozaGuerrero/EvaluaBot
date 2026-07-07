"""Protecciones de la API que no dependen del proveedor de auth.

- Límite de tamaño de cuerpo: el servidor http.server anterior capaba el JSON a
  1 MB; FastAPI/Uvicorn no traen límite por defecto, así que sin esto cualquiera
  podría mandar un body arbitrariamente grande. La subida de informes finales
  (multipart) necesita más margen que un JSON normal.

- Rate limit de endpoints de generación: los /api/generar* llaman a la API de
  Anthropic (coste real por llamada) y a generación de PDF/DOCX (CPU). Límite
  simple en memoria por token/IP — suficiente mientras el despliegue sea de un
  solo proceso (igual que las sesiones en state.py).
"""

import threading
import time

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

MAX_BODY_JSON = 1_000_000          # 1 MB, mismo límite que el servidor anterior
MAX_BODY_UPLOAD = 15_000_000       # 15 MB para /api/subir-informe-final (docx)

RUTAS_GENERACION = "/api/generar"  # cubre /api/generar, /api/generar-anual, /api/generar-pdf-*
LIMITE_GENERACION = 10             # peticiones...
VENTANA_GENERACION = 60            # ...por minuto y por cliente


class BodySizeLimitMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http" or scope["method"] not in ("POST", "PUT", "PATCH", "DELETE"):
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        limite = MAX_BODY_UPLOAD if path == "/api/subir-informe-final" else MAX_BODY_JSON
        headers = dict(scope.get("headers", []))
        try:
            content_length = int(headers.get(b"content-length", b"0"))
        except ValueError:
            content_length = 0
        if content_length > limite:
            respuesta = JSONResponse({"error": "El cuerpo de la petición es demasiado grande."}, status_code=413)
            await respuesta(scope, receive, send)
            return
        await self.app(scope, receive, send)


class GenerationRateLimitMiddleware:
    """Ventana deslizante simple por cliente para los endpoints de generación."""

    def __init__(self, app: ASGIApp, limite: int = LIMITE_GENERACION, ventana: int = VENTANA_GENERACION):
        self.app = app
        self.limite = limite
        self.ventana = ventana
        self._historial: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def _clave_cliente(self, scope: Scope) -> str:
        headers = dict(scope.get("headers", []))
        auth = headers.get(b"authorization", b"").decode()
        if auth.lower().startswith("bearer "):
            return auth[7:].strip()
        cliente = scope.get("client")
        return cliente[0] if cliente else "desconocido"

    def _excede(self, clave: str) -> bool:
        ahora = time.monotonic()
        with self._lock:
            marcas = [m for m in self._historial.get(clave, []) if ahora - m < self.ventana]
            if len(marcas) >= self.limite:
                self._historial[clave] = marcas
                return True
            marcas.append(ahora)
            self._historial[clave] = marcas
            # Poda ocasional para que el dict no crezca sin límite
            if len(self._historial) > 10_000:
                self._historial = {
                    k: v for k, v in self._historial.items() if v and ahora - v[-1] < self.ventana
                }
            return False

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if (
            scope["type"] == "http"
            and scope["method"] == "POST"
            and scope.get("path", "").startswith(RUTAS_GENERACION)
        ):
            if self._excede(self._clave_cliente(scope)):
                respuesta = JSONResponse(
                    {"error": "Has generado demasiados documentos seguidos. Espera un minuto y vuelve a intentarlo."},
                    status_code=429,
                )
                await respuesta(scope, receive, send)
                return
        await self.app(scope, receive, send)
