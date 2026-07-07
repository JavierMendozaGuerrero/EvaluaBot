from fastapi import Depends, Request

from .. import users
from ..notion_service import obtener_advisees
from ..utils import normalizar_nombre


def get_session(request: Request):
    """Bearer header primero; si no hay, cae a ?token= (usado por descargas por window.open())."""
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return users.obtener_sesion_por_token(auth.split(" ", 1)[1].strip())
    token_query = request.query_params.get("token", "")
    return users.obtener_sesion_por_token(token_query)


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
