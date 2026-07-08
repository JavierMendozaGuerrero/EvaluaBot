from fastapi import Depends, Request

from .. import users
from ..notion_service import obtener_advisees
from ..utils import normalizar_nombre


def get_token(request: Request) -> str:
    """Extrae el token de sesión de la cabecera Authorization: Bearer.

    Antes se aceptaba también ?token= en la URL para las descargas por
    window.open(), pero eso filtraba el token en el historial/logs/Referer.
    El frontend ahora descarga con la cabecera (fetch + blob), así que el
    token ya nunca viaja en la URL.
    """
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth.split(" ", 1)[1].strip()
    return ""


def get_session(request: Request):
    return users.obtener_sesion_por_token(get_token(request))


def require_session(session: dict | None = Depends(get_session)):
    if not session:
        raise PermissionError("Inicia sesión para acceder.")
    return session


def require_admin(message: str = "Solo administradores pueden acceder."):
    """Factory: cada ruta del original tiene su propio mensaje de error para el caso no-admin."""

    def _dep(session: dict = Depends(require_session)):
        if not session.get("is_admin"):
            raise PermissionError(message)
        return session

    return _dep


def exigir_acceso_advisee(session: dict, evaluado: str) -> None:
    """Lanza PermissionError si el CA de la sesión no tutela a `evaluado` (admin pasa siempre)."""
    if session.get("is_admin"):
        return
    advisees_ca = obtener_advisees(
        session.get("persona", ""),
        ca_aliases=[session.get("username", ""), session.get("email", "")],
    )
    if normalizar_nombre(evaluado) not in [normalizar_nombre(a) for a in advisees_ca]:
        raise PermissionError("Solo el CA de esta persona (o un administrador) puede acceder.")
