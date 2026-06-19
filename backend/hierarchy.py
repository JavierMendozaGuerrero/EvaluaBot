_NIVELES_CARGO = {
    "trainee": 0,
    "analyst": 1,
    "associate": 2,
    "sr. associate": 3,
    "manager": 4,
    "sr. manager": 5,
    "director": 6,
    "partner": 7,
    # Palantir track (mapped to equivalent general scale)
    "jr. palantir engineer": 1,
    "palantir engineer": 2,
    "palantir sr. engineer": 3,
}


def _normalizar_cargo(cargo: str) -> str:
    return " ".join(cargo.strip().lower().split())


def nivel_cargo(cargo: str) -> int | None:
    if not cargo:
        return None
    return _NIVELES_CARGO.get(_normalizar_cargo(cargo))


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


def sufijo_preguntas(relacion: str) -> str:
    if relacion == "superior":
        return " -EVALUANDO A DEBAJO"
    if relacion == "inferior":
        return " -EVALUANDO A GENTE DE ARRIBA"
    return " -EVALUANDO A GENTE DE MI NIVEL"
