import html
import logging
import os
import socketserver
import urllib.parse
from http.server import SimpleHTTPRequestHandler

from . import config
from .notion_service import listar_bbdd_evaluados
from .reports import generar_archivo_trayectoria, generar_archivos_informe
from .slack_bot import enviar_revision_pendiente, pendientes_revision_html, preguntas_revision_html
from .users import autenticar_usuario, crear_sesion, obtener_sesion, registrar_usuario, validar_acceso_sesion, validar_admin_sesion
from .utils import normalizar_nombre, slug_archivo


class WebHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=config.CARPETA_WEB, **kwargs)

    def log_message(self, *args, **kwargs):
        pass

    def responder_html(self, contenido, status=200):
        body = contenido.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def redirect(self, destino, cookie=None):
        self.send_response(303)
        self.send_header("Location", destino)
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()

    def servir_archivo(self, nombre_archivo, content_type):
        ruta = os.path.join(config.CARPETA_WEB, os.path.basename(nombre_archivo))
        if not os.path.exists(ruta):
            self.send_error(404)
            return
        with open(ruta, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def servir_archivo_protegido(self, ruta, query):
        datos = urllib.parse.parse_qs(query)
        evaluado = datos.get("evaluado", [""])[0]
        sesion = obtener_sesion(self.headers)
        try:
            validar_acceso_sesion(sesion, evaluado)
        except PermissionError as error:
            self.pagina_error("Acceso denegado", str(error), 403)
            return
        nombre_autorizado = "Todas las personas" if evaluado == "__todas__" else evaluado
        slug = slug_archivo(nombre_autorizado)
        if not (ruta.startswith(f"/informe_{slug}.") or ruta.startswith(f"/trayectoria_{slug}.")):
            self.pagina_error("Acceso denegado", "El archivo solicitado no corresponde con la persona autorizada.", 403)
            return
        content_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document" if ruta.endswith(".docx") else "text/html; charset=utf-8"
        self.servir_archivo(ruta.lstrip("/"), content_type)

    def opciones_evaluados(self):
        sesion = obtener_sesion(self.headers)
        opciones = []
        if sesion and sesion.get("is_admin"):
            opciones.append('<option value="__todas__">Todas las personas</option>')
        for bbdd in sorted(listar_bbdd_evaluados(), key=lambda item: item["evaluado"].lower()):
            if sesion and not sesion.get("is_admin") and normalizar_nombre(bbdd["evaluado"]) != normalizar_nombre(sesion.get("persona")):
                continue
            nombre_texto = html.escape(bbdd["evaluado"])
            nombre_valor = html.escape(bbdd["evaluado"], quote=True)
            opciones.append(f'<option value="{nombre_valor}">{nombre_texto}</option>')
        return "\n".join(opciones) or '<option value="">No hay tabla disponible</option>'

    def pagina_error(self, titulo, mensaje, status=500):
        self.responder_html(f"""<!DOCTYPE html><html lang="es"><head><meta charset="utf-8"><title>{html.escape(titulo)}</title></head>
<body style="font-family:Arial,sans-serif;max-width:720px;margin:48px auto;line-height:1.6"><h1>{html.escape(titulo)}</h1><p>{html.escape(mensaje)}</p><p><a href="/">Volver</a></p></body></html>""", status)

    def pagina_login(self, mensaje=""):
        extra = f"<p class='error'>{html.escape(mensaje)}</p>" if mensaje else ""
        self.responder_html(f"""<!DOCTYPE html><html lang="es"><head><meta charset="utf-8"><title>Login</title><style>{config.IGENERIS_CSS}.login-wrap{{max-width:980px;margin:0 auto}}.auth-form{{max-width:420px}}</style></head>
<body><main class="page login-wrap"><nav class="nav"><a class="brand" href="/login">igeneris</a><div class="nav-links"><a href="/register">Registro</a></div></nav>
<section class="hero"><div><p class="kicker">Evaluaciones internas</p><h1>Accede a tus informes.</h1><p>Una herramienta privada para consultar feedback, trayectoria e informes.</p></div>
<form class="auth-form panel" method="post" action="/login"><h2>Entrar</h2>{extra}<label>Usuario</label><input name="username" required><label>Contraseña</label><input name="password" type="password" required><div class="actions"><button type="submit">Entrar</button><a class="button secondary" href="/register">Crear cuenta</a></div></form></section></main></body></html>""")

    def pagina_registro(self, mensaje=""):
        extra = f"<p class='error'>{html.escape(mensaje)}</p>" if mensaje else ""
        self.responder_html(f"""<!DOCTYPE html><html lang="es"><head><meta charset="utf-8"><title>Registro</title><style>{config.IGENERIS_CSS}.login-wrap{{max-width:980px;margin:0 auto}}.auth-form{{max-width:460px}}</style></head>
<body><main class="page login-wrap"><nav class="nav"><a class="brand" href="/login">igeneris</a><div class="nav-links"><a href="/login">Login</a></div></nav>
<section class="hero"><div><p class="kicker">Nuevo acceso</p><h1>Registra tu usuario.</h1><p>Tu usuario determina qué tabla puedes consultar. Ana puede activar permisos con su clave.</p></div>
<form class="auth-form panel" method="post" action="/register"><h2>Registro</h2>{extra}<label>Usuario</label><input name="username" required><label>Contraseña</label><input name="password" type="password" required><label>Clave admin</label><input name="admin_code" type="password" placeholder="Solo Ana"><div class="actions"><button type="submit">Crear cuenta</button><a class="button secondary" href="/login">Ya tengo cuenta</a></div></form></section></main></body></html>""")

    def pagina_home(self):
        sesion = obtener_sesion(self.headers)
        if not sesion:
            self.redirect("/login")
            return
        opciones = self.opciones_evaluados()
        usuario = html.escape(sesion["username"])
        rol = "Admin" if sesion.get("is_admin") else f"Solo {html.escape(sesion.get('persona', ''))}"
        revision = ""
        if sesion.get("is_admin"):
            revision = f"""<section class="review panel"><p class="kicker">Revisión antes de enviar</p><h2>Evaluación de Slack</h2><p>Estas son las preguntas que se enviarán.</p><ol>{preguntas_revision_html()}</ol>{pendientes_revision_html()}</section>"""
        self.responder_html(f"""<!DOCTYPE html><html lang="es"><head><meta charset="utf-8"><title>Evaluaciones</title><style>{config.IGENERIS_CSS}.tools{{display:grid;grid-template-columns:repeat(2,minmax(260px,1fr));gap:26px;margin-top:34px}}.tool{{border-top:1px solid var(--ink);padding-top:18px}}.review{{margin-top:38px}}.loading{{position:fixed;inset:0;display:none;place-items:center;background:rgba(9,14,22,.72);color:white;padding:24px}}.loading.visible{{display:grid}}</style></head>
<body><main class="page"><nav class="nav"><a class="brand" href="/">igeneris</a><div class="nav-links"><span>{usuario}</span><span>{rol}</span><a href="/logout">Cerrar sesión</a></div></nav>
<section class="hero"><div><p class="kicker">People analytics</p><h1>Centro de evaluaciones.</h1></div><div class="panel"><p>Genera informes y trayectorias visuales a partir del feedback guardado en Notion.</p><p class="fine">Ana puede consultar todo. Cada persona ve únicamente su tabla.</p></div></section>
<section class="tools"><form class="tool" method="post" action="/generar" data-loading="Comprobando caché y generando informe si hace falta"><h2>Informe</h2><p>Reutiliza el último informe si no hay cambios; si hay nuevas evaluaciones, Claude lo actualiza.</p><label>Persona evaluada</label><select name="evaluado">{opciones}</select><div class="actions"><button type="submit">Generar informe</button></div></form>
<form class="tool" method="post" action="/trayectoria" data-loading="Preparando trayectoria visual"><h2>Trayectoria</h2><p>Una experiencia visual para navegar el feedback por fecha, proyecto y satisfacción.</p><label>Persona evaluada</label><select name="evaluado">{opciones}</select><div class="actions"><button class="secondary" type="submit">Generar trayectoria</button></div></form></section>{revision}
<div id="loading" class="loading"><div><h2 id="loading-title">Preparando</h2><p>Esto puede tardar unos segundos.</p></div></div><script>for(const form of document.querySelectorAll("form")){{form.addEventListener("submit",()=>{{document.getElementById("loading-title").textContent=form.dataset.loading||"Procesando";document.getElementById("loading").classList.add("visible");}})}}</script></main></body></html>""")

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        ruta = parsed.path
        if ruta == "/login":
            self.pagina_login(); return
        if ruta == "/register":
            self.pagina_registro(); return
        if ruta == "/logout":
            self.redirect("/login", "session=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax"); return
        if ruta.lower() == "/users.json":
            self.send_error(404); return
        if ruta.startswith("/informe") or ruta.startswith("/trayectoria"):
            self.servir_archivo_protegido(ruta, parsed.query); return
        if ruta in ("/", "/index.html"):
            self.pagina_home(); return
        return super().do_GET()

    def do_POST(self):
        ruta = urllib.parse.urlparse(self.path).path
        if ruta not in ("/login", "/register", "/generar", "/trayectoria", "/enviar_pendiente"):
            self.send_error(404); return
        try:
            longitud = min(int(self.headers.get("Content-Length", "0")), 1_000_000)
            datos = urllib.parse.parse_qs(self.rfile.read(longitud).decode("utf-8") if longitud else "")
            if ruta == "/register":
                try:
                    registrar_usuario(datos.get("username", [""])[0], datos.get("password", [""])[0], datos.get("admin_code", [""])[0])
                except Exception as error:
                    self.pagina_registro(str(error)); return
                self.redirect("/login"); return
            if ruta == "/login":
                try:
                    usuario = autenticar_usuario(datos.get("username", [""])[0], datos.get("password", [""])[0])
                except PermissionError as error:
                    self.pagina_login(str(error)); return
                self.redirect("/", f"session={crear_sesion(usuario)}; Path=/; HttpOnly; SameSite=Lax"); return

            sesion = obtener_sesion(self.headers)
            if ruta == "/enviar_pendiente":
                validar_admin_sesion(sesion)
                enviar_revision_pendiente(datos.get("pending_id", [""])[0])
                self.responder_html('<h1>Evaluación enviada</h1><p>La primera pregunta se ha enviado a Slack.</p><p><a href="/">Volver</a></p>'); return

            evaluado = datos.get("evaluado", ["__todas__"])[0]
            validar_acceso_sesion(sesion, evaluado)
            query = urllib.parse.urlencode({"evaluado": evaluado})
            if ruta == "/generar":
                total, slug, desde_cache = generar_archivos_informe(evaluado)
                titulo = "Informe reutilizado" if desde_cache else "Informe generado"
                detalle = f"No había evaluaciones nuevas. Se reutilizó el informe anterior con {total} evaluaciones." if desde_cache else f"Claude ha analizado {total} evaluaciones."
                enlaces = f'<p><a href="/informe_{slug}.html?{query}">Ver informe web</a></p><p><a href="/informe_{slug}.docx?{query}">Descargar documento Word</a></p>'
            else:
                total, slug = generar_archivo_trayectoria(evaluado)
                titulo = "Trayectoria generada"
                detalle = f"Se han preparado {total} evaluaciones para navegar por fechas."
                enlaces = f'<p><a href="/trayectoria_{slug}.html?{query}">Ver trayectoria React</a></p>'
            self.responder_html(f'<h1>{titulo}</h1><p>{detalle}</p>{enlaces}<p><a href="/">Volver</a></p>')
        except PermissionError as error:
            self.pagina_error("Acceso denegado", str(error), 403)
        except Exception as error:
            logging.exception("Error generando archivo desde la web")
            self.pagina_error("No se pudo completar la acción", str(error), 500)


def iniciar_servidor_web():
    os.makedirs(config.CARPETA_WEB, exist_ok=True)
    with socketserver.TCPServer(("", config.PUERTO_WEB), WebHandler) as httpd:
        logging.info(f"Web disponible en http://localhost:{config.PUERTO_WEB}")
        httpd.serve_forever()
