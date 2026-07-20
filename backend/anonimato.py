import json
import os

from .utils import normalizar_nombre

_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "dashboard_web", "anonimato.json")

_DEFAULTS = {"global_anonimo": True, "advisees_revelados": []}


def cargar_config():
    try:
        with open(_CONFIG_PATH, encoding="utf-8") as f:
            data = json.load(f)
            cfg = {**_DEFAULTS, **data}
            # Migrar nombres antiguos si existen
            for campo_viejo in ("evaluados_revelados", "cas_revelados"):
                if campo_viejo in cfg and "advisees_revelados" not in data:
                    cfg["advisees_revelados"] = cfg.pop(campo_viejo)
            return cfg
    except FileNotFoundError:
        return dict(_DEFAULTS)


def guardar_config(cfg):
    os.makedirs(os.path.dirname(_CONFIG_PATH), exist_ok=True)
    cfg.pop("evaluados_revelados", None)
    cfg.pop("cas_revelados", None)
    with open(_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def evaluadores_visibles_para_advisee(nombre_advisee: str, cfg=None) -> bool:
    """True si para este advisee se muestran los nombres de los evaluadores."""
    if cfg is None:
        cfg = cargar_config()
    if not cfg.get("global_anonimo", True):
        return True
    revelados_norm = {normalizar_nombre(n) for n in (cfg.get("advisees_revelados") or [])}
    return normalizar_nombre(nombre_advisee) in revelados_norm
