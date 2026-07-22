import os

from fastapi import APIRouter, Body, Depends, File, UploadFile

from ..deps import require_admin
from ... import config
from ...anonimato import cargar_config as cargar_anonimato, guardar_config as guardar_anonimato
from ...bienvenida import enviar_bienvenida
from ...create_users_from_employees import crear_cuenta_individual
from ...notion_service import asignar_advisee_a_ca, crear_empleado_en_notion, obtener_lista_cas, subir_archivo_a_notion
from ...utils import normalizar_nombre, slug_archivo

router = APIRouter()


@router.get("/api/admin/cas")
def admin_listar_cas(session=Depends(require_admin("Solo administradores."))):
    return {"cas": obtener_lista_cas()}


@router.post("/api/admin/subir-foto")
def admin_subir_foto(archivo: UploadFile = File(...), session=Depends(require_admin("Solo administradores."))):
    """Sube una imagen a Notion y devuelve su file_upload_id, para adjuntarla luego a
    la ficha del empleado al darlo de alta."""
    contenido = archivo.file.read()
    if not contenido:
        raise ValueError("El archivo está vacío.")
    file_upload_id = subir_archivo_a_notion(
        nombre_archivo=archivo.filename or "foto",
        contenido=contenido,
        content_type=archivo.content_type or "application/octet-stream",
    )
    return {"file_upload_id": file_upload_id, "filename": archivo.filename or "foto"}


@router.post("/api/admin/registrar-empleado")
def admin_registrar_empleado(datos: dict = Body(default={}), session=Depends(require_admin("Solo administradores."))):
    """Alta completa de un empleado: fila en Lista de empleados + cuenta de login +
    (opcional) asignación de CA + (opcional) bienvenida por Slack. Idempotente: no
    duplica empleado ni cuenta si ya existen. Los pasos que no salen se devuelven en
    `avisos` sin abortar el resto."""
    nombre = (datos.get("nombre") or "").strip()
    if not nombre:
        raise ValueError("El nombre es obligatorio.")

    avisos = []
    id_usuario = (datos.get("id_usuario") or "").strip()

    empleado = crear_empleado_en_notion(
        nombre=nombre,
        correo=datos.get("correo", ""),
        id_usuario=id_usuario,
        nombre_slack=datos.get("nombre_slack", ""),
        cargo=datos.get("cargo", ""),
        area=datos.get("area", ""),
        idioma=datos.get("idioma", ""),
        pais=datos.get("pais", ""),
        foto=datos.get("foto", ""),
        foto_upload_id=datos.get("foto_upload_id", ""),
    )
    if not empleado.get("creado"):
        avisos.append("Ya existía un empleado con ese nombre en la Lista de empleados (no se ha duplicado).")

    ca = (datos.get("ca") or "").strip()
    ca_asignado = None
    if ca:
        resultado_ca = asignar_advisee_a_ca(ca, nombre)
        if resultado_ca.get("ok"):
            ca_asignado = ca
        else:
            avisos.append(f"CA: {resultado_ca.get('motivo', 'no se pudo asignar')}")

    cuenta = crear_cuenta_individual(nombre, datos.get("correo", ""))
    if cuenta.get("ya_existia"):
        avisos.append("Ya existía una cuenta de login para esta persona (no se ha cambiado su contraseña).")

    bienvenida = None
    if datos.get("enviar_bienvenida", True):
        resultado_bienvenida = enviar_bienvenida(
            slack_id=id_usuario,
            nombre=nombre,
            idioma=(datos.get("idioma") or "es"),
            username=cuenta.get("username", ""),
            password=cuenta.get("password_temporal"),
        )
        bienvenida = resultado_bienvenida.get("enviado")
        if not resultado_bienvenida.get("enviado"):
            avisos.append(resultado_bienvenida.get("motivo", "No se envió la bienvenida por Slack."))

    return {
        "ok": True,
        "nombre": nombre,
        "empleado_creado": empleado.get("creado"),
        "username": cuenta.get("username"),
        "password_temporal": cuenta.get("password_temporal"),
        "ca_asignado": ca_asignado,
        "bienvenida_enviada": bienvenida,
        "avisos": avisos,
    }


@router.get("/api/anonimato-evaluadores")
def anonimato_evaluadores_get(session=Depends(require_admin("Solo administradores."))):
    return cargar_anonimato()


@router.post("/api/anonimato-evaluadores")
def anonimato_evaluadores_post(datos: dict = Body(default={}), session=Depends(require_admin("Solo administradores."))):
    cfg = cargar_anonimato()
    anteriores = set(cfg.get("advisees_revelados") or [])
    if "global_anonimo" in datos:
        cfg["global_anonimo"] = bool(datos["global_anonimo"])
    if "advisees_revelados" in datos:
        # Guardamos el nombre tal cual llega (se muestra al admin), pero sin repetidos:
        # el mismo advisee puede llegar escrito de varias formas y duplicado en la lista
        # haría imposible quitarle la revelación.
        vistos = set()
        limpios = []
        for nombre in datos["advisees_revelados"]:
            clave = normalizar_nombre(nombre)
            if not clave or clave in vistos:
                continue
            vistos.add(clave)
            limpios.append(nombre)
        cfg["advisees_revelados"] = limpios
    guardar_anonimato(cfg)
    nuevos = set(cfg.get("advisees_revelados") or [])
    for advisee_cambiado in anteriores.symmetric_difference(nuevos):
        slug = slug_archivo(advisee_cambiado)
        for prefijo in ("opiniones_ca_", "evals_mensuales_", "evals_proyecto_", "seguimiento_personal_", "info_completa_"):
            for ext in (".pdf", ".html"):
                f = os.path.join(config.CARPETA_WEB, f"{prefijo}{slug}{ext}")
                if os.path.exists(f):
                    try:
                        os.remove(f)
                    except Exception:
                        pass
        cache_json = os.path.join(config.CARPETA_WEB, f"opiniones_ca_{slug}_cache.json")
        if os.path.exists(cache_json):
            try:
                os.remove(cache_json)
            except Exception:
                pass
    return {"ok": True, **cfg}
