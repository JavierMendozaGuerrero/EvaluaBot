from fastapi import APIRouter, Body, Depends

from ..deps import get_session, require_session
from ...notion_service import guardar_idioma_por_sesion, guardar_pais_por_sesion, idioma_por_sesion
from ...users import (
    autenticar_usuario,
    cambiar_password_con_token,
    crear_sesion,
    obtener_sesion_por_token,
    registrar_usuario,
    solicitar_reset_password,
)

router = APIRouter()

PAISES_PERMITIDOS = ("España", "México", "Portugal")


@router.get("/api/health")
def health():
    return {"ok": True}


@router.get("/api/me")
def me(session=Depends(get_session)):
    if not session:
        return {"user": None}
    usuario = {**session, "idioma": idioma_por_sesion(session)}
    return {"user": usuario}


@router.post("/api/register")
def register(datos: dict = Body(default={})):
    registrar_usuario(datos.get("username", ""), datos.get("password", ""))
    return {"ok": True}


@router.post("/api/login")
def login(datos: dict = Body(default={})):
    usuario = autenticar_usuario(datos.get("username", ""), datos.get("password", ""))
    token = crear_sesion(usuario)
    sesion = obtener_sesion_por_token(token)
    sesion_con_idioma = {**sesion, "idioma": idioma_por_sesion(sesion)}
    return {"token": token, "user": sesion_con_idioma}


@router.post("/api/password-reset/request")
def password_reset_request(datos: dict = Body(default={})):
    solicitar_reset_password(datos.get("email", ""))
    return {"ok": True}


@router.post("/api/password-reset/confirm")
def password_reset_confirm(datos: dict = Body(default={})):
    cambiar_password_con_token(datos.get("token", ""), datos.get("password", ""), datos.get("confirmPassword"))
    return {"ok": True}


@router.post("/api/set-idioma")
def set_idioma(datos: dict = Body(default={}), session=Depends(require_session)):
    idioma = (datos.get("idioma") or "").strip().lower()
    if idioma not in ("es", "en"):
        idioma = "es"
    ok = guardar_idioma_por_sesion(session, idioma)
    return {"ok": bool(ok), "idioma": idioma}


@router.post("/api/set-pais")
def set_pais(datos: dict = Body(default={}), session=Depends(require_session)):
    pais = (datos.get("pais") or "").strip()[:80]
    if pais not in PAISES_PERMITIDOS:
        raise ValueError("País no permitido.")
    guardado = guardar_pais_por_sesion(session, pais)
    return {"ok": True, "pais": guardado}
