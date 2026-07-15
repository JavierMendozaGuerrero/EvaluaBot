"""Cierre de objetivos: copia a 'Objetivos antiguos - {nombre}' + archivado del original.

El orden importa y por eso se fija aquí: se copia ANTES de archivar. Si un día alguien lo
invierte y el archivado deja de ser lo último, un fallo a mitad borraría el objetivo sin
haberlo guardado en ningún sitio. Duplicado es recuperable; perdido no.
"""

import pytest

from backend import notion_service as ns


@pytest.fixture
def objetivo():
    return {
        "page_id": "obj-1",
        "titulo": "Mejorar presentaciones",
        "ca": "Carlos CA",
        "kpis": "2 por trimestre",
        "descripcion": "Detalle",
        "tipo": "CTTF",
        "fecha": "2024-03-10T09:00:00+00:00",
    }


@pytest.fixture
def notion_falso(monkeypatch):
    """Captura las llamadas a Notion en vez de emitirlas."""
    llamadas = {"creadas": [], "archivadas": []}
    monkeypatch.setattr(ns, "_obtener_o_crear_bbdd_objetivos_persona", lambda nombre, antiguos=False: "db-antiguos")
    monkeypatch.setattr(
        ns, "_crear_pagina_en_bbdd",
        lambda db_id, props: llamadas["creadas"].append((db_id, props)),
    )
    monkeypatch.setattr(
        ns, "eliminar_objetivo_persona",
        lambda page_id: llamadas["archivadas"].append(page_id) or True,
    )
    return llamadas


def test_copia_a_antiguos_y_archiva_el_original(notion_falso, objetivo):
    assert ns.mover_objetivo_a_antiguos("Juan Perez", objetivo, "Carlos CA") is True

    assert len(notion_falso["creadas"]) == 1
    db_id, props = notion_falso["creadas"][0]
    assert db_id == "db-antiguos"
    assert props["Name"]["title"][0]["text"]["content"] == "Mejorar presentaciones"
    assert props["KPIs"]["rich_text"][0]["text"]["content"] == "2 por trimestre"
    assert props["Tipo"]["rich_text"][0]["text"]["content"] == "CTTF"
    # Conserva la fecha de creación original, no la del cierre.
    assert props["Fecha"]["date"]["start"] == "2024-03-10T09:00:00+00:00"
    # Y registra quién lo cerró y cuándo.
    assert props["Eliminado por"]["rich_text"][0]["text"]["content"] == "Carlos CA"
    assert props["Fecha eliminacion"]["date"]["start"]

    assert notion_falso["archivadas"] == ["obj-1"]


def test_no_archiva_si_la_copia_falla(monkeypatch, notion_falso, objetivo):
    """Sin copia no hay archivado: el objetivo sigue vivo en su base en vez de evaporarse."""
    def _falla(db_id, props):
        raise RuntimeError("Notion caído")

    monkeypatch.setattr(ns, "_crear_pagina_en_bbdd", _falla)

    assert ns.mover_objetivo_a_antiguos("Juan Perez", objetivo, "Carlos CA") is False
    assert notion_falso["archivadas"] == []


def test_objetivo_sin_fecha_no_manda_date_vacio(notion_falso, objetivo):
    """Notion rechaza {"start": ""}; debe ir date: None."""
    objetivo["fecha"] = ""

    assert ns.mover_objetivo_a_antiguos("Juan Perez", objetivo, "Carlos CA") is True
    _, props = notion_falso["creadas"][0]
    assert props["Fecha"] == {"date": None}


def test_la_pagina_de_antiguos_se_crea_bajo_to_see(monkeypatch):
    """No en la raíz: mismo criterio que el log de evaluación anual (commit 999c44a)."""
    creadas = []
    monkeypatch.setattr(ns, "_parent_bbdd_referencia", lambda: {"type": "page_id", "page_id": "root"})
    monkeypatch.setattr(ns, "_buscar_pagina_en_jerarquia", lambda nombre, root: None)  # no existe aún
    monkeypatch.setattr(
        ns, "_parent_bbdd_en_pagina",
        lambda nombre, crear=False: {"type": "page_id", "page_id": f"pagina-{nombre}"},
    )
    monkeypatch.setattr(
        ns.notion.pages, "create",
        lambda **kw: creadas.append(kw) or {"id": "nueva-pagina"},
    )

    assert ns._parent_pagina_objetivos_antiguos() == {"type": "page_id", "page_id": "nueva-pagina"}
    assert creadas[0]["parent"] == {"type": "page_id", "page_id": "pagina-TO-SEE"}


def test_la_pagina_de_antiguos_se_reutiliza_si_ya_existe(monkeypatch):
    """Si alguien la mueve a mano en Notion, se reutiliza donde esté en vez de duplicarla."""
    monkeypatch.setattr(ns, "_parent_bbdd_referencia", lambda: {"type": "page_id", "page_id": "root"})
    monkeypatch.setattr(ns, "_buscar_pagina_en_jerarquia", lambda nombre, root: "ya-existe")
    def _no_crear(**kw):
        raise AssertionError("No debe crear la página si ya existe")
    monkeypatch.setattr(ns.notion.pages, "create", _no_crear)

    assert ns._parent_pagina_objetivos_antiguos() == {"type": "page_id", "page_id": "ya-existe"}


def test_las_bases_de_vigentes_y_antiguos_no_comparten_titulo():
    assert ns._titulo_bbdd_objetivos("Juan Perez") == "Objetivos - Juan Perez"
    assert ns._titulo_bbdd_objetivos("Juan Perez", antiguos=True) == "Objetivos antiguos - Juan Perez"
    # La clave de cache se deriva del título: si colisionaran, leer los antiguos
    # devolvería los vigentes (y al revés).
    assert ns._clave_objetivos(ns._titulo_bbdd_objetivos("Juan Perez")) != ns._clave_objetivos(
        ns._titulo_bbdd_objetivos("Juan Perez", antiguos=True)
    )
