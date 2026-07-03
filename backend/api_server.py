import cgi
import gzip
import io
import json
import logging
import os
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

try:
    import mammoth
except ImportError:
    mammoth = None

from datetime import datetime, timedelta, timezone

from . import config
from .i18n import t
from .notion_service import (
    listar_bbdd_evaluados,
    obtener_advisees,
    obtener_datos_empleados_por_nombres,
    obtener_opiniones_ca_por_advisee,
    listar_advisees_con_opiniones_ca,
    guardar_objetivo_persona,
    obtener_objetivos_persona,
    eliminar_objetivo_persona,
    guardar_informe_final,
    obtener_informe_final_reciente,
    obtener_ca_de_empleado,
    ca_tiene_acceso_activo,
    toggle_acceso_advisees,
    advisee_tiene_acceso_individual,
    toggle_acceso_advisee_individual,
    obtener_perfil_empleado,
    obtener_registros_empleados,
    idioma_de_persona,
    idioma_por_sesion,
    guardar_idioma_por_sesion,
    guardar_pais_por_sesion,
    obtener_paises_disponibles,
    obtener_todos_los_advisees,
    evaluacion_proyecto_guardada_desde,
    evaluacion_personal_guardada_desde,
    obtener_config_calendario,
    siguiente_envio_calendario,
    guardar_evaluacion_personal,
    guardar_en_notion,
    actualizar_en_notion,
    buscar_empleado_y_cargo,
    obtener_preguntas_desde_notion,
    obtener_preguntas_mo,
    obtener_preguntas_palantir,
    obtener_evaluados_middleoffice,
    sugerir_empleados_parecidos,
    obtener_historial_mis_evaluaciones,
    obtener_criterios_evaluacion,
    obtener_feedback_confidencial_por_evaluado,
    obtener_todo_el_feedback_confidencial,
)
from .hierarchy import comparar_jerarquia, tipo_relacion
from .project_evals import (
    obtener_proyectos_activos_empleado,
    obtener_equipo_proyecto,
    obtener_preguntas_tipo,
    activar_evaluaciones_empleados,
    guardar_evaluacion_proyecto,
    obtener_proyectos_manager,
    obtener_estado_evaluaciones_proyecto,
    enviar_recordatorios_proyecto,
    añadir_miembro_proyecto,
    eliminar_miembro_proyecto,
    obtener_evals_completadas_proyecto,
    LABELS_TIPOS,
)
from .evaluaciones_extra import (
    solicitar_evaluacion_extra,
    obtener_solicitudes_pendientes,
    guardar_evaluacion_extra,
    obtener_evaluaciones_extra_por_evaluado,
)
from .ca_reviews import guardar_nota_ca_web, notificar_acceso_informe_final_web, obtener_resumen_advisee_para_ca
from .eval_tracking import resumen_ciclo_actual, detalle_por_persona
from .personal_eval import notificar_urgencia_personal_web
from .reports import generar_archivo_trayectoria, generar_archivos_informe
from .skill_informes_anual import generar_informe_anual, obtener_empleados_evaluacion_anual
from . import eval_anual_sesion as eval_sesion
from .skill_opiniones_ca import generar_resumen_opiniones_ca
from .skill_pdfs_fuentes import (
    generar_pdf_evals_proyecto,
    generar_pdf_seguimiento_personal,
    generar_pdf_evals_mensuales,
    generar_pdf_completo,
    generar_pdf_evals_extra,
)
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
from .anonimato import cargar_config as cargar_anonimato, guardar_config as guardar_anonimato, evaluadores_visibles_para_advisee

PAISES_PERMITIDOS = ("España", "México", "Portugal")


class ReusableTCPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


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
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(204)
        self.end_headers()

    def leer_json(self):
        longitud = min(int(self.headers.get("Content-Length", "0")), 1_000_000)
        if not longitud:
            return {}
        return json.loads(self.rfile.read(longitud).decode("utf-8"))

    def leer_multipart(self):
        longitud = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(longitud)
        environ = {
            "REQUEST_METHOD": "POST",
            "CONTENT_TYPE": self.headers.get("Content-Type", ""),
            "CONTENT_LENGTH": str(longitud),
        }
        return cgi.FieldStorage(fp=io.BytesIO(body), headers=self.headers, environ=environ)

    def responder_json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        usar_gzip = "gzip" in self.headers.get("Accept-Encoding", "") and len(body) > 1024
        if usar_gzip:
            body = gzip.compress(body, 5)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        if usar_gzip:
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Vary", "Accept-Encoding")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def sesion_actual(self):
        auth = self.headers.get("Authorization", "")
        if auth.lower().startswith("bearer "):
            return obtener_sesion_por_token(auth.split(" ", 1)[1].strip())
        token_query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get("token", [""])[0]
        return obtener_sesion_por_token(token_query)

    @staticmethod
    def _aliases_sesion(sesion):
        email = sesion.get("email", "")
        return [sesion.get("username", ""), email, email.split("@")[0] if email else ""]

    def _exigir_acceso_advisee(self, sesion, evaluado):
        """Lanza PermissionError si el CA de la sesión no tutela a `evaluado` (admin pasa siempre)."""
        if sesion.get("is_admin"):
            return
        advisees_ca = obtener_advisees(
            sesion.get("persona", ""),
            ca_aliases=[sesion.get("username", ""), sesion.get("email", "")],
        )
        if normalizar_nombre(evaluado) not in [normalizar_nombre(a) for a in advisees_ca]:
            raise PermissionError("Solo el CA de esta persona (o un administrador) puede acceder.")

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
                usuario = {**sesion, "idioma": idioma_por_sesion(sesion)}
                self.responder_json({"user": usuario})
                return
            if ruta == "/api/evaluados":
                sesion = self.sesion_actual()
                if not sesion:
                    raise PermissionError("Inicia sesión para acceder.")
                if sesion.get("is_admin"):
                    registros = obtener_registros_empleados()
                    opciones = sorted(
                        [{"value": r["nombre"], "label": r["nombre"], "foto": r.get("foto", "")} for r in registros if r.get("nombre")],
                        key=lambda o: o["label"].lower(),
                    )
                else:
                    opciones = []
                    for bbdd in sorted(listar_bbdd_evaluados(), key=lambda item: item["evaluado"].lower()):
                        if normalizar_nombre(bbdd["evaluado"]) != normalizar_nombre(sesion.get("persona")):
                            continue
                        opciones.append({"value": bbdd["evaluado"], "label": bbdd["evaluado"]})
                    datos = obtener_datos_empleados_por_nombres([o["value"] for o in opciones])
                    fotos = {normalizar_nombre(d["nombre"]): d.get("foto", "") for d in datos}
                    for o in opciones:
                        o["foto"] = fotos.get(normalizar_nombre(o["value"]), "")
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
                _cfg_anon = cargar_anonimato()
                if not (sesion.get("is_admin") or evaluadores_visibles_para_advisee(advisee, _cfg_anon)):
                    for op in opiniones:
                        op["resumen_advisee"] = ""
                self.responder_json({"opiniones": opiniones})
                return
            if ruta == "/api/mi-perfil":
                sesion = self.sesion_actual()
                if not sesion:
                    raise PermissionError("Inicia sesión para acceder.")
                perfil = obtener_perfil_empleado(sesion.get("persona", ""))
                self.responder_json(perfil)
                return
            if ruta == "/api/paises":
                sesion = self.sesion_actual()
                if not sesion:
                    raise PermissionError("Inicia sesión para acceder.")
                self.responder_json({"paises": obtener_paises_disponibles()})
                return
            if ruta == "/api/perfil-empleado":
                sesion = self.sesion_actual()
                if not sesion:
                    raise PermissionError("Inicia sesión para acceder.")
                if not sesion.get("is_admin"):
                    raise PermissionError("Solo administradores pueden consultar perfiles de empleados.")
                query_params = urllib.parse.parse_qs(parsed.query)
                nombre = query_params.get("nombre", [""])[0]
                perfil = obtener_perfil_empleado(nombre)
                self.responder_json(perfil)
                return
            if ruta == "/api/cumplimiento-evaluaciones":
                sesion = self.sesion_actual()
                if not sesion:
                    raise PermissionError("Inicia sesión para acceder.")
                if not sesion.get("is_admin"):
                    raise PermissionError("Solo administradores pueden consultar el cumplimiento de evaluaciones.")
                self.responder_json({"cumplimiento": resumen_ciclo_actual()})
                return
            if ruta == "/api/cumplimiento-evaluaciones-detalle":
                sesion = self.sesion_actual()
                if not sesion:
                    raise PermissionError("Inicia sesión para acceder.")
                if not sesion.get("is_admin"):
                    raise PermissionError("Solo administradores pueden consultar el cumplimiento de evaluaciones.")
                query_params = urllib.parse.parse_qs(parsed.query)
                nombre = query_params.get("nombre", [""])[0]
                if not nombre:
                    self.responder_json({"error": "Falta el parámetro nombre."}, 400)
                    return
                self.responder_json({"detalle": detalle_por_persona(nombre)})
                return
            if ruta == "/api/feedback-confidencial":
                sesion = self.sesion_actual()
                if not sesion:
                    raise PermissionError("Inicia sesión para acceder.")
                if not sesion.get("is_admin"):
                    raise PermissionError("Solo administradores pueden acceder a este contenido.")
                query_params = urllib.parse.parse_qs(parsed.query)
                evaluado = query_params.get("evaluado", [""])[0]
                if not evaluado:
                    self.responder_json({"error": "Falta el parámetro evaluado."}, 400)
                    return
                try:
                    feedback = obtener_feedback_confidencial_por_evaluado(evaluado)
                except RuntimeError:
                    feedback = []
                self.responder_json({"feedback": feedback})
                return
            if ruta == "/api/feedback-confidencial-todos":
                sesion = self.sesion_actual()
                if not sesion:
                    raise PermissionError("Inicia sesión para acceder.")
                if not sesion.get("is_admin"):
                    raise PermissionError("Solo administradores pueden acceder a este contenido.")
                self.responder_json({"feedback": obtener_todo_el_feedback_confidencial()})
                return
            if ruta == "/api/criterios-evaluacion":
                sesion = self.sesion_actual()
                if not sesion:
                    raise PermissionError("Inicia sesión para acceder.")
                query_params = urllib.parse.parse_qs(parsed.query)
                grupo = query_params.get("grupo", ["negocio"])[0]
                _GRUPO_NOTION = {"negocio": "Negocio", "palantir": "Palantir", "middleoffice": "MiddleOffice"}
                notion_grupo = _GRUPO_NOTION.get(grupo, grupo)
                criterios = obtener_criterios_evaluacion(notion_grupo, idioma_por_sesion(sesion))
                self.responder_json({"criterios": criterios})
                return
            if ruta == "/api/objetivos":
                sesion = self.sesion_actual()
                if not sesion:
                    raise PermissionError("Inicia sesión para acceder.")
                query_params = urllib.parse.parse_qs(parsed.query)
                nombre = query_params.get("nombre", [""])[0]
                objetivos = obtener_objetivos_persona(nombre)
                self.responder_json({"objetivos": objetivos})
                return
            if ruta == "/api/evaluados-anual":
                sesion = self.sesion_actual()
                if not sesion:
                    raise PermissionError("Inicia sesión para acceder.")
                if not sesion.get("is_admin"):
                    raise PermissionError("Solo administradores pueden acceder a las evaluaciones anuales.")
                nombres = obtener_empleados_evaluacion_anual()
                self.responder_json({"evaluados": [{"value": n, "label": n} for n in nombres]})
                return
            if ruta == "/api/acceso-advisees":
                sesion = self.sesion_actual()
                if not sesion:
                    raise PermissionError("Inicia sesión para acceder.")
                ca_aliases_sesion = [sesion.get("username", ""), sesion.get("email", "")]
                activo = ca_tiene_acceso_activo(sesion.get("persona", ""), ca_aliases=ca_aliases_sesion)
                self.responder_json({"activo": activo})
                return
            if ruta == "/api/acceso-advisee-individual":
                sesion = self.sesion_actual()
                if not sesion:
                    raise PermissionError("Inicia sesión para acceder.")
                query_params = urllib.parse.parse_qs(parsed.query)
                advisee_nombre = query_params.get("advisee", [""])[0]
                if not advisee_nombre:
                    self.responder_json({"error": "Falta el parámetro advisee."}, 400)
                    return
                activo = advisee_tiene_acceso_individual(advisee_nombre, sesion.get("persona", ""))
                self.responder_json({"activo": activo})
                return
            if ruta == "/api/informe-final":
                sesion = self.sesion_actual()
                if not sesion:
                    raise PermissionError("Inicia sesión para acceder.")
                query_params = urllib.parse.parse_qs(parsed.query)
                evaluado = query_params.get("evaluado", [""])[0]
                ca_nombre = sesion.get("persona", "")
                advisees_ca = obtener_advisees(
                    ca_nombre,
                    ca_aliases=[sesion.get("username", ""), sesion.get("email", "")],
                )
                es_admin = sesion.get("is_admin", False)
                es_ca = normalizar_nombre(evaluado) in [normalizar_nombre(a) for a in advisees_ca]
                es_propio = normalizar_nombre(evaluado) == normalizar_nombre(ca_nombre)
                if es_propio and not es_admin and not es_ca:
                    ca_del_evaluado = obtener_ca_de_empleado(evaluado)
                    acceso = bool(ca_del_evaluado and (
                        ca_tiene_acceso_activo(ca_del_evaluado)
                        or advisee_tiene_acceso_individual(evaluado, ca_del_evaluado)
                    ))
                    informe = obtener_informe_final_reciente(evaluado) if acceso else None
                    if acceso and informe:
                        self.responder_json({
                            "disponible": True,
                            "accesoActivo": True,
                            "docxUrl": self.url_archivo(informe["docx"], evaluado),
                            "htmlUrl": self.url_archivo(informe["html"], evaluado) if informe.get("html") else None,
                        })
                    elif acceso:
                        self.responder_json({"disponible": False, "accesoActivo": True, "mensaje": "Tu CA aún no ha subido tu informe final."})
                    else:
                        self.responder_json({"disponible": False, "accesoActivo": False, "mensaje": "Tu CA aún no ha publicado tu informe final."})
                    return
                if not es_admin and not es_ca:
                    raise PermissionError("No tienes permiso para ver este informe.")
                informe = obtener_informe_final_reciente(evaluado)
                if not informe:
                    self.responder_json({"disponible": False, "mensaje": "No hay informe final disponible."})
                    return
                self.responder_json({
                    "disponible": True,
                    "docxUrl": self.url_archivo(informe["docx"], evaluado),
                    "htmlUrl": self.url_archivo(informe["html"], evaluado) if informe.get("html") else None,
                })
                return
            if ruta == "/api/evaluaciones-proyecto-activas":
                sesion = self.sesion_actual()
                if not sesion:
                    raise PermissionError("Inicia sesión para acceder.")
                persona = sesion.get("persona", "")
                proyectos = obtener_proyectos_activos_empleado(persona)
                self.responder_json({"proyectos": proyectos})
                return
            if ruta == "/api/evaluaciones-proyecto-completadas":
                sesion = self.sesion_actual()
                if not sesion:
                    raise PermissionError("Inicia sesión para acceder.")
                query_params = urllib.parse.parse_qs(parsed.query)
                proyecto = query_params.get("proyecto", [""])[0]
                if not proyecto:
                    self.responder_json({"error": "Falta el parámetro proyecto."}, 400)
                    return
                persona = sesion.get("persona", "")
                completadas = obtener_evals_completadas_proyecto(persona, proyecto)
                self.responder_json({"completadas": completadas})
                return
            if ruta == "/api/todos-empleados":
                sesion = self.sesion_actual()
                if not sesion:
                    raise PermissionError("Inicia sesión para acceder.")
                try:
                    registros = obtener_registros_empleados()
                    empleados = sorted(
                        [r["nombre"] for r in registros if r.get("nombre")],
                        key=lambda n: n.lower(),
                    )
                except Exception:
                    empleados = []
                self.responder_json({"empleados": empleados})
                return
            if ruta == "/api/preguntas-evaluacion-proyecto":
                sesion = self.sesion_actual()
                if not sesion:
                    raise PermissionError("Inicia sesión para acceder.")
                query_params = urllib.parse.parse_qs(parsed.query)
                tipo = query_params.get("tipo", [""])[0]
                if tipo not in LABELS_TIPOS:
                    self.responder_json({"error": "Tipo no válido."}, 400)
                    return
                preguntas = obtener_preguntas_tipo(tipo, idioma_por_sesion(sesion))
                self.responder_json({"preguntas": preguntas})
                return
            if ruta == "/api/equipo-proyecto":
                sesion = self.sesion_actual()
                if not sesion:
                    raise PermissionError("Inicia sesión para acceder.")
                query_params = urllib.parse.parse_qs(parsed.query)
                proyecto = query_params.get("proyecto", [""])[0]
                empleados = obtener_equipo_proyecto(proyecto) if proyecto else []
                self.responder_json({"empleados": empleados})
                return
            if ruta == "/api/proyectos-manager":
                sesion = self.sesion_actual()
                if not sesion:
                    raise PermissionError("Inicia sesión para acceder.")
                persona = sesion.get("persona", "")
                proyectos = obtener_proyectos_manager(persona)
                self.responder_json({"proyectos": proyectos})
                return
            if ruta == "/api/estado-proyecto":
                sesion = self.sesion_actual()
                if not sesion:
                    raise PermissionError("Inicia sesión para acceder.")
                query_params = urllib.parse.parse_qs(parsed.query)
                proyecto = query_params.get("proyecto", [""])[0]
                if not proyecto:
                    self.responder_json({"error": "Falta el proyecto."}, 400)
                    return
                estado = obtener_estado_evaluaciones_proyecto(proyecto)
                cfg_anon = cargar_anonimato()
                es_admin = sesion.get("is_admin", False)
                for m in estado:
                    m["n_pendientes"] = len(m["pendientes"])
                    if not (es_admin or evaluadores_visibles_para_advisee(m.get("evaluado", ""), cfg_anon)):
                        m["evaluadores"] = []
                        m["pendientes"] = []
                self.responder_json({"estado": estado})
                return
            if ruta == "/api/evaluaciones-extra-pendientes":
                sesion = self.sesion_actual()
                if not sesion:
                    raise PermissionError("Inicia sesión para acceder.")
                persona = sesion.get("persona", "")
                pendientes = obtener_solicitudes_pendientes(persona)
                self.responder_json({"pendientes": pendientes})
                return
            if ruta == "/api/evaluaciones-extra-recibidas":
                sesion = self.sesion_actual()
                if not sesion:
                    raise PermissionError("Inicia sesión para acceder.")
                query_params = urllib.parse.parse_qs(parsed.query)
                evaluado = query_params.get("evaluado", [""])[0]
                if not evaluado:
                    self.responder_json({"error": "Falta el parámetro evaluado."}, 400)
                    return
                self._exigir_acceso_advisee(sesion, evaluado)
                evaluaciones = obtener_evaluaciones_extra_por_evaluado(evaluado)
                self.responder_json({"evaluaciones": evaluaciones})
                return
            if ruta.startswith("/api/eval-anual/"):
                sesion = self.sesion_actual()
                if not sesion:
                    raise PermissionError("Inicia sesión para acceder.")
                qp = urllib.parse.parse_qs(parsed.query)
                evaluado = qp.get("evaluado", [""])[0].strip()
                if not evaluado:
                    self.responder_json({"error": "Falta el parámetro evaluado."}, 400)
                    return
                self._exigir_acceso_advisee(sesion, evaluado)
                if ruta == "/api/eval-anual/estado":
                    self.responder_json(eval_sesion.estado_sesion(evaluado))
                    return
                if ruta == "/api/eval-anual/area":
                    clave = qp.get("clave", [""])[0].strip()
                    self.responder_json(eval_sesion.obtener_area(evaluado, clave))
                    return
                self.responder_json({"error": "Ruta no encontrada."}, 404)
                return
            if ruta == "/api/anonimato-evaluadores":
                sesion = self.sesion_actual()
                if not sesion or not sesion.get("is_admin"):
                    raise PermissionError("Solo administradores.")
                self.responder_json(cargar_anonimato())
                return
            if ruta.startswith("/api/files/"):
                self.servir_archivo_protegido(ruta.removeprefix("/api/files/"), parsed.query)
                return
            if ruta == "/api/estado-ciclo-slack":
                sesion = self.sesion_actual()
                if not sesion:
                    raise PermissionError("Inicia sesión para acceder.")
                persona = sesion.get("persona", "")
                completadas = {"proyecto": False, "personal": False}
                _fallback_5w = (datetime.now(timezone.utc) - timedelta(weeks=5)).timestamp()
                try:
                    cal = obtener_config_calendario()
                    fecha_proyecto = cal.get("proyecto_ca")
                    if fecha_proyecto:
                        siguiente = siguiente_envio_calendario(fecha_proyecto, 4)
                        ultimo = siguiente - timedelta(weeks=4)
                        completadas["proyecto"] = evaluacion_proyecto_guardada_desde(persona, ultimo.timestamp())
                    else:
                        completadas["proyecto"] = evaluacion_proyecto_guardada_desde(persona, _fallback_5w)
                    fecha_personal = cal.get("personal")
                    if fecha_personal:
                        siguiente_p = siguiente_envio_calendario(fecha_personal, 4)
                        ultimo_p = siguiente_p - timedelta(weeks=4)
                        completadas["personal"] = evaluacion_personal_guardada_desde(persona, ultimo_p.timestamp())
                    else:
                        completadas["personal"] = evaluacion_personal_guardada_desde(persona, _fallback_5w)
                except Exception:
                    logging.exception("Error comprobando estado ciclo slack")
                    completadas["proyecto"] = evaluacion_proyecto_guardada_desde(persona, _fallback_5w)
                    completadas["personal"] = evaluacion_personal_guardada_desde(persona, _fallback_5w)
                self.responder_json({
                    "cicloActivo": True,
                    "completadas": completadas,
                })
                return
            if ruta == "/api/buscar-empleado-slack":
                sesion = self.sesion_actual()
                if not sesion:
                    raise PermissionError("Inicia sesión para acceder.")
                query_params = urllib.parse.parse_qs(parsed.query)
                nombre_busqueda = query_params.get("nombre", [""])[0]
                area = query_params.get("area", ["negocio"])[0].lower()
                persona = sesion.get("persona", "")
                # MiddleOffice sin nombre → devuelve evaluables + preguntas
                if area == "middleoffice" and not nombre_busqueda:
                    mo_evaluables = obtener_evaluados_middleoffice(persona)
                    self.responder_json({
                        "moEvaluables": mo_evaluables,
                        "preguntas": obtener_preguntas_mo(idioma_por_sesion(sesion)),
                    })
                    return
                if not nombre_busqueda:
                    self.responder_json({"error": "Falta el nombre."}, 400)
                    return
                empleado, cargo_evaluado = buscar_empleado_y_cargo(nombre_busqueda)
                if area == "middleoffice" and not empleado:
                    # Para MO, el nombre puede no estar en la lista general pero sí en MO
                    mo_evaluables = obtener_evaluados_middleoffice(persona)
                    for mo_e in mo_evaluables:
                        if normalizar_nombre(mo_e) == normalizar_nombre(nombre_busqueda):
                            empleado = mo_e
                            break
                if not empleado:
                    self.responder_json({"empleado": None, "sugerencias": sugerir_empleados_parecidos(nombre_busqueda)})
                    return
                evaluador_perfil = obtener_perfil_empleado(persona)
                cargo_evaluador = evaluador_perfil.get("cargo", "")
                relacion = comparar_jerarquia(cargo_evaluador, cargo_evaluado or "")
                tipo = tipo_relacion(relacion)
                _idi = idioma_por_sesion(sesion)
                if area == "middleoffice":
                    preguntas = obtener_preguntas_mo(_idi)
                elif area == "palantir":
                    preguntas = obtener_preguntas_palantir(tipo, _idi)
                else:
                    pn = obtener_preguntas_desde_notion(tipo, _idi)
                    nocion_q1 = pn.get("q1", "")
                    def _es_default(t):
                        return not t or t.startswith("Este mes") or "Puedes considerar claridad" in t
                    if _es_default(nocion_q1):
                        sujeto = "del Project Leader" if relacion == "inferior" else f"de {empleado}"
                        texto_q1 = f"¿Cómo valorarías del 1 al 5 la contribución {sujeto} al buen avance del proyecto?"
                    elif "{nombre}" in nocion_q1:
                        nombre_resuelto = empleado if relacion != "inferior" else "el Project Leader"
                        texto_q1 = nocion_q1.replace("{nombre}", nombre_resuelto)
                    else:
                        texto_q1 = nocion_q1
                    preguntas = [
                        {"clave": "q1", "texto": texto_q1},
                        {"clave": "q2", "texto": pn.get("q2") or "Indica un ejemplo concreto que justifique tu valoración"},
                    ]
                self.responder_json({"empleado": empleado, "relacion": relacion, "preguntas": preguntas})
                return
            if ruta == "/api/resumen-evaluaciones-advisee":
                sesion = self.sesion_actual()
                if not sesion:
                    raise PermissionError("Inicia sesión para acceder.")
                query_params = urllib.parse.parse_qs(parsed.query)
                advisee = query_params.get("advisee", [""])[0]
                if not advisee:
                    self.responder_json({"error": "Falta el advisee."}, 400)
                    return
                ca_nombre = sesion.get("persona", "")
                ca_aliases = [sesion.get("username", ""), sesion.get("email", "")]
                advisees_ca = list({
                    *obtener_advisees(ca_nombre, ca_aliases=ca_aliases),
                    *listar_advisees_con_opiniones_ca(ca_nombre, ca_aliases=ca_aliases),
                })
                if normalizar_nombre(advisee) not in [normalizar_nombre(a) for a in advisees_ca]:
                    raise PermissionError("No tienes acceso a las evaluaciones de este advisee.")
                resumen, sin_novedades = obtener_resumen_advisee_para_ca(ca_nombre, advisee)
                self.responder_json({"resumen": resumen, "sinNovedades": sin_novedades})
                return
            if ruta == "/api/historial-evaluaciones":
                sesion = self.sesion_actual()
                if not sesion:
                    raise PermissionError("Inicia sesión para acceder.")
                query_params = urllib.parse.parse_qs(parsed.query)
                evaluado = query_params.get("evaluado", [""])[0]
                evaluador = query_params.get("evaluador", [""])[0]
                proyecto = query_params.get("proyecto", [""])[0]
                if not evaluado or not evaluador:
                    self.responder_json({"error": "Faltan parámetros."}, 400)
                    return
                historial = obtener_historial_mis_evaluaciones(evaluado, evaluador, proyecto)
                self.responder_json({"historial": historial})
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
            ct = self.headers.get("Content-Type", "")
            if "multipart/form-data" in ct:
                form = self.leer_multipart()
                datos = {}
            else:
                datos = self.leer_json()
                form = None
            if ruta == "/api/notas-ca":
                sesion = self.sesion_actual()
                if not sesion:
                    raise PermissionError("Inicia sesión para acceder.")
                advisee_nombre = datos.get("advisee", "").strip()
                nota = datos.get("nota", "").strip()
                if not advisee_nombre or not nota:
                    self.responder_json({"error": "Faltan datos"}, 400)
                    return
                ca_nombre = sesion.get("persona", "")
                ok, err = guardar_nota_ca_web(ca_nombre, advisee_nombre, nota)
                self.responder_json({"ok": ok, "error": err})
                return
            if ruta == "/api/register":
                registrar_usuario(datos.get("username", ""), datos.get("password", ""))
                self.responder_json({"ok": True})
                return
            if ruta == "/api/login":
                usuario = autenticar_usuario(datos.get("username", ""), datos.get("password", ""))
                token = crear_sesion(usuario)
                sesion = obtener_sesion_por_token(token)
                sesion_con_idioma = {**sesion, "idioma": idioma_por_sesion(sesion)}
                self.responder_json({"token": token, "user": sesion_con_idioma})
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
            if ruta == "/api/set-idioma":
                idioma = (datos.get("idioma") or "").strip().lower()
                if idioma not in ("es", "en"):
                    idioma = "es"
                ok = guardar_idioma_por_sesion(sesion, idioma)
                self.responder_json({"ok": bool(ok), "idioma": idioma})
                return
            if ruta == "/api/set-pais":
                pais = (datos.get("pais") or "").strip()[:80]
                if pais not in PAISES_PERMITIDOS:
                    raise ValueError("País no permitido.")
                guardado = guardar_pais_por_sesion(sesion, pais)
                self.responder_json({"ok": True, "pais": guardado})
                return
            if ruta == "/api/generar":
                evaluado = datos.get("evaluado", "")
                cargo = datos.get("cargo", "").strip()
                advisees_ca = obtener_advisees(
                    sesion.get("persona", ""),
                    ca_aliases=[sesion.get("username", ""), sesion.get("email", "")],
                )
                if not sesion.get("is_admin") and normalizar_nombre(evaluado) not in [normalizar_nombre(a) for a in advisees_ca]:
                    raise PermissionError("Solo administradores o CAs pueden generar informes.")
                validar_acceso_sesion(sesion, evaluado, extra_permitidos=advisees_ca)
                respuesta = {}
                try:
                    total, slug, desde_cache = generar_archivos_informe(evaluado)
                    respuesta["total"] = total
                    respuesta["desdeCache"] = desde_cache
                except Exception:
                    logging.exception("No se pudo generar el informe HTML para %s", evaluado)
                try:
                    slug_anual = generar_informe_anual(evaluado, cargo=cargo)
                    respuesta["docxAnualUrl"] = self.url_archivo(f"informe_anual_{slug_anual}.docx", evaluado)
                    respuesta["htmlUrl"] = self.url_archivo(f"informe_anual_{slug_anual}.html", evaluado)
                except Exception:
                    logging.exception("No se pudo generar el informe anual IGENERIS para %s", evaluado)
                if not respuesta:
                    raise RuntimeError("No se pudo generar ningún informe para esta persona. No ha recibido evaluaciones.")
                self.responder_json(respuesta)
                return
            if ruta == "/api/generar-opiniones-ca":
                evaluado = (datos.get("evaluado", "") or datos.get("advisee", "")).strip()
                if not evaluado:
                    self.responder_json({"error": "Selecciona un advisee."}, 400)
                    return
                advisees_ca = obtener_advisees(
                    sesion.get("persona", ""),
                    ca_aliases=[sesion.get("username", ""), sesion.get("email", "")],
                )
                if not sesion.get("is_admin") and normalizar_nombre(evaluado) not in [normalizar_nombre(a) for a in advisees_ca]:
                    raise PermissionError("Solo el CA o un administrador pueden generar este documento.")
                validar_acceso_sesion(sesion, evaluado, extra_permitidos=advisees_ca)
                _cfg_anon_op = cargar_anonimato()
                _anonimo_op = not (sesion.get("is_admin") or evaluadores_visibles_para_advisee(evaluado, _cfg_anon_op))
                slug = generar_resumen_opiniones_ca(evaluado, anonimo=_anonimo_op, idioma=idioma_de_persona(evaluado))
                self.responder_json({
                    "pdfUrl": self.url_archivo(f"opiniones_ca_{slug}.pdf", evaluado),
                    "htmlUrl": self.url_archivo(f"opiniones_ca_{slug}.html", evaluado),
                })
                return
            if ruta in ("/api/generar-pdf-evals-proyecto", "/api/generar-pdf-seguimiento", "/api/generar-pdf-evals-mensuales", "/api/generar-pdf-completo", "/api/generar-pdf-evals-extra"):
                evaluado = (datos.get("evaluado", "") or datos.get("advisee", "")).strip()
                if not evaluado:
                    self.responder_json({"error": "Selecciona un advisee."}, 400)
                    return
                self._exigir_acceso_advisee(sesion, evaluado)
                _cfg_anon = cargar_anonimato()
                _anonimo = not (sesion.get("is_admin") or evaluadores_visibles_para_advisee(evaluado, _cfg_anon))
                _GEN = {
                    "/api/generar-pdf-evals-proyecto": (generar_pdf_evals_proyecto, "evals_proyecto"),
                    "/api/generar-pdf-seguimiento": (generar_pdf_seguimiento_personal, "seguimiento_personal"),
                    "/api/generar-pdf-evals-mensuales": (generar_pdf_evals_mensuales, "evals_mensuales"),
                    "/api/generar-pdf-completo": (generar_pdf_completo, "info_completa"),
                    "/api/generar-pdf-evals-extra": (generar_pdf_evals_extra, "evals_extra"),
                }
                generador, prefijo = _GEN[ruta]
                anonimo_ruta = False if ruta in ("/api/generar-pdf-evals-proyecto", "/api/generar-pdf-evals-extra") else _anonimo
                slug = generador(evaluado, anonimo=anonimo_ruta, idioma=idioma_de_persona(evaluado))
                self.responder_json({"pdfUrl": self.url_archivo(f"{prefijo}_{slug}.pdf", evaluado)})
                return
            if ruta == "/api/trayectoria":
                evaluado = datos.get("evaluado", "")
                advisees_ca = obtener_advisees(
                    sesion.get("persona", ""),
                    ca_aliases=[sesion.get("username", ""), sesion.get("email", "")],
                )
                es_propio_tray = normalizar_nombre(evaluado) == normalizar_nombre(sesion.get("persona", ""))
                if not sesion.get("is_admin") and normalizar_nombre(evaluado) not in [normalizar_nombre(a) for a in advisees_ca]:
                    if not es_propio_tray:
                        raise PermissionError("Solo administradores o CAs pueden generar informes.")
                    ca_tray = obtener_ca_de_empleado(evaluado)
                    if not (ca_tray and (ca_tiene_acceso_activo(ca_tray) or advisee_tiene_acceso_individual(evaluado, ca_tray))):
                        raise PermissionError("Tu CA aún no ha publicado tu informe final.")
                validar_acceso_sesion(sesion, evaluado, extra_permitidos=advisees_ca)
                total, slug = generar_archivo_trayectoria(evaluado)
                self.responder_json({"total": total, "htmlUrl": self.url_archivo(f"trayectoria_{slug}.html", evaluado)})
                return
            if ruta == "/api/objetivos":
                ca_nombre = sesion.get("persona", "")
                nombre = datos.get("nombre", "")
                titulo = datos.get("titulo", "").strip()
                kpis = datos.get("kpis", "").strip()
                descripcion = datos.get("descripcion", "").strip()
                tipo = datos.get("tipo", "").strip()
                if not nombre or not titulo:
                    self.responder_json({"error": "Faltan campos obligatorios (nombre y título)."}, 400)
                    return
                guardar_objetivo_persona(ca_nombre, nombre, titulo, kpis, descripcion, tipo)
                self.responder_json({"ok": True})
                return
            if ruta == "/api/generar-anual":
                if not sesion.get("is_admin"):
                    raise PermissionError("Solo administradores pueden generar informes anuales.")
                evaluado = datos.get("evaluado", "").strip()
                cargo = datos.get("cargo", "").strip()
                if not evaluado:
                    self.responder_json({"error": "Selecciona un empleado."}, 400)
                    return
                slug = generar_informe_anual(evaluado, cargo=cargo)
                self.responder_json({
                    "docxUrl": self.url_archivo(f"informe_anual_{slug}.docx", evaluado),
                    "htmlUrl": self.url_archivo(f"informe_anual_{slug}.html", evaluado),
                })
                return
            if ruta.startswith("/api/eval-anual/"):
                evaluado = (datos.get("evaluado", "") or "").strip()
                if not evaluado:
                    self.responder_json({"error": "Falta el campo evaluado."}, 400)
                    return
                self._exigir_acceso_advisee(sesion, evaluado)
                if ruta == "/api/eval-anual/iniciar":
                    self.responder_json(eval_sesion.iniciar_sesion(evaluado, cargo=datos.get("cargo", "").strip()))
                    return
                if ruta == "/api/eval-anual/confirmar-identidad":
                    self.responder_json(eval_sesion.confirmar_identidad(evaluado))
                    return
                if ruta == "/api/eval-anual/eliminar":
                    self.responder_json(eval_sesion.eliminar_sesion(evaluado))
                    return
                if ruta == "/api/eval-anual/responder-area":
                    self.responder_json(eval_sesion.responder_area(
                        evaluado, datos.get("clave", "").strip(), datos.get("texto", "")))
                    return
                if ruta == "/api/eval-anual/confirmar-area":
                    self.responder_json(eval_sesion.confirmar_area(
                        evaluado, datos.get("clave", "").strip()))
                    return
                if ruta == "/api/eval-anual/finalizar":
                    res = eval_sesion.finalizar_sesion(evaluado)
                    slug_fin = slug_archivo(evaluado)
                    res["htmlUrl"] = self.url_archivo(f"informe_anual_{slug_fin}.html", evaluado)
                    res["docxUrl"] = self.url_archivo(f"informe_anual_{slug_fin}.docx", evaluado)
                    self.responder_json(res)
                    return
                self.responder_json({"error": "Ruta no encontrada."}, 404)
                return
            if ruta == "/api/acceso-advisees":
                activo = datos.get("activo", False)
                ca_aliases_sesion = [sesion.get("username", ""), sesion.get("email", "")]
                exito = toggle_acceso_advisees(sesion.get("persona", ""), activo, ca_aliases=ca_aliases_sesion)
                if not exito:
                    raise RuntimeError("No se encontró tu fila en Lista CA. Contacta con el administrador.")
                self.responder_json({"ok": True, "activo": activo})
                return
            if ruta == "/api/acceso-advisee-individual":
                advisee_nombre = datos.get("advisee", "")
                activo = datos.get("activo", False)
                if not advisee_nombre:
                    self.responder_json({"error": "Falta el campo advisee."}, 400)
                    return
                exito = toggle_acceso_advisee_individual(sesion.get("persona", ""), advisee_nombre, activo)
                if not exito:
                    raise RuntimeError("No se pudo actualizar el acceso individual.")
                if activo:
                    notificar_acceso_informe_final_web(advisee_nombre)
                self.responder_json({"ok": True, "activo": activo})
                return
            if ruta == "/api/subir-informe-final":
                if form is None:
                    self.responder_json({"error": "Se esperaba multipart/form-data."}, 400)
                    return
                evaluado_subida = form.getvalue("evaluado", "")
                archivo_field = form["archivo"] if "archivo" in form else None
                if not evaluado_subida or archivo_field is None:
                    self.responder_json({"error": "Faltan campos: evaluado y archivo."}, 400)
                    return
                advisees_ca = obtener_advisees(
                    sesion.get("persona", ""),
                    ca_aliases=[sesion.get("username", ""), sesion.get("email", "")],
                )
                if not sesion.get("is_admin") and normalizar_nombre(evaluado_subida) not in [normalizar_nombre(a) for a in advisees_ca]:
                    raise PermissionError("Solo puedes subir informes para tus advisees.")
                slug_ev = slug_archivo(evaluado_subida)
                ts = int(time.time())
                docx_filename = f"informe_final_{slug_ev}_{ts}.docx"
                docx_path = os.path.join(config.CARPETA_WEB, docx_filename)
                with open(docx_path, "wb") as f:
                    f.write(archivo_field.file.read())
                html_filename = ""
                if mammoth:
                    try:
                        html_filename = f"informe_final_{slug_ev}_{ts}.html"
                        html_path = os.path.join(config.CARPETA_WEB, html_filename)
                        with open(docx_path, "rb") as df:
                            resultado_html = mammoth.convert_to_html(df)
                        with open(html_path, "w", encoding="utf-8") as hf:
                            hf.write(resultado_html.value)
                    except Exception:
                        logging.exception("Error convirtiendo docx a HTML")
                        html_filename = ""
                url_notion = f"{config.APP_PUBLIC_URL}/api/files/{urllib.parse.quote(docx_filename)}?evaluado={urllib.parse.quote(evaluado_subida)}"
                ca_subida = sesion.get("persona", "") if not sesion.get("is_admin") else ""
                guardar_informe_final(
                    ca_nombre=ca_subida,
                    advisee=evaluado_subida,
                    docx_filename=docx_filename,
                    html_filename=html_filename,
                    url=url_notion,
                )
                resp_data: dict = {
                    "ok": True,
                    "docxUrl": self.url_archivo(docx_filename, evaluado_subida),
                }
                if html_filename:
                    resp_data["htmlUrl"] = self.url_archivo(html_filename, evaluado_subida)
                self.responder_json(resp_data)
                return
            if ruta == "/api/activar-evaluaciones-proyecto":
                manager = sesion.get("persona", "")
                _idi = idioma_por_sesion(sesion)
                proyecto = datos.get("proyecto", "").strip()
                empleados = datos.get("empleados", [])
                if not proyecto:
                    self.responder_json({"error": t("pe.err_missing_project", _idi)}, 400)
                    return
                if not empleados or not isinstance(empleados, list):
                    self.responder_json({"error": t("pe.err_select_employee", _idi)}, 400)
                    return
                resultado = activar_evaluaciones_empleados(manager, proyecto, empleados, _idi)
                self.responder_json(resultado)
                return
            if ruta == "/api/modificar-equipo-proyecto":
                manager = sesion.get("persona", "")
                _idi = idioma_por_sesion(sesion)
                accion = datos.get("accion", "").strip()
                proyecto = datos.get("proyecto", "").strip()
                empleado = datos.get("empleado", "").strip()
                if accion not in ("añadir", "eliminar") or not proyecto or not empleado:
                    self.responder_json({"error": t("pe.err_missing_fields", _idi)}, 400)
                    return
                if accion == "añadir":
                    resultado = añadir_miembro_proyecto(manager, proyecto, empleado, _idi)
                else:
                    resultado = eliminar_miembro_proyecto(proyecto, empleado, _idi)
                self.responder_json(resultado)
                return
            if ruta == "/api/recordatorio-proyecto":
                manager = sesion.get("persona", "")
                _idi = idioma_por_sesion(sesion)
                proyecto = datos.get("proyecto", "").strip()
                if not proyecto:
                    self.responder_json({"error": t("pe.err_missing_fields", _idi)}, 400)
                    return
                proyectos_mgr = {p["nombre_proyecto"] for p in obtener_proyectos_manager(manager)}
                if proyecto not in proyectos_mgr:
                    self.responder_json({"error": t("pe.err_not_your_project", _idi)}, 403)
                    return
                resultado = enviar_recordatorios_proyecto(proyecto)
                self.responder_json({"ok": True, **resultado})
                return
            if ruta == "/api/guardar-evaluacion-proyecto":
                evaluador = sesion.get("persona", "")
                proyecto = datos.get("proyecto", "").strip()
                tipo = datos.get("tipo", "").strip()
                evaluado = datos.get("evaluado", "").strip()
                respuestas = datos.get("respuestas", {})
                if not proyecto or not tipo or not evaluado:
                    self.responder_json({"error": "Faltan campos obligatorios."}, 400)
                    return
                if tipo not in LABELS_TIPOS:
                    self.responder_json({"error": "Tipo de evaluación no válido."}, 400)
                    return
                preguntas = obtener_preguntas_tipo(tipo, idioma_por_sesion(sesion))
                ok = guardar_evaluacion_proyecto(evaluador, evaluado, proyecto, tipo, respuestas, preguntas)
                if ok:
                    self.responder_json({"ok": True})
                else:
                    self.responder_json({"error": "No se pudo guardar la evaluación en Notion."}, 500)
                return
            if ruta == "/api/solicitar-evaluacion-extra":
                evaluado = sesion.get("persona", "")
                _idi = idioma_por_sesion(sesion)
                evaluador = datos.get("evaluador", "").strip()
                contexto = datos.get("contexto", "").strip()
                if not evaluador or not contexto:
                    self.responder_json({"error": "Faltan campos obligatorios."}, 400)
                    return
                resultado = solicitar_evaluacion_extra(evaluado, evaluador, contexto, _idi)
                self.responder_json(resultado)
                return
            if ruta == "/api/guardar-evaluacion-extra":
                evaluador = sesion.get("persona", "")
                evaluado = datos.get("evaluado", "").strip()
                contexto = datos.get("contexto", "").strip()
                nota = datos.get("nota")
                justificacion = datos.get("justificacion", "").strip()
                solicitud_page_id = datos.get("solicitudPageId", "").strip()
                if not evaluado or not justificacion or nota not in (1, 2, 3, 4):
                    self.responder_json({"error": "Faltan campos obligatorios o la nota no es válida (1-4)."}, 400)
                    return
                ok = guardar_evaluacion_extra(evaluado, evaluador, contexto, nota, justificacion, solicitud_page_id)
                if ok:
                    self.responder_json({"ok": True})
                else:
                    self.responder_json({"error": "No se pudo guardar la evaluación en Notion."}, 500)
                return
            if ruta == "/api/urgencia-personal":
                nombre = sesion.get("persona", "")
                descripcion = datos.get("descripcion", "").strip()
                if not nombre or not descripcion:
                    self.responder_json({"error": "Faltan datos."}, 400)
                    return
                ok = notificar_urgencia_personal_web(nombre, descripcion)
                self.responder_json({"ok": ok})
                return
            if ruta == "/api/guardar-evaluacion-personal":
                nombre = sesion.get("persona", "")
                if not nombre:
                    self.responder_json({"error": "Tu sesión no tiene un nombre asociado. Vuelve a iniciar sesión."}, 400)
                    return
                comentario = datos.get("comentario", "").strip()
                if not comentario:
                    self.responder_json({"error": "El comentario no puede estar vacío."}, 400)
                    return
                ok = guardar_evaluacion_personal(nombre, {"comentario": comentario})
                if not ok:
                    self.responder_json({"error": "No se pudo guardar en Notion. Revisa los permisos del token de Notion."}, 500)
                    return
                self.responder_json({"ok": True})
                return
            if ruta == "/api/anonimato-evaluadores":
                if not sesion.get("is_admin"):
                    raise PermissionError("Solo administradores.")
                cfg = cargar_anonimato()
                anteriores = set(cfg.get("advisees_revelados") or [])
                if "global_anonimo" in datos:
                    cfg["global_anonimo"] = bool(datos["global_anonimo"])
                if "advisees_revelados" in datos:
                    cfg["advisees_revelados"] = list(datos["advisees_revelados"])
                guardar_anonimato(cfg)
                nuevos = set(cfg.get("advisees_revelados") or [])
                for advisee_cambiado in anteriores.symmetric_difference(nuevos):
                    _slug = slug_archivo(advisee_cambiado)
                    for _p in ("opiniones_ca_", "evals_mensuales_", "evals_proyecto_",
                               "seguimiento_personal_", "info_completa_"):
                        for _ext in (".pdf", ".html"):
                            _f = os.path.join(config.CARPETA_WEB, f"{_p}{_slug}{_ext}")
                            if os.path.exists(_f):
                                try:
                                    os.remove(_f)
                                except Exception:
                                    pass
                    _cache_json = os.path.join(config.CARPETA_WEB, f"opiniones_ca_{_slug}_cache.json")
                    if os.path.exists(_cache_json):
                        try:
                            os.remove(_cache_json)
                        except Exception:
                            pass
                self.responder_json({"ok": True, **cfg})
                return
            if ruta == "/api/guardar-evaluacion-slack":
                persona = sesion.get("persona", "")
                evaluado_nombre = datos.get("evaluado", "").strip()
                proyecto_nombre = datos.get("proyecto", "").strip()
                area = datos.get("area", "negocio").strip().lower()
                respuestas_usuario = datos.get("respuestas", {})
                if not evaluado_nombre or not persona:
                    self.responder_json({"error": "Faltan campos obligatorios."}, 400)
                    return
                respuestas_completas = {"evaluado": evaluado_nombre, "proyecto": proyecto_nombre}
                respuestas_completas.update({k: v for k, v in respuestas_usuario.items() if v})
                _, cargo_evaluado = buscar_empleado_y_cargo(evaluado_nombre)
                evaluador_perfil = obtener_perfil_empleado(persona)
                cargo_evaluador = evaluador_perfil.get("cargo", "")
                relacion = comparar_jerarquia(cargo_evaluador, cargo_evaluado or "")
                _AREA_DISPLAY = {"negocio": "Negocio", "middleoffice": "MiddleOffice", "palantir": "Palantir"}
                page_id = guardar_en_notion(persona, respuestas_completas, relacion=relacion, area=_AREA_DISPLAY.get(area, "Negocio"))
                if page_id:
                    self.responder_json({"ok": True, "page_id": page_id})
                else:
                    self.responder_json({"error": "No se pudo guardar en Notion."}, 500)
                return
            if ruta == "/api/actualizar-evaluacion-slack":
                persona = sesion.get("persona", "")
                page_id = datos.get("page_id", "").strip()
                evaluado_nombre = datos.get("evaluado", "").strip()
                proyecto_nombre = datos.get("proyecto", "").strip()
                area = datos.get("area", "negocio").strip().lower()
                respuestas_usuario = datos.get("respuestas", {})
                if not page_id or not persona or not evaluado_nombre:
                    self.responder_json({"error": "Faltan campos obligatorios."}, 400)
                    return
                respuestas_completas = {"evaluado": evaluado_nombre, "proyecto": proyecto_nombre}
                respuestas_completas.update({k: v for k, v in respuestas_usuario.items() if v})
                _, cargo_evaluado = buscar_empleado_y_cargo(evaluado_nombre)
                evaluador_perfil = obtener_perfil_empleado(persona)
                cargo_evaluador = evaluador_perfil.get("cargo", "")
                relacion = comparar_jerarquia(cargo_evaluador, cargo_evaluado or "")
                _AREA_DISPLAY = {"negocio": "Negocio", "middleoffice": "MiddleOffice", "palantir": "Palantir"}
                ok = actualizar_en_notion(page_id, persona, respuestas_completas, relacion=relacion, area=_AREA_DISPLAY.get(area, "Negocio"))
                if ok:
                    self.responder_json({"ok": True})
                else:
                    self.responder_json({"error": "No se pudo actualizar en Notion."}, 500)
                return
            self.responder_json({"error": "No encontrado"}, 404)
        except PermissionError as error:
            self.responder_json({"error": str(error)}, 403)
        except Exception as error:
            logging.exception("Error en API POST")
            self.responder_json({"error": str(error)}, 500)

    def do_DELETE(self):
        parsed = urllib.parse.urlparse(self.path)
        ruta = parsed.path
        try:
            sesion = self.sesion_actual()
            if not sesion:
                raise PermissionError("Inicia sesión para acceder.")
            length = int(self.headers.get("Content-Length", 0))
            datos = json.loads(self.rfile.read(length)) if length else {}
            if ruta == "/api/objetivos":
                page_id = datos.get("page_id", "")
                if not page_id:
                    self.responder_json({"error": "Falta page_id."}, 400)
                    return
                ok = eliminar_objetivo_persona(page_id)
                self.responder_json({"ok": ok})
                return
            self.responder_json({"error": "No encontrado"}, 404)
        except PermissionError as error:
            self.responder_json({"error": str(error)}, 403)
        except Exception as error:
            logging.exception("Error en API DELETE")
            self.responder_json({"error": str(error)}, 500)

    def url_archivo(self, nombre_archivo, evaluado):
        query = urllib.parse.urlencode({"evaluado": evaluado})
        return f"/api/files/{urllib.parse.quote(nombre_archivo)}?{query}"

    def servir_archivo_protegido(self, nombre_archivo, query):
        params = urllib.parse.parse_qs(query)
        evaluado = params.get("evaluado", [""])[0]
        sesion = self.sesion_actual()
        if not sesion:
            raise PermissionError("Inicia sesión para acceder.")
        ca_nombre = sesion.get("persona", "")
        advisees_ca = obtener_advisees(
            ca_nombre,
            ca_aliases=[sesion.get("username", ""), sesion.get("email", "")],
        )
        es_admin = sesion.get("is_admin", False)
        es_ca = normalizar_nombre(evaluado) in [normalizar_nombre(a) for a in advisees_ca]
        es_propio = normalizar_nombre(evaluado) == normalizar_nombre(ca_nombre)
        slug = slug_archivo(evaluado)
        es_borrador = (
            nombre_archivo.startswith(f"informe_{slug}.")
            or nombre_archivo.startswith(f"informe_anual_{slug}.")
        )
        es_trayectoria = nombre_archivo.startswith(f"trayectoria_{slug}.")
        es_final = nombre_archivo.startswith(f"informe_final_{slug}_")
        es_opiniones = nombre_archivo.startswith(f"opiniones_ca_{slug}.")
        es_fuente_pdf = any(nombre_archivo.startswith(f"{p}_{slug}.") for p in (
            "evals_proyecto", "seguimiento_personal", "evals_mensuales", "info_completa"))
        if not es_borrador and not es_trayectoria and not es_final and not es_opiniones and not es_fuente_pdf:
            raise PermissionError("El archivo solicitado no corresponde con la persona autorizada.")
        if (es_borrador or es_opiniones or es_fuente_pdf) and not es_admin and not es_ca:
            raise PermissionError("Solo el CA o un administrador pueden ver los documentos generados.")
        if (es_trayectoria or es_final) and not es_admin and not es_ca:
            if es_propio:
                ca_del_evaluado = obtener_ca_de_empleado(evaluado)
                if not (ca_del_evaluado and ca_tiene_acceso_activo(ca_del_evaluado)):
                    raise PermissionError("Tu CA aún no ha publicado tu informe.")
            else:
                raise PermissionError("No tienes permiso para ver este archivo.")
        ruta = os.path.join(config.CARPETA_WEB, os.path.basename(nombre_archivo))
        if not os.path.exists(ruta):
            self.responder_json({"error": "Archivo no encontrado"}, 404)
            return
        if nombre_archivo.endswith(".docx"):
            content_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        elif nombre_archivo.endswith(".pdf"):
            content_type = "application/pdf"
        else:
            content_type = "text/html; charset=utf-8"
        cache_control = "no-cache" if (es_opiniones or es_fuente_pdf or es_borrador) else "private, max-age=300"
        stat = os.stat(ruta)
        etag = '"%x-%x"' % (int(stat.st_mtime), stat.st_size)
        if self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            self.send_header("ETag", etag)
            self.send_header("Cache-Control", cache_control)
            self.end_headers()
            return
        with open(ruta, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache_control)
        self.send_header("ETag", etag)
        self.end_headers()
        self.wfile.write(body)


def iniciar_api_backend():
    os.makedirs(config.CARPETA_WEB, exist_ok=True)
    try:
        with ReusableTCPServer(("0.0.0.0", config.PUERTO_WEB), ApiHandler) as httpd:
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
