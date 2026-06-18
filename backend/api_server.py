import json
import logging
import os
import urllib.parse
from http.server import BaseHTTPRequestHandler
from socketserver import TCPServer

from . import config
from .notion_service import (
    listar_bbdd_evaluados,
    obtener_advisees,
    obtener_datos_empleados_por_nombres,
    obtener_opiniones_ca_por_advisee,
    listar_advisees_con_opiniones_ca,
    guardar_objetivos,
    obtener_objetivos,
)
from .reports import generar_archivo_trayectoria, generar_archivos_informe
from .users import (
    autenticar_usuario,
    cambiar_password_con_token,
    crear_sesion,
    obtener_sesion_por_token,
    registrar_usuario,
    solicitar_reset_password,
    validar_acceso_sesion,
)
from .utils import normalizar_nombre, slug_archivo


class ReusableTCPServer(TCPServer):
    allow_reuse_address = True


class ApiHandler(BaseHTTPRequestHandler):
    def log_message(self, *args, **kwargs):
        pass

    def end_headers(self):
        origen = self.headers.get("Origin", "")
        origenes_permitidos = {
            config.FRONTEND_ORIGIN,
            "http://localhost:5173",
            "http://127.0.0.1:5173",
        }
        self.send_header("Access-Control-Allow-Origin", origen if origen in origenes_permitidos else config.FRONTEND_ORIGIN)
        self.send_header("Access-Control-Allow-Credentials", "true")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(204)
        self.end_headers()

    def leer_json(self):
        longitud = min(int(self.headers.get("Content-Length", "0")), 1_000_000)
        if not longitud:
            return {}
        return json.loads(self.rfile.read(longitud).decode("utf-8"))

    def responder_json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def sesion_actual(self):
        auth = self.headers.get("Authorization", "")
        if auth.lower().startswith("bearer "):
            return obtener_sesion_por_token(auth.split(" ", 1)[1].strip())
        token_query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get("token", [""])[0]
        return obtener_sesion_por_token(token_query)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        ruta = parsed.path
        try:
            if ruta == "/api/health":
                self.responder_json({"ok": True})
                return
            if ruta == "/api/me":
                sesion = self.sesion_actual()
                if not sesion:
                    self.responder_json({"user": None})
                    return
                self.responder_json({"user": sesion})
                return
            if ruta == "/api/evaluados":
                sesion = self.sesion_actual()
                if not sesion:
                    raise PermissionError("Inicia sesión para acceder.")
                opciones = []
                for bbdd in sorted(listar_bbdd_evaluados(), key=lambda item: item["evaluado"].lower()):
                    if not sesion.get("is_admin") and normalizar_nombre(bbdd["evaluado"]) != normalizar_nombre(sesion.get("persona")):
                        continue
                    opciones.append({"value": bbdd["evaluado"], "label": bbdd["evaluado"]})
                self.responder_json({"evaluados": opciones})
                return
            if ruta == "/api/mis-advisees":
                sesion = self.sesion_actual()
                if not sesion:
                    raise PermissionError("Inicia sesión para acceder.")
                ca_nombre = sesion.get("persona", "")
                ca_aliases = [sesion.get("username", ""), sesion.get("email", "")]
                advisee_nombres = [
                    *obtener_advisees(ca_nombre, ca_aliases=ca_aliases),
                    *listar_advisees_con_opiniones_ca(ca_nombre, ca_aliases=ca_aliases),
                ]
                vistos = set()
                advisee_nombres = [
                    nombre for nombre in advisee_nombres
                    if nombre and not (normalizar_nombre(nombre) in vistos or vistos.add(normalizar_nombre(nombre)))
                ]
                advisees = obtener_datos_empleados_por_nombres(advisee_nombres)
                self.responder_json({"advisees": advisees})
                return
            if ruta == "/api/opiniones-ca":
                sesion = self.sesion_actual()
                if not sesion:
                    raise PermissionError("Inicia sesión para acceder.")
                query_params = urllib.parse.parse_qs(parsed.query)
                advisee = query_params.get("advisee", [""])[0]
                ca_nombre = sesion.get("persona", "")
                opiniones = obtener_opiniones_ca_por_advisee(
                    ca_nombre,
                    advisee,
                    ca_aliases=[sesion.get("username", ""), sesion.get("email", "")],
                )
                self.responder_json({"opiniones": opiniones})
                return
            if ruta == "/api/objetivos":
                sesion = self.sesion_actual()
                if not sesion:
                    raise PermissionError("Inicia sesión para acceder.")
                query_params = urllib.parse.parse_qs(parsed.query)
                nombre = query_params.get("nombre", [""])[0]
                objetivos = obtener_objetivos(nombre)
                self.responder_json({"objetivos": objetivos})
                return
            if ruta.startswith("/api/files/"):
                self.servir_archivo_protegido(ruta.removeprefix("/api/files/"), parsed.query)
                return
            self.responder_json({"error": "No encontrado"}, 404)
        except PermissionError as error:
            self.responder_json({"error": str(error)}, 403)
        except Exception as error:
            logging.exception("Error en API GET")
            self.responder_json({"error": str(error)}, 500)

    def do_POST(self):
        ruta = urllib.parse.urlparse(self.path).path
        try:
            datos = self.leer_json()
            if ruta == "/api/register":
                registrar_usuario(datos.get("username", ""), datos.get("password", ""))
                self.responder_json({"ok": True})
                return
            if ruta == "/api/login":
                usuario = autenticar_usuario(datos.get("username", ""), datos.get("password", ""))
                token = crear_sesion(usuario)
                self.responder_json({"token": token, "user": obtener_sesion_por_token(token)})
                return
            if ruta == "/api/password-reset/request":
                solicitar_reset_password(datos.get("email", ""))
                self.responder_json({"ok": True})
                return
            if ruta == "/api/password-reset/confirm":
                cambiar_password_con_token(datos.get("token", ""), datos.get("password", ""), datos.get("confirmPassword"))
                self.responder_json({"ok": True})
                return

            sesion = self.sesion_actual()
            if not sesion:
                raise PermissionError("Inicia sesión para acceder.")
            if ruta == "/api/generar":
                evaluado = datos.get("evaluado", "")
                advisees_ca = obtener_advisees(
                    sesion.get("persona", ""),
                    ca_aliases=[sesion.get("username", ""), sesion.get("email", "")],
                )
                validar_acceso_sesion(sesion, evaluado, extra_permitidos=advisees_ca)
                total, slug, desde_cache = generar_archivos_informe(evaluado)
                self.responder_json(
                    {
                        "total": total,
                        "desdeCache": desde_cache,
                        "htmlUrl": self.url_archivo(f"informe_{slug}.html", evaluado),
                        "docxUrl": self.url_archivo(f"informe_{slug}.docx", evaluado),
                    }
                )
                return
            if ruta == "/api/trayectoria":
                evaluado = datos.get("evaluado", "")
                advisees_ca = obtener_advisees(
                    sesion.get("persona", ""),
                    ca_aliases=[sesion.get("username", ""), sesion.get("email", "")],
                )
                validar_acceso_sesion(sesion, evaluado, extra_permitidos=advisees_ca)
                total, slug = generar_archivo_trayectoria(evaluado)
                self.responder_json({"total": total, "htmlUrl": self.url_archivo(f"trayectoria_{slug}.html", evaluado)})
                return
            if ruta == "/api/objetivos":
                ca_nombre = sesion.get("persona", "")
                nombre = datos.get("nombre", "")
                texto = datos.get("objetivos", "").strip()
                if not nombre or not texto:
                    self.responder_json({"error": "Faltan campos obligatorios."}, 400)
                    return
                guardar_objetivos(ca_nombre, nombre, texto)
                self.responder_json({"ok": True})
                return
            self.responder_json({"error": "No encontrado"}, 404)
        except PermissionError as error:
            self.responder_json({"error": str(error)}, 403)
        except Exception as error:
            logging.exception("Error en API POST")
            self.responder_json({"error": str(error)}, 500)

    def url_archivo(self, nombre_archivo, evaluado):
        query = urllib.parse.urlencode({"evaluado": evaluado})
        return f"/api/files/{urllib.parse.quote(nombre_archivo)}?{query}"

    def servir_archivo_protegido(self, nombre_archivo, query):
        datos = urllib.parse.parse_qs(query)
        evaluado = datos.get("evaluado", [""])[0]
        sesion = self.sesion_actual()
        advisees_ca = obtener_advisees(
            sesion.get("persona", "") if sesion else "",
            ca_aliases=[sesion.get("username", ""), sesion.get("email", "")] if sesion else [],
        )
        validar_acceso_sesion(sesion, evaluado, extra_permitidos=advisees_ca)
        nombre_autorizado = evaluado
        slug = slug_archivo(nombre_autorizado)
        if not (nombre_archivo.startswith(f"informe_{slug}.") or nombre_archivo.startswith(f"trayectoria_{slug}.")):
            raise PermissionError("El archivo solicitado no corresponde con la persona autorizada.")
        ruta = os.path.join(config.CARPETA_WEB, os.path.basename(nombre_archivo))
        if not os.path.exists(ruta):
            self.responder_json({"error": "Archivo no encontrado"}, 404)
            return
        content_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document" if nombre_archivo.endswith(".docx") else "text/html; charset=utf-8"
        with open(ruta, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def iniciar_api_backend():
    os.makedirs(config.CARPETA_WEB, exist_ok=True)
    try:
        with ReusableTCPServer(("", config.PUERTO_WEB), ApiHandler) as httpd:
            logging.info(f"API backend disponible en http://localhost:{config.PUERTO_WEB}")
            httpd.serve_forever()
    except OSError as error:
        logging.error(
            "No se pudo iniciar la API en http://localhost:%s. "
            "Ese puerto parece estar ocupado. Cierra el otro proceso o arranca con: "
            '$env:PUERTO_WEB="8001"; python bot.py',
            config.PUERTO_WEB,
        )
        logging.debug("Detalle del error al iniciar la API", exc_info=True)
