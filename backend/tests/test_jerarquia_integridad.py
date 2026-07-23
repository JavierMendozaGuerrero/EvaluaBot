"""La integridad del campo que decide la privacidad: `relacion`.

Todo el filtrado de feedback confidencial (ver test_pdfs_fuentes_privacidad) se apoya en que
`relacion` sea correcta. Estos tests cubren las tres formas en que podía dejar de serlo:

  1. Editar una evaluación cuando la jerarquía cambió dejaba la columna vieja puesta, así que
     la fila declaraba dos jerarquías y la lectura resolvía el empate a favor de 'superiores'
     -> una bottom-to-top se publicaba al CA.
  2. Una fila ya corrupta (por el bug anterior o editada a mano en Notion) debía dejar de
     interpretarse en vez de adivinar.
  3. La web recalculaba la jerarquía al guardar, y no al servir las preguntas: si el cargo
     cambiaba entremedias, el evaluador respondía a preguntas bottom-to-top y se grababa
     como top-down.
"""

import pytest

from backend import notion_service as ns
from backend.api.routers import personal_slack as ps


# ── 1. Editar no puede dejar dos jerarquías en la misma fila ──────────────────

@pytest.fixture
def notion_falso(monkeypatch):
    """Captura el update que se manda a Notion en vez de enviarlo."""
    capturado = {}

    class _Pages:
        def update(self, page_id, properties):
            capturado["page_id"] = page_id
            capturado["properties"] = properties

    monkeypatch.setattr(ns, "notion", type("N", (), {"pages": _Pages()})())
    return capturado


def _texto_de(props, nombre):
    return "".join(t["text"]["content"] for t in props[nombre]["rich_text"])


def test_editar_vacia_las_columnas_de_las_otras_jerarquias(notion_falso):
    """Guardada como bottom-to-top y editada ya como top-down: 'de inferiores' debe quedar vacía."""
    ok = ns.actualizar_en_notion(
        "page-1", "Evaluador X",
        {"evaluado": "Laura", "proyecto": "P", "q1": "5", "q2": "texto nuevo"},
        relacion="superior",
    )

    assert ok
    props = notion_falso["properties"]
    assert _texto_de(props, "Valoración de superiores") == "5"
    assert _texto_de(props, "Justificación de superiores") == "texto nuevo"
    # Lo que fallaba: estas se quedaban con el texto anterior.
    for col in ("Valoración de inferiores", "Justificación de inferiores",
                "Valoración de iguales", "Justificación de iguales"):
        assert props[col]["rich_text"] == [], f"'{col}' deberia haberse vaciado"


# ── 2. Una fila con dos jerarquías es corrupta y no se interpreta ─────────────

def _rt(texto):
    return {"rich_text": [{"text": {"content": texto}}]}


def _pagina(**columnas):
    props = {
        "Name": {"title": [{"text": {"content": "Evaluacion 2026-07-15 10:00"}}]},
        "Evaluador": _rt("Evaluador X"),
        "Proyecto": _rt("Proyecto P"),
        "Fecha": {"date": {"start": "2026-07-15"}},
    }
    for col, texto in columnas.items():
        props[col] = _rt(texto)
    return {"id": "p1", "url": "https://notion/p1", "properties": props}


@pytest.fixture
def leer_paginas(monkeypatch):
    def _con(paginas):
        monkeypatch.setattr(ns, "_query_bbdd", lambda db, **kw: {"results": paginas, "has_more": False})
        return ns.obtener_evaluaciones_de_bbdd("db-1", "Laura")
    return _con


def test_fila_con_una_sola_jerarquia_se_lee_bien(leer_paginas):
    evals = leer_paginas([
        _pagina(**{"Valoración de inferiores": "2", "Justificación de inferiores": "CONFIDENCIAL"}),
    ])

    assert len(evals) == 1
    assert evals[0]["relacion"] == "inferior"
    assert evals[0]["q2"] == "CONFIDENCIAL"


def test_fila_con_dos_jerarquias_se_descarta_entera(leer_paginas):
    """Antes ganaba 'superiores' por ser el primer if: la fila se publicaba como top-down."""
    evals = leer_paginas([
        _pagina(**{
            "Valoración de superiores": "5", "Justificación de superiores": "texto nuevo",
            "Valoración de inferiores": "2", "Justificación de inferiores": "CONFIDENCIAL",
        }),
    ])

    assert evals == []


def test_una_fila_corrupta_no_se_lleva_por_delante_a_las_sanas(leer_paginas):
    evals = leer_paginas([
        _pagina(**{"Valoración de superiores": "4", "Justificación de superiores": "SANA"}),
        _pagina(**{"Valoración de superiores": "5", "Valoración de inferiores": "2"}),
    ])

    assert len(evals) == 1
    assert evals[0]["relacion"] == "superior"
    assert evals[0]["q2"] == "SANA"


# ── 3. La jerarquía se fija al abrir y se reutiliza al guardar ────────────────

@pytest.fixture(autouse=True)
def _limpiar_relaciones():
    ps._relaciones_fijadas.clear()
    yield
    ps._relaciones_fijadas.clear()


def test_al_guardar_se_usa_la_jerarquia_fijada_al_abrir(monkeypatch):
    """El cargo cambia después de servir las preguntas: debe mandar el de cuando se abrió."""
    ps._fijar_relacion("Ana", "Laura", "inferior")   # abrió una eval bottom-to-top
    # Ahora el perfil dice que es senior (la ascendieron, o la caché devuelve otro cargo).
    monkeypatch.setattr(ps, "obtener_perfil_empleado", lambda p: {"cargo": "Director"})

    assert ps._relacion_al_guardar("Ana", "Laura", "Analyst") == "inferior"


def test_sin_jerarquia_fijada_se_recalcula(monkeypatch):
    """Reinicio del proceso: se vuelve al comportamiento anterior, no se rompe el guardado."""
    monkeypatch.setattr(ps, "obtener_perfil_empleado", lambda p: {"cargo": "Director"})

    assert ps._relacion_al_guardar("Ana", "Laura", "Analyst") == "superior"


def test_la_jerarquia_fijada_caduca(monkeypatch):
    ps._fijar_relacion("Ana", "Laura", "inferior")
    monkeypatch.setattr(ps, "obtener_perfil_empleado", lambda p: {"cargo": "Director"})
    # Simula que la entrada es más vieja que el TTL.
    viejo = ps.time.time() - ps._TTL_RELACION_FIJADA - 1
    ps._relaciones_fijadas[ps._clave_relacion("Ana", "Laura")] = ("inferior", viejo)

    assert ps._relacion_al_guardar("Ana", "Laura", "Analyst") == "superior"
    assert ps._clave_relacion("Ana", "Laura") not in ps._relaciones_fijadas, "debe purgarse"


def test_la_jerarquia_fijada_no_depende_de_como_se_escriba_el_nombre():
    ps._fijar_relacion("ana garcia", "Laura Carrasco", "inferior")

    assert ps._relacion_al_guardar("Ana García", "laura  carrasco", "Analyst") == "inferior"
