"""El toggle individual de anonimato (revelar los evaluadores de UN advisee concreto).

El global (`global_anonimo`) sale por un camino aparte que ni siquiera compara nombres;
estos tests cubren el individual, que sí los compara y es donde estaba el fallo: los
nombres vienen de Notion y no siempre llegan escritos igual (tildes, mayúsculas) en el
lado del admin que los guarda y en el lado del CA que los consulta.
"""

import pytest

from backend import anonimato
from backend.anonimato import evaluadores_visibles_para_advisee


@pytest.fixture
def config_temporal(tmp_path, monkeypatch):
    """Aísla anonimato.json en un fichero temporal: los tests no tocan la config real."""
    monkeypatch.setattr(anonimato, "_CONFIG_PATH", str(tmp_path / "anonimato.json"))
    return tmp_path


def _revelados(client):
    return client.get("/api/anonimato-evaluadores").json()["advisees_revelados"]


# --- La lógica de visibilidad ---------------------------------------------------

def test_revelar_individual_hace_visibles_sus_evaluadores():
    cfg = {"global_anonimo": True, "advisees_revelados": ["Irene Pedrós Tobaruela"]}
    assert evaluadores_visibles_para_advisee("Irene Pedrós Tobaruela", cfg) is True


def test_el_resto_sigue_anonimo_cuando_se_revela_a_uno():
    cfg = {"global_anonimo": True, "advisees_revelados": ["Irene Pedrós Tobaruela"]}
    assert evaluadores_visibles_para_advisee("Juan García", cfg) is False


@pytest.mark.parametrize("consultado", [
    "Irene Pedros Tobaruela",      # sin tildes
    "irene pedrós tobaruela",      # en minúsculas
    "  Irene   Pedrós Tobaruela ", # espacios de más
])
def test_el_nombre_casa_aunque_se_escriba_distinto(consultado):
    """El fallo original: se guardaba desde el admin con una grafía y se consultaba
    desde el CA con otra, así que la revelación individual no surtía efecto."""
    cfg = {"global_anonimo": True, "advisees_revelados": ["Irene Pedrós Tobaruela"]}
    assert evaluadores_visibles_para_advisee(consultado, cfg) is True


def test_el_toggle_global_no_depende_del_nombre():
    assert evaluadores_visibles_para_advisee("Cualquiera", {"global_anonimo": False, "advisees_revelados": []}) is True
    assert evaluadores_visibles_para_advisee("Cualquiera", {"global_anonimo": True, "advisees_revelados": []}) is False


# --- El endpoint que usa el botón ------------------------------------------------

def test_revelar_y_volver_a_ocultar_a_un_advisee(client, as_session, admin_session, config_temporal):
    as_session(admin_session)

    client.post("/api/anonimato-evaluadores", json={"advisees_revelados": ["Irene Pedrós Tobaruela"]})
    assert _revelados(client) == ["Irene Pedrós Tobaruela"]
    assert evaluadores_visibles_para_advisee("Irene Pedros Tobaruela") is True

    # Quitar la revelación: el botón manda la lista ya sin ese nombre.
    client.post("/api/anonimato-evaluadores", json={"advisees_revelados": []})
    assert _revelados(client) == []
    assert evaluadores_visibles_para_advisee("Irene Pedros Tobaruela") is False


def test_no_se_guarda_el_mismo_advisee_dos_veces(client, as_session, admin_session, config_temporal):
    """Antes, si el nombre no casaba, el front añadía un duplicado en vez de quitarlo
    y ya no había forma de volver a ocultarlo."""
    as_session(admin_session)
    client.post("/api/anonimato-evaluadores", json={
        "advisees_revelados": ["Irene Pedrós Tobaruela", "Irene Pedros Tobaruela", "irene pedrós tobaruela"],
    })
    assert len(_revelados(client)) == 1


def test_revelar_a_uno_no_pisa_el_toggle_global(client, as_session, admin_session, config_temporal):
    as_session(admin_session)
    client.post("/api/anonimato-evaluadores", json={"global_anonimo": False})
    client.post("/api/anonimato-evaluadores", json={"advisees_revelados": ["Irene Pedrós Tobaruela"]})
    assert client.get("/api/anonimato-evaluadores").json()["global_anonimo"] is False


def test_un_no_admin_no_puede_tocar_el_anonimato(client, as_session, user_session, config_temporal):
    as_session(user_session)
    r = client.post("/api/anonimato-evaluadores", json={"advisees_revelados": ["Irene Pedrós Tobaruela"]})
    assert r.status_code == 403