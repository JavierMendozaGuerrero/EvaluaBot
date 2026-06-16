import json
import logging
import os
import urllib.parse
from http.server import BaseHTTPRequestHandler
from socketserver import TCPServer

from . import config
from .notion_service import listar_bbdd_evaluados
from .reports import generar_archivo_trayectoria, generar_archivos_informe
from .slack_bot import enviar_revision_pendiente, preguntas_revision_html
from .state import pendientes_revision
from .users import (
    autenticar_usuario,
    crear_sesion,
    obtener_sesion_por_token,
    registrar_usuario,
    validar_acceso_sesion,
    validar_admin_sesion,
)
from .utils import normalizar_nombre, slug_archivo


class ApiHandler(BaseHTTPRequestHandler):
    def log_message(self, *args, **kwargs):
        pass

    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", config.FRONTEND_ORIGIN)
        self.send_header("Access-Control-Allow-Credentials", "true")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(204)
        self.end_headers()

    def leer_json(self):
        longitud = int(self.headers.get("Content-Length", "0"))
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
                if sesion.get("is_admin"):
                    opciones.append({"value": "__todas__", "label": "Todas las personas"})
                for bbdd in sorted(listar_bbdd_evaluados(), key=lambda item: item["evaluado"].lower()):
                    if not sesion.get("is_admin") and normalizar_nombre(bbdd["evaluado"]) != normalizar_nombre(sesion.get("persona")):
                        continue
                    opciones.append({"value": bbdd["evaluado"], "label": bbdd["evaluado"]})
                self.responder_json({"evaluados": opciones})
                return
            if ruta == "/api/revision-pendiente":
                sesion = self.sesion_actual()
                validar_admin_sesion(sesion)
                self.responder_json(
                    {
                        "preguntasHtml": preguntas_revision_html(),
                        "pendientes": [
                            {"id": key, **value}
                            for key, value in pendientes_revision.items()
                        ],
                    }
                )
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
                registrar_usuario(datos.get("username", ""), datos.get("password", ""), datos.get("adminCode", ""))
                self.responder_json({"ok": True})
                return
            if ruta == "/api/login":
                usuario = autenticar_usuario(datos.get("username", ""), datos.get("password", ""))
                token = crear_sesion(usuario)
                self.responder_json({"token": token, "user": obtener_sesion_por_token(token)})
                return

            sesion = self.sesion_actual()
            if not sesion:
                raise PermissionError("Inicia sesión para acceder.")
            if ruta == "/api/generar":
                evaluado = datos.get("evaluado", "__todas__")
                validar_acceso_sesion(sesion, evaluado)
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
                evaluado = datos.get("evaluado", "__todas__")
                validar_acceso_sesion(sesion, evaluado)
                total, slug = generar_archivo_trayectoria(evaluado)
                self.responder_json({"total": total, "htmlUrl": self.url_archivo(f"trayectoria_{slug}.html", evaluado)})
                return
            if ruta == "/api/revision-pendiente/enviar":
                validar_admin_sesion(sesion)
                enviar_revision_pendiente(datos.get("pendingId", ""))
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
        validar_acceso_sesion(sesion, evaluado)
        nombre_autorizado = "Todas las personas" if evaluado == "__todas__" else evaluado
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
    with TCPServer(("", config.PUERTO_WEB), ApiHandler) as httpd:
        logging.info(f"API backend disponible en http://localhost:{config.PUERTO_WEB}")
        httpd.serve_forever()
