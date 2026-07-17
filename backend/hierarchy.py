import logging

_NIVELES_CARGO = {
    "trainee": 0,
    "analyst": 1,
    "associate": 2,
    "sr. associate": 3,
    "manager": 4,
    "sr. manager": 5,
    "director": 6,
    "partner": 7,
    # Palantir track (mapped to equivalent general scale). La Lista de empleados de
    # Notion usa varias formas para el mismo puesto ("Palantir Sr. Engineer" y
    # "Sr. Palantir Engineer"), así que cada nivel acepta todas sus variantes.
    "jr. palantir engineer": 1,
    "palantir engineer": 2,
    "mid palantir engineer": 2,
    "palantir sr. engineer": 3,
    "sr. palantir engineer": 3,
    "palantir lead": 4,
    "lead palantir engineer": 4,
    "director palantir": 6,
    "palantir director": 6,
    # MiddleOffice (Office Manager, Communication and PR, los Head) se deja fuera a
    # propósito: no participa en evaluaciones de proyecto. Si alguno entrara en una,
    # nivel_cargo() devuelve None y salta el warning de abajo.
}

# Un cargo sin mapear degrada la evaluación en silencio (None -> 'igual' -> top-down),
# así que se avisa una vez por cargo distinto para no inundar el log: cada render de
# evaluaciones pendientes vuelve a llamar aquí.
_cargos_sin_mapear_avisados: set = set()


def _normalizar_cargo(cargo: str) -> str:
    return " ".join(cargo.strip().lower().split())


def nivel_cargo(cargo: str) -> int | None:
    if not cargo:
        return None
    clave = _normalizar_cargo(cargo)
    nivel = _NIVELES_CARGO.get(clave)
    if nivel is None and clave not in _cargos_sin_mapear_avisados:
        _cargos_sin_mapear_avisados.add(clave)
        logging.warning(
            "Cargo '%s' no está en _NIVELES_CARGO: la jerarquía cae a 'igual' y la "
            "evaluación se sirve top-down. Añádelo a backend/hierarchy.py si no es intencionado.",
            cargo,
        )
    return nivel


def comparar_jerarquia(cargo_evaluador: str, cargo_evaluado: str) -> str:
    """Returns 'superior' if evaluador is more senior, 'inferior' if less, 'igual' otherwise."""
    nivel_eval = nivel_cargo(cargo_evaluador or "")
    nivel_evad = nivel_cargo(cargo_evaluado or "")
    if nivel_eval is None or nivel_evad is None:
        return "igual"
    if nivel_eval > nivel_evad:
        return "superior"
    if nivel_eval < nivel_evad:
        return "inferior"
    return "igual"


def tipo_relacion(relacion: str) -> str:
    """Convierte la relación jerárquica al nombre de sección en la BD de Preguntas."""
    if relacion == "superior":
        return "Top-Bottom"
    if relacion == "inferior":
        return "Bottom-Top"
    return "Same Level"
