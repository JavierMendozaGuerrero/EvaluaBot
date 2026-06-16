import hashlib
import hmac
import json
import os
import secrets

from . import config
from .state import sesiones_web
from .utils import normalizar_nombre


def _ruta_usuarios():
    return os.path.join(config.CARPETA_WEB, "users.json")


def cargar_usuarios():
    ruta = _ruta_usuarios()
    if not os.path.exists(ruta):
        return {}
    with open(ruta, "r", encoding="utf-8") as f:
        return json.load(f)


def guardar_usuarios(usuarios):
    os.makedirs(config.CARPETA_WEB, exist_ok=True)
    with open(_ruta_usuarios(), "w", encoding="utf-8") as f:
        json.dump(usuarios, f, ensure_ascii=False, indent=2)


def hash_password(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 120000)
    return salt, digest.hex()


def verificar_password(password, salt, password_hash):
    _, digest = hash_password(password, salt)
    return hmac.compare_digest(digest, password_hash)


def registrar_usuario(username, password, admin_code):
    username = " ".join((username or "").split()).strip()
    if not username or not password:
        raise ValueError("Usuario y contraseña son obligatorios.")

    usuarios = cargar_usuarios()
    clave = normalizar_nombre(username)
    if clave in usuarios:
        raise ValueError("Ese usuario ya existe.")

    es_admin = normalizar_nombre(username) == normalizar_nombre(config.ADMIN_NAME) and bool(config.ADMIN_ACCESS_CODE) and admin_code == config.ADMIN_ACCESS_CODE
    salt, password_hash = hash_password(password)
    usuarios[clave] = {
        "username": username,
        "persona": username,
        "is_admin": es_admin,
        "salt": salt,
        "password_hash": password_hash,
    }
    guardar_usuarios(usuarios)


def autenticar_usuario(username, password):
    usuario = cargar_usuarios().get(normalizar_nombre(username))
    if not usuario or not verificar_password(password, usuario["salt"], usuario["password_hash"]):
        raise PermissionError("Usuario o contraseña incorrectos.")
    return usuario


def crear_sesion(usuario):
    token = secrets.token_urlsafe(32)
    sesiones_web[token] = {
        "username": usuario["username"],
        "persona": usuario["persona"],
        "is_admin": bool(usuario.get("is_admin")),
    }
    return token


def obtener_cookie(headers, nombre):
    cookie = headers.get("Cookie", "")
    for parte in cookie.split(";"):
        if "=" not in parte:
            continue
        clave, valor = parte.strip().split("=", 1)
        if clave == nombre:
            return valor
    return ""


def obtener_sesion(headers):
    return sesiones_web.get(obtener_cookie(headers, "session"))


def validar_acceso_sesion(sesion, evaluado):
    if not sesion:
        raise PermissionError("Inicia sesión para acceder.")
    if sesion.get("is_admin"):
        return
    if evaluado == "__todas__":
        raise PermissionError("Solo Ana puede generar informes globales.")
    if normalizar_nombre(sesion.get("persona")) != normalizar_nombre(evaluado):
        raise PermissionError("Solo puedes ver las evaluaciones hechas sobre ti.")


def validar_admin_sesion(sesion):
    if not sesion or not sesion.get("is_admin"):
        raise PermissionError("Solo Ana puede revisar y enviar evaluaciones.")
