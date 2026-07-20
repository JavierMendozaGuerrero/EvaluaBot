"""Ver la versión ACTUAL del informe: la publicada o el borrador en curso.

El CA quiere repasar en qué punto está el informe aunque todavía no lo haya publicado,
así que /api/informe-final le sirve el borrador si es más reciente que la última Final.
El advisee NO entra en ese trato: a él solo se le sirve la versión publicada. Estos tests
fijan esa frontera, que es lo que se rompería sin querer al tocar el router.
"""

import pytest

from backend.api import files as files_router
from backend.api.routers import reports as reports_router

BORRADOR = {"empleado": "Juan Perez", "caActual": "Carlos CA", "dimensiones": []}


@pytest.fixture
def sin_word(monkeypatch, tmp_path):
    """Neutraliza la generación real del .docx/.html: aquí se comprueba el enrutado, no el Word."""
    monkeypatch.setattr(reports_router.eval_anual_sesion, "word_desde_borrador",
                        lambda borrador, nombre, fuentes=None: str(tmp_path / nombre))
    monkeypatch.setattr(reports_router.eval_anual_sesion, "fuentes_para_revision", lambda ev: {})
    monkeypatch.setattr(reports_router, "mammoth", None)


@pytest.fixture
def como_ca_de_juan(as_session, user_session, monkeypatch):
    as_session(user_session)
    monkeypatch.setattr(reports_router, "obtener_advisees", lambda *a, **k: ["Juan Perez"])


def test_ca_ve_el_borrador_como_version_actual(client, como_ca_de_juan, sin_word, monkeypatch):
    monkeypatch.setattr(reports_router, "obtener_informe_estructurado_actual",
                        lambda ev: {"estado": "Borrador", "contenido": BORRADOR})
    r = client.get("/api/informe-final", params={"evaluado": "Juan Perez", "incluir_borrador": 1})
    assert r.status_code == 200
    datos = r.json()
    assert datos["disponible"] is True
    assert datos["estado"] == "Borrador"
    # Nombre propio: el borrador no debe pisar el Word que el advisee puede descargar.
    assert "revision_informe_Juan_Perez.docx" in datos["docxUrl"]


def test_ca_ve_la_final_cuando_es_la_version_actual(client, como_ca_de_juan, sin_word, monkeypatch):
    """También la Final va a la copia de revisión: esta vista lleva el anexo de fuentes, así
    que no puede escribir sobre `informe_final_*`, que es el fichero del advisee."""
    monkeypatch.setattr(reports_router, "obtener_informe_estructurado_actual",
                        lambda ev: {"estado": "Final", "contenido": BORRADOR})
    r = client.get("/api/informe-final", params={"evaluado": "Juan Perez", "incluir_borrador": 1})
    assert r.status_code == 200
    datos = r.json()
    assert datos["estado"] == "Final"
    assert "revision_informe_Juan_Perez.docx" in datos["docxUrl"]


def test_sin_incluir_borrador_el_ca_solo_ve_la_publicada(client, como_ca_de_juan, sin_word, monkeypatch):
    """Las demás pantallas (panel de admin, subir informe) no piden el borrador y no deben verlo."""
    monkeypatch.setattr(reports_router, "obtener_informe_final_estructurado_reciente", lambda ev: BORRADOR)

    def _no_llamar(ev):
        raise AssertionError("Sin incluir_borrador no se debe mirar el borrador.")

    monkeypatch.setattr(reports_router, "obtener_informe_estructurado_actual", _no_llamar)
    r = client.get("/api/informe-final", params={"evaluado": "Juan Perez"})
    assert r.status_code == 200
    assert r.json()["estado"] == "Final"


def test_ca_sin_ninguna_version_recibe_no_disponible(client, como_ca_de_juan, sin_word, monkeypatch):
    monkeypatch.setattr(reports_router, "obtener_informe_estructurado_actual", lambda ev: None)
    r = client.get("/api/informe-final", params={"evaluado": "Juan Perez", "incluir_borrador": 1})
    assert r.status_code == 200
    assert r.json()["disponible"] is False


def test_advisee_recibe_la_final_y_nunca_el_borrador(client, as_session, user_session, sin_word, monkeypatch):
    """Aunque pida el borrador a mano en la URL, el advisee solo ve lo publicado."""
    as_session(user_session)
    yo = user_session["persona"]
    monkeypatch.setattr(reports_router, "obtener_advisees", lambda *a, **k: [])
    monkeypatch.setattr(reports_router, "obtener_ca_de_empleado", lambda ev: "Otra CA")
    monkeypatch.setattr(reports_router, "advisee_tiene_acceso_individual", lambda ev, ca: True)
    monkeypatch.setattr(reports_router, "obtener_informe_final_estructurado_reciente", lambda ev: BORRADOR)

    def _no_llamar(ev):
        raise AssertionError("El advisee no debe leer la versión actual: podría ser un borrador.")

    monkeypatch.setattr(reports_router, "obtener_informe_estructurado_actual", _no_llamar)

    r = client.get("/api/informe-final", params={"evaluado": yo, "incluir_borrador": 1})
    assert r.status_code == 200
    datos = r.json()
    assert datos["disponible"] is True
    assert datos["estado"] == "Final"
    assert "borrador" not in datos["docxUrl"]


def test_advisee_no_puede_descargar_el_docx_del_borrador(client, as_session, user_session, monkeypatch, tmp_path):
    """El fichero del borrador es solo del CA, aunque el advisee tenga acceso a su informe."""
    as_session(user_session)
    yo = user_session["persona"]  # "Carlos CA" -> es_propio, pero no es CA de sí mismo
    monkeypatch.setattr(files_router, "obtener_advisees", lambda *a, **k: [])
    monkeypatch.setattr(files_router, "obtener_ca_de_empleado", lambda ev: "Otra CA")
    monkeypatch.setattr(files_router, "advisee_tiene_acceso_individual", lambda ev, ca: True)
    monkeypatch.setattr(files_router.config, "CARPETA_WEB", str(tmp_path))
    (tmp_path / "revision_informe_Carlos_CA.docx").write_bytes(b"borrador en curso")

    r = client.get("/api/files/revision_informe_Carlos_CA.docx", params={"evaluado": yo})
    assert r.status_code == 403
    assert "Solo el CA o un administrador" in r.json()["error"]