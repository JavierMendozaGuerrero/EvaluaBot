import os

from fastapi import APIRouter, Body, Depends

from ..deps import require_admin
from ... import config
from ...anonimato import cargar_config as cargar_anonimato, guardar_config as guardar_anonimato
from ...utils import slug_archivo

router = APIRouter()


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
        cfg["advisees_revelados"] = list(datos["advisees_revelados"])
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
