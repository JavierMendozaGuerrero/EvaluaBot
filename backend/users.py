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
    _parent_bbdd_en_pagina,
    _query_bbdd,
    _tipo_objeto_busqueda_bbdd,
    _usa_data_sources,
    obtener_registros_empleados,
)
from .state import lock, password_reset_tokens, registros_pendientes, sesiones_expira, sesiones_web
from .utils import normalizar_nombre


_cache_users_database_id = None

# Caducidad de sesión: una sesión inactiva/antigua deja de ser válida pasado este
# tiempo, aunque el token siga circulando. Antes las sesiones vivían para siempre.
SESSION_TTL = timedelta(hours=12)
# Caducidad cuando el usuario marca "Recuérdame" en el login.
SESSION_TTL_REMEMBER = timedelta(days=30)

# Registro: el alta se confirma con un código de 6 dígitos enviado al email del
# empleado, para probar que quien se registra es el dueño de ese buzón.
REGISTRO_CODE_TTL = timedelta(minutes=10)
REGISTRO_MAX_INTENTOS = 5


def _ruta_sesiones():
    return os.path.join(config.CARPETA_WEB, "sesiones.json")


def _hash_token(token):
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


def _guardar_sesiones_local():
    """Persiste las sesiones activas a disco para que sobrevivan a un reinicio
    del backend. Guarda solo el hash del token (nunca el token en claro): si
    este archivo se filtra no sirve para suplantar a nadie, igual que un
    password_hash no sirve para loguearse sin la contraseña."""
    os.makedirs(config.CARPETA_WEB, exist_ok=True)
    ruta = _ruta_sesiones()
    datos = {
        h: {**sesion, "expira": sesiones_expira[h].isoformat()}
        for h, sesion in sesiones_web.items()
        if h in sesiones_expira
    }
    with tempfile.NamedTemporaryFile("w", dir=config.CARPETA_WEB, delete=False, suffix=".tmp", encoding="utf-8") as f:
        json.dump(datos, f, ensure_ascii=False, indent=2)
        tmp = f.name
    os.replace(tmp, ruta)


def _cargar_sesiones_local():
    """Recupera al arrancar el proceso las sesiones persistidas en el último
    guardado, descartando las ya caducadas."""
    ruta = _ruta_sesiones()
    if not os.path.exists(ruta):
        return
    try:
        with open(ruta, "r", encoding="utf-8") as f:
            datos = json.load(f)
    except Exception:
        logging.exception("No se pudieron cargar las sesiones persistidas")
        return
    ahora = datetime.now(timezone.utc)
    with lock:
        for h, sesion in datos.items():
            expira = datetime.fromisoformat(sesion["expira"])
            if expira < ahora:
                continue
            sesiones_expira[h] = expira
            sesiones_web[h] = {k: v for k, v in sesion.items() if k != "expira"}


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
    parent = _parent_bbdd_en_pagina(config.NOTION_DATA_LISTS_PAGE_NAME, crear=True)
    resultado = notion.search(
        query=titulo,
        filter={"value": _tipo_objeto_busqueda_bbdd(), "property": "object"},
        page_size=100,
    )
    for bbdd in resultado.get("results", []):
        if _extraer_titulo_bbdd(bbdd).strip().lower() == titulo.strip().lower():
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


def cargar_usuarios():
    """Los usuarios viven en Notion. Punto: no hay copia local.

    Antes, si Notion fallaba, se caía a un users.json local. Eso hacía más daño que
    bien: ese fichero se quedaba congelado en quien estuviera dado de alta el día que
    se creó, así que un tropiezo de Notion dejaba fuera a todo el mundo menos a esos, y
    a ellos les dejaba entrar. Un fallo de red se convertía en un cambio silencioso de
    quién tiene cuenta. Ahora, si Notion no responde, la petición falla y se ve.
    """
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


def _pagina_usuario_existente(database_id, clave):
    for usuario in cargar_usuarios().values():
        if normalizar_nombre(usuario.get("username")) == clave:
            return usuario.get("_page_id")
    return None


def guardar_usuario(usuario):
    """Si Notion falla, la excepción sube: quien cambia una contraseña tiene que saber
    que no se ha guardado, en vez de creérselo porque fue a parar a un fichero local
    que nadie vuelve a leer."""
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


def guardar_usuarios(usuarios):
    """Ídem que guardar_usuario: si Notion falla, que se entere quien llama."""
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


def _empleado_por_email(email):
    """Devuelve el registro de empleado cuyo email/alias coincide, o None."""
    email_clave = normalizar_nombre(email)
    if "@" not in email_clave:
        return None
    for empleado in obtener_registros_empleados():
        valores = [empleado.get("email", ""), *empleado.get("aliases", [])]
        if any(normalizar_nombre(valor) == email_clave for valor in valores if valor):
            return empleado
    return None


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


def _enviar_email_codigo(destinatario, codigo):
    if not config.SMTP_HOST or not config.SMTP_FROM:
        raise RuntimeError("Falta configurar SMTP_HOST y SMTP_FROM para enviar el código.")

    mensaje = EmailMessage()
    mensaje["Subject"] = "Tu código de verificación"
    mensaje["From"] = config.SMTP_FROM
    mensaje["To"] = destinatario
    mensaje.set_content(
        "Hola,\n\n"
        f"Tu código de verificación para completar el registro es: {codigo}\n\n"
        "El código caduca en 10 minutos. Si no has intentado registrarte, ignora este correo.\n"
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

    # Al cambiar la contraseña cerramos cualquier sesión abierta del usuario.
    invalidar_sesiones_de_usuario(usuario["username"])

    with lock:
        password_reset_tokens.pop(token, None)


def solicitar_registro(username, password, email=""):
    """Paso 1 del alta: valida los datos y envía un código de verificación al email
    del empleado. La cuenta NO se crea hasta confirmar el código (confirmar_registro).
    Devuelve el email de destino (para que el frontend muestre a dónde se envió)."""
    username = " ".join((username or "").split()).strip()
    email = " ".join((email or "").split()).strip().lower()
    if not username or not password:
        raise ValueError("Usuario y contraseña son obligatorios.")
    if not email or "@" not in email:
        raise ValueError("Introduce un email válido.")
    validar_password_segura(password)

    # El alta solo se permite a empleados reales: el email debe estar en la
    # "Lista de empleados" de Notion. Además la identidad (persona) se liga al
    # empleado verificado, no al username elegido — así nadie puede reclamar la
    # identidad (y por tanto los datos) de otra persona poniendo su nombre.
    empleado = _empleado_por_email(email)
    if not empleado:
        raise PermissionError("Ese email no está en la lista de empleados. Contacta con RRHH.")
    persona = (empleado.get("nombre") or username).strip()

    usuarios = cargar_usuarios()
    clave = normalizar_nombre(username)
    if clave in usuarios:
        raise ValueError("Ese usuario ya existe.")
    persona_clave = normalizar_nombre(persona)
    if any(normalizar_nombre(u.get("persona")) == persona_clave for u in usuarios.values()):
        raise ValueError("Ya existe una cuenta para este empleado. Usa «He olvidado mi contraseña».")

    salt, password_hash = hash_password(password)
    codigo = f"{secrets.randbelow(1_000_000):06d}"
    with lock:
        registros_pendientes[normalizar_nombre(email)] = {
            "username": username,
            "persona": persona,
            "email": email,
            "salt": salt,
            "password_hash": password_hash,
            "codigo": codigo,
            "expires_at": datetime.now(timezone.utc) + REGISTRO_CODE_TTL,
            "intentos": 0,
        }
    _enviar_email_codigo(email, codigo)
    return email


def confirmar_registro(email, codigo):
    """Paso 2 del alta: comprueba el código y, si es correcto, crea la cuenta."""
    email_clave = normalizar_nombre(email)
    codigo = (codigo or "").strip()
    with lock:
        pendiente = registros_pendientes.get(email_clave)
        if not pendiente or pendiente["expires_at"] < datetime.now(timezone.utc):
            registros_pendientes.pop(email_clave, None)
            raise PermissionError("El código ha caducado. Vuelve a registrarte.")
        pendiente["intentos"] += 1
        if pendiente["intentos"] > REGISTRO_MAX_INTENTOS:
            registros_pendientes.pop(email_clave, None)
            raise PermissionError("Demasiados intentos. Vuelve a registrarte.")
        if not hmac.compare_digest(codigo, pendiente["codigo"]):
            raise PermissionError("Código incorrecto.")
        datos = dict(pendiente)

    usuarios = cargar_usuarios()
    clave = normalizar_nombre(datos["username"])
    if clave in usuarios:
        with lock:
            registros_pendientes.pop(email_clave, None)
        raise ValueError("Ese usuario ya existe.")
    usuarios[clave] = {
        "username": datos["username"],
        "persona": datos["persona"],
        "email": datos["email"],
        "is_admin": False,
        "salt": datos["salt"],
        "password_hash": datos["password_hash"],
    }
    guardar_usuarios(usuarios)
    with lock:
        registros_pendientes.pop(email_clave, None)


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
    if not usuario:
        # Ejecutamos un hash igualmente para que el tiempo de respuesta no revele
        # si el usuario existe o no (evita enumeración de usuarios por tiempos).
        hash_password(password or "", "timing_dummy_salt")
        raise PermissionError("Usuario o contraseña incorrectos.")
    if not verificar_password(password, usuario["salt"], usuario["password_hash"]):
        raise PermissionError("Usuario o contraseña incorrectos.")
    return usuario


def crear_sesion(usuario, remember=False):
    token = secrets.token_urlsafe(32)
    h = _hash_token(token)
    ttl = SESSION_TTL_REMEMBER if remember else SESSION_TTL
    with lock:
        sesiones_web[h] = {
            "username": usuario["username"],
            "persona": usuario["persona"],
            "email": usuario.get("email", ""),
            "is_admin": bool(usuario.get("is_admin")),
        }
        sesiones_expira[h] = datetime.now(timezone.utc) + ttl
        _guardar_sesiones_local()
    return token


def cerrar_sesion(token):
    """Invalida un token de sesión (logout del lado servidor)."""
    h = _hash_token(token)
    with lock:
        sesiones_web.pop(h, None)
        sesiones_expira.pop(h, None)
        _guardar_sesiones_local()


def invalidar_sesiones_de_usuario(username):
    """Cierra todas las sesiones abiertas de un usuario (p. ej. al cambiar su contraseña)."""
    clave = normalizar_nombre(username)
    with lock:
        tokens = [
            h for h, s in sesiones_web.items()
            if normalizar_nombre(s.get("username")) == clave
        ]
        for h in tokens:
            sesiones_web.pop(h, None)
            sesiones_expira.pop(h, None)
        _guardar_sesiones_local()


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
    if not token:
        return None
    h = _hash_token(token)
    with lock:
        expira = sesiones_expira.get(h)
        if expira is not None and expira < datetime.now(timezone.utc):
            # Sesión caducada: la eliminamos y la tratamos como inexistente.
            sesiones_web.pop(h, None)
            sesiones_expira.pop(h, None)
            _guardar_sesiones_local()
            return None
        return sesiones_web.get(h)


def validar_acceso_sesion(sesion, evaluado, extra_permitidos=None):
    if not sesion:
        raise PermissionError("Inicia sesión para acceder.")
    if sesion.get("is_admin"):
        return
    if normalizar_nombre(sesion.get("persona")) == normalizar_nombre(evaluado):
        return
    if extra_permitidos and normalizar_nombre(evaluado) in [normalizar_nombre(n) for n in extra_permitidos]:
        return
    raise PermissionError("Solo puedes ver las evaluaciones hechas sobre ti.")


# Al arrancar el proceso, recupera las sesiones que sobrevivieron a un reinicio.
_cargar_sesiones_local()
