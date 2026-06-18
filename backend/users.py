import hashlib
import hmac
import json
import logging
import os
import secrets
import smtplib
import tempfile
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

from . import config
from .clients import notion
from .notion_service import (
    _crear_pagina_en_bbdd,
    _data_source_id,
    _extraer_titulo_bbdd,
    _parent_bbdd_referencia,
    _query_bbdd,
    _tipo_objeto_busqueda_bbdd,
    _usa_data_sources,
    obtener_registros_empleados,
)
from .state import lock, password_reset_tokens, sesiones_web
from .utils import normalizar_nombre


_cache_users_database_id = None


def _ruta_usuarios():
    return os.path.join(config.CARPETA_WEB, "users.json")


def _propiedades_usuarios():
    return {
        "Name": {"title": {}},
        "Username": {"rich_text": {}},
        "Persona": {"rich_text": {}},
        "Email": {"email": {}},
        "Is admin": {"checkbox": {}},
        "Salt": {"rich_text": {}},
        "Password hash": {"rich_text": {}},
        "Fecha alta": {"date": {}},
    }


def _texto_rich_text(propiedades, nombre_propiedad):
    items = propiedades.get(nombre_propiedad, {}).get("rich_text", [])
    return "".join(item.get("plain_text", "") for item in items).strip()


def _texto_title(propiedades, nombre_propiedad):
    items = propiedades.get(nombre_propiedad, {}).get("title", [])
    return "".join(item.get("plain_text", "") for item in items).strip()


def _texto_email(propiedades, nombre_propiedad):
    return (propiedades.get(nombre_propiedad, {}).get("email") or "").strip()


def _asegurar_propiedades_usuarios(database_id):
    necesarias = _propiedades_usuarios()
    if _usa_data_sources():
        bbdd = notion.data_sources.retrieve(data_source_id=database_id)
        faltantes = {k: v for k, v in necesarias.items() if k not in bbdd.get("properties", {})}
        if faltantes:
            notion.data_sources.update(data_source_id=database_id, properties=faltantes)
        return

    bbdd = notion.databases.retrieve(database_id=database_id)
    faltantes = {k: v for k, v in necesarias.items() if k not in bbdd.get("properties", {})}
    if faltantes:
        notion.databases.update(database_id=database_id, properties=faltantes)


def _obtener_o_crear_bbdd_usuarios():
    global _cache_users_database_id
    if _cache_users_database_id:
        return _cache_users_database_id

    if config.NOTION_USERS_DATABASE_ID:
        database_id = config.NOTION_USERS_DATABASE_ID.strip().replace("-", "")
        if _usa_data_sources():
            try:
                notion.data_sources.retrieve(data_source_id=database_id)
            except Exception:
                database_id = _data_source_id(notion.databases.retrieve(database_id=database_id))
        _asegurar_propiedades_usuarios(database_id)
        _cache_users_database_id = database_id
        return database_id

    titulo = config.NOTION_USERS_DATABASE_NAME
    parent = _parent_bbdd_referencia()
    resultado = notion.search(
        query=titulo,
        filter={"value": _tipo_objeto_busqueda_bbdd(), "property": "object"},
        page_size=100,
    )
    for bbdd in resultado.get("results", []):
        if _extraer_titulo_bbdd(bbdd) == titulo:
            database_id = _data_source_id(bbdd)
            _asegurar_propiedades_usuarios(database_id)
            _cache_users_database_id = database_id
            return database_id

    if _usa_data_sources():
        nueva = notion.databases.create(
            parent=parent,
            title=[{"type": "text", "text": {"content": titulo}}],
            initial_data_source={
                "title": [{"type": "text", "text": {"content": titulo}}],
                "properties": _propiedades_usuarios(),
            },
        )
        nueva = notion.databases.retrieve(database_id=nueva["id"])
    else:
        nueva = notion.databases.create(
            parent=parent,
            title=[{"type": "text", "text": {"content": titulo}}],
            properties=_propiedades_usuarios(),
        )

    database_id = _data_source_id(nueva)
    _asegurar_propiedades_usuarios(database_id)
    _cache_users_database_id = database_id
    logging.info("Base de usuarios web creada en Notion: %s", titulo)
    return database_id


def _cargar_usuarios_local():
    ruta = _ruta_usuarios()
    if not os.path.exists(ruta):
        return {}
    with open(ruta, "r", encoding="utf-8") as f:
        return json.load(f)


def _guardar_usuarios_local(usuarios):
    os.makedirs(config.CARPETA_WEB, exist_ok=True)
    ruta = _ruta_usuarios()
    with tempfile.NamedTemporaryFile("w", dir=config.CARPETA_WEB, delete=False, suffix=".tmp", encoding="utf-8") as f:
        json.dump(usuarios, f, ensure_ascii=False, indent=2)
        tmp = f.name
    os.replace(tmp, ruta)


def cargar_usuarios():
    try:
        database_id = _obtener_o_crear_bbdd_usuarios()
        usuarios = {}
        cursor = None
        while True:
            kwargs = {"page_size": 100}
            if cursor:
                kwargs["start_cursor"] = cursor
            resp = _query_bbdd(database_id, **kwargs)
            for pagina in resp.get("results", []):
                props = pagina.get("properties", {})
                username = _texto_rich_text(props, "Username") or _texto_title(props, "Name")
                if not username:
                    continue
                clave = normalizar_nombre(username)
                usuarios[clave] = {
                    "username": username,
                    "persona": _texto_rich_text(props, "Persona") or username,
                    "email": _texto_email(props, "Email"),
                    "is_admin": bool(props.get("Is admin", {}).get("checkbox")),
                    "salt": _texto_rich_text(props, "Salt"),
                    "password_hash": _texto_rich_text(props, "Password hash"),
                    "_page_id": pagina.get("id"),
                }
            if not resp.get("has_more"):
                break
            cursor = resp.get("next_cursor")
        return usuarios
    except Exception:
        logging.exception("No se pudieron cargar usuarios desde Notion; usando users.json local como fallback")
        return _cargar_usuarios_local()


def _pagina_usuario_existente(database_id, clave):
    for usuario in cargar_usuarios().values():
        if normalizar_nombre(usuario.get("username")) == clave:
            return usuario.get("_page_id")
    return None


def guardar_usuario(usuario):
    try:
        database_id = _obtener_o_crear_bbdd_usuarios()
        clave = normalizar_nombre(usuario.get("username"))
        page_id = usuario.get("_page_id") or _pagina_usuario_existente(database_id, clave)
        properties = {
            "Name": {"title": [{"text": {"content": usuario["username"]}}]},
            "Username": {"rich_text": [{"text": {"content": usuario["username"]}}]},
            "Persona": {"rich_text": [{"text": {"content": usuario.get("persona", usuario["username"])}}]},
            "Email": {"email": usuario.get("email") or None},
            "Is admin": {"checkbox": bool(usuario.get("is_admin"))},
            "Salt": {"rich_text": [{"text": {"content": usuario["salt"]}}]},
            "Password hash": {"rich_text": [{"text": {"content": usuario["password_hash"]}}]},
        }
        if page_id:
            notion.pages.update(page_id=page_id, properties=properties)
        else:
            properties["Fecha alta"] = {"date": {"start": datetime.now(timezone.utc).isoformat()}}
            _crear_pagina_en_bbdd(database_id, properties)
        return
    except Exception:
        logging.exception("No se pudo guardar usuario en Notion; actualizando fallback local")
        usuarios = _cargar_usuarios_local()
        usuarios[normalizar_nombre(usuario.get("username"))] = {
            k: v for k, v in usuario.items() if not k.startswith("_")
        }
        _guardar_usuarios_local(usuarios)


def guardar_usuarios(usuarios):
    try:
        database_id = _obtener_o_crear_bbdd_usuarios()
        for clave, usuario in usuarios.items():
            page_id = usuario.get("_page_id") or _pagina_usuario_existente(database_id, clave)
            properties = {
                "Name": {"title": [{"text": {"content": usuario["username"]}}]},
                "Username": {"rich_text": [{"text": {"content": usuario["username"]}}]},
                "Persona": {"rich_text": [{"text": {"content": usuario.get("persona", usuario["username"])}}]},
                "Email": {"email": usuario.get("email") or None},
                "Is admin": {"checkbox": bool(usuario.get("is_admin"))},
                "Salt": {"rich_text": [{"text": {"content": usuario["salt"]}}]},
                "Password hash": {"rich_text": [{"text": {"content": usuario["password_hash"]}}]},
            }
            if page_id:
                notion.pages.update(page_id=page_id, properties=properties)
            else:
                properties["Fecha alta"] = {"date": {"start": datetime.now(timezone.utc).isoformat()}}
                _crear_pagina_en_bbdd(database_id, properties)
        return
    except Exception:
        logging.exception("No se pudieron guardar usuarios en Notion; usando users.json local como fallback")
        _guardar_usuarios_local(usuarios)


def hash_password(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 120000)
    return salt, digest.hex()


def verificar_password(password, salt, password_hash):
    _, digest = hash_password(password, salt)
    return hmac.compare_digest(digest, password_hash)


def validar_password_segura(password):
    if len(password or "") < 8:
        raise ValueError("La contraseña debe tener al menos 8 caracteres.")
    if not any(char.isupper() for char in password):
        raise ValueError("La contraseña debe incluir al menos una mayúscula.")
    if not any(not char.isalnum() for char in password):
        raise ValueError("La contraseña debe incluir al menos un caracter especial.")


def _buscar_usuario_por_email_empleado(usuarios, email):
    email_clave = normalizar_nombre(email)
    if "@" not in email_clave:
        return None
    try:
        for empleado in obtener_registros_empleados():
            valores = [
                empleado.get("email", ""),
                *empleado.get("aliases", []),
            ]
            if not any(normalizar_nombre(valor) == email_clave for valor in valores if valor):
                continue
            persona_clave = normalizar_nombre(empleado.get("nombre"))
            return next(
                (u for u in usuarios.values() if normalizar_nombre(u.get("persona")) == persona_clave),
                None,
            )
    except Exception:
        logging.exception("No se pudo resolver usuario por email desde la lista de empleados")
    return None


def _usuario_por_login_o_email(login):
    usuarios = cargar_usuarios()
    usuario = usuarios.get(normalizar_nombre(login))
    if not usuario:
        login_clave = normalizar_nombre(login)
        usuario = next(
            (u for u in usuarios.values() if normalizar_nombre(u.get("email")) == login_clave),
            None,
        )
    if not usuario:
        usuario = _buscar_usuario_por_email_empleado(usuarios, login)
    return usuarios, usuario


def _enviar_email_reset(destinatario, reset_url):
    if not config.SMTP_HOST or not config.SMTP_FROM:
        raise RuntimeError("Falta configurar SMTP_HOST y SMTP_FROM para enviar correos.")

    mensaje = EmailMessage()
    mensaje["Subject"] = "Restablece tu contraseña"
    mensaje["From"] = config.SMTP_FROM
    mensaje["To"] = destinatario
    mensaje.set_content(
        "Hola,\n\n"
        "Hemos recibido una solicitud para cambiar tu contraseña.\n\n"
        f"Abre este enlace para elegir una nueva contraseña:\n{reset_url}\n\n"
        "El enlace caduca en 30 minutos. Si no has pedido este cambio, puedes ignorar este correo.\n"
    )

    with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=20) as smtp:
        if config.SMTP_USE_TLS:
            smtp.starttls()
        if config.SMTP_USER or config.SMTP_PASSWORD:
            smtp.login(config.SMTP_USER, config.SMTP_PASSWORD)
        smtp.send_message(mensaje)


def solicitar_reset_password(email):
    email = " ".join((email or "").split()).strip().lower()
    if not email or "@" not in email:
        raise ValueError("Introduce un email valido.")

    usuarios, usuario = _usuario_por_login_o_email(email)
    if not usuario:
        return

    destinatario = usuario.get("email") or email
    token = secrets.token_urlsafe(32)
    with lock:
        password_reset_tokens[token] = {
            "username": usuario["username"],
            "email": destinatario,
            "expires_at": datetime.now(timezone.utc) + timedelta(minutes=30),
        }
    reset_url = f"{config.APP_PUBLIC_URL}/#/reset/{token}"
    _enviar_email_reset(destinatario, reset_url)


def cambiar_password_con_token(token, nueva_password, confirm_password=None):
    token = (token or "").strip()
    if not token or not nueva_password:
        raise ValueError("Token y nueva contraseña son obligatorios.")
    if confirm_password is not None and nueva_password != confirm_password:
        raise ValueError("Las contraseñas no coinciden.")
    validar_password_segura(nueva_password)

    with lock:
        datos = password_reset_tokens.get(token)
        if not datos or datos["expires_at"] < datetime.now(timezone.utc):
            password_reset_tokens.pop(token, None)
            raise PermissionError("El enlace ha caducado o no es valido.")

    usuarios = cargar_usuarios()
    clave = normalizar_nombre(datos["username"])
    usuario = usuarios.get(clave)
    if not usuario:
        raise PermissionError("No se encontro el usuario para este enlace.")

    salt, password_hash = hash_password(nueva_password)
    usuario["salt"] = salt
    usuario["password_hash"] = password_hash
    guardar_usuario(usuario)

    with lock:
        password_reset_tokens.pop(token, None)


def registrar_usuario(username, password, admin_code):
    username = " ".join((username or "").split()).strip()
    if not username or not password:
        raise ValueError("Usuario y contraseña son obligatorios.")
    validar_password_segura(password)

    usuarios = cargar_usuarios()
    clave = normalizar_nombre(username)
    if clave in usuarios:
        raise ValueError("Ese usuario ya existe.")

    es_admin = normalizar_nombre(username) == normalizar_nombre(config.ADMIN_NAME) and bool(config.ADMIN_ACCESS_CODE) and admin_code == config.ADMIN_ACCESS_CODE
    salt, password_hash = hash_password(password)
    usuarios[clave] = {
        "username": username,
        "persona": username,
        "email": "",
        "is_admin": es_admin,
        "salt": salt,
        "password_hash": password_hash,
    }
    guardar_usuarios(usuarios)


def autenticar_usuario(username, password):
    usuarios = cargar_usuarios()
    usuario = usuarios.get(normalizar_nombre(username))
    if not usuario:
        email_clave = normalizar_nombre(username)
        usuario = next(
            (u for u in usuarios.values() if normalizar_nombre(u.get("email")) == email_clave),
            None,
        )
    if not usuario:
        usuario = _buscar_usuario_por_email_empleado(usuarios, username)
    if not usuario or not verificar_password(password, usuario["salt"], usuario["password_hash"]):
        raise PermissionError("Usuario o contraseña incorrectos.")
    return usuario


def crear_sesion(usuario):
    token = secrets.token_urlsafe(32)
    sesiones_web[token] = {
        "username": usuario["username"],
        "persona": usuario["persona"],
        "email": usuario.get("email", ""),
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


def obtener_sesion_por_token(token):
    return sesiones_web.get(token)


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
