from fastapi import APIRouter, Body, Depends, Request

from ... import config
from ..deps import get_session, get_token, require_session
from ...notion_service import guardar_idioma_por_sesion, guardar_pais_por_sesion, idioma_por_sesion
from ...users import (
    autenticar_usuario,
    cambiar_password_con_token,
    cerrar_sesion,
    confirmar_registro,
    crear_sesion,
    obtener_sesion_por_token,
    solicitar_registro,
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


def _exigir_registro_habilitado():
    if not config.REGISTRO_WEB_HABILITADO:
        raise PermissionError("El registro está deshabilitado. Contacta con RRHH para tu cuenta.")


@router.post("/api/register")
def register(datos: dict = Body(default={})):
    _exigir_registro_habilitado()
    # Paso 1: valida y envía el código. El frontend detecta este marcador y pide el código.
    email = solicitar_registro(datos.get("username", ""), datos.get("password", ""), datos.get("email", ""))
    raise PermissionError(f"VERIFICACION_REQUERIDA:{email}")


@router.post("/api/register/verify")
def register_verify(datos: dict = Body(default={})):
    _exigir_registro_habilitado()
    # Paso 2: confirma el código y crea la cuenta.
    confirmar_registro(datos.get("email", ""), datos.get("code", ""))
    return {"ok": True}


@router.post("/api/logout")
def logout(request: Request):
    cerrar_sesion(get_token(request))
    return {"ok": True}


@router.post("/api/login")
def login(datos: dict = Body(default={})):
    usuario = autenticar_usuario(datos.get("username", ""), datos.get("password", ""))
    token = crear_sesion(usuario, remember=bool(datos.get("remember")))
    sesion = obtener_sesion_por_token(token)
    sesion_con_idioma = {**sesion, "idioma": idioma_por_sesion(sesion)}
    return {"token": token, "user": sesion_con_idioma}


def _base_url_publica(request: Request) -> str:
    """URL pública real desde la que llega la petición.

    Detrás del proxy de Cloud Run (o cualquier reverse proxy) el host y el
    esquema reales viajan en las cabeceras X-Forwarded-*. Así el enlace de
    reset apunta siempre al despliegue actual sin depender de configurar
    APP_PUBLIC_URL a mano.

    Solo se acepta el origen si está en la lista blanca de CORS: el Host es un
    dato que el cliente puede falsear (host header injection), y este link va
    en un email de recuperación de contraseña. Si no está permitido, se
    devuelve "" para que el llamador caiga en el APP_PUBLIC_URL configurado."""
    host = request.headers.get("x-forwarded-host") or request.headers.get("host")
    if not host:
        return ""
    proto = request.headers.get("x-forwarded-proto", request.url.scheme or "https")
    # Con varios proxies encadenados las cabeceras pueden traer lista "a, b".
    host = host.split(",")[0].strip()
    proto = proto.split(",")[0].strip()
    origen = f"{proto}://{host}"
    permitidos = {o.rstrip("/") for o in config.CORS_ORIGINS} | {config.APP_PUBLIC_URL}
    return origen if origen in permitidos else ""


@router.post("/api/password-reset/request")
def password_reset_request(request: Request, datos: dict = Body(default={})):
    solicitar_reset_password(datos.get("email", ""), base_url=_base_url_publica(request))
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
