from fastapi import APIRouter, Depends

from ..deps import require_admin, require_session
from ...notion_service import (
    listar_bbdd_evaluados,
    obtener_advisees,
    obtener_datos_empleados_por_nombres,
    obtener_paises_disponibles,
    obtener_perfil_empleado,
    obtener_registros_empleados,
)
from ...utils import normalizar_nombre

router = APIRouter()


@router.get("/api/evaluados")
def evaluados(session=Depends(require_session)):
    if session.get("is_admin"):
        registros = obtener_registros_empleados()
        opciones = sorted(
            [{"value": r["nombre"], "label": r["nombre"], "foto": r.get("foto", "")} for r in registros if r.get("nombre")],
            key=lambda o: o["label"].lower(),
        )
    else:
        opciones = []
        for bbdd in sorted(listar_bbdd_evaluados(), key=lambda item: item["evaluado"].lower()):
            if normalizar_nombre(bbdd["evaluado"]) != normalizar_nombre(session.get("persona")):
                continue
            opciones.append({"value": bbdd["evaluado"], "label": bbdd["evaluado"]})
        datos = obtener_datos_empleados_por_nombres([o["value"] for o in opciones])
        fotos = {normalizar_nombre(d["nombre"]): d.get("foto", "") for d in datos}
        for o in opciones:
            o["foto"] = fotos.get(normalizar_nombre(o["value"]), "")
    return {"evaluados": opciones}


@router.get("/api/mis-advisees")
def mis_advisees(session=Depends(require_session)):
    ca_nombre = session.get("persona", "")
    ca_aliases = [session.get("username", ""), session.get("email", "")]
    advisee_nombres = obtener_advisees(ca_nombre, ca_aliases=ca_aliases)
    vistos = set()
    advisee_nombres = [
        nombre
        for nombre in advisee_nombres
        if nombre and not (normalizar_nombre(nombre) in vistos or vistos.add(normalizar_nombre(nombre)))
    ]
    advisees = obtener_datos_empleados_por_nombres(advisee_nombres)
    return {"advisees": advisees}


@router.get("/api/mi-perfil")
def mi_perfil(session=Depends(require_session)):
    return obtener_perfil_empleado(session.get("persona", ""))


@router.get("/api/paises")
def paises(session=Depends(require_session)):
    return {"paises": obtener_paises_disponibles()}


@router.get("/api/perfil-empleado")
def perfil_empleado(
    nombre: str = "",
    session=Depends(require_admin("Solo administradores pueden consultar perfiles de empleados.")),
):
    return obtener_perfil_empleado(nombre)


@router.get("/api/todos-empleados")
def todos_empleados(session=Depends(require_session)):
    try:
        registros = obtener_registros_empleados()
        empleados = sorted([r["nombre"] for r in registros if r.get("nombre")], key=lambda n: n.lower())
    except Exception:
        empleados = []
    return {"empleados": empleados}
