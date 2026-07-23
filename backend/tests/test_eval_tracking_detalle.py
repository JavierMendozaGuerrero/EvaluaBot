"""Agrupación del panel de cumplimiento: año > mes > categoría > tipo.

Lo que se fija aquí:
  - se agrupa por `Fecha_envio` (mes natural), NO por `Ciclo`: un ciclo es una ventana
    de 4 semanas anclada a una fecha de Notion, así que cruza meses;
  - las de Slack (mensual/personal/ca) son las opcionales y las demás las obligatorias
    de cerrar proyecto: esa división no está en Notion, sale del tipo;
  - lo más reciente primero, y ningún tipo desconocido se pierde por el camino.
"""

import pytest

from backend import eval_tracking
from backend.eval_tracking import detalle_por_persona


def _fila(persona: str, tipo: str, envio: str, ciclo: str = "2026-01-01", completada: bool = False):
    return {
        "id": "p",
        "properties": {
            "Persona": {"title": [{"plain_text": persona}]},
            "Tipo": {"select": {"name": tipo}},
            "Fecha_envio": {"date": {"start": envio}},
            "Ciclo": {"rich_text": [{"plain_text": ciclo}]},
            "Completada": {"checkbox": completada},
        },
    }


@pytest.fixture
def filas(monkeypatch):
    """Sustituye la BD de tracking por una lista de filas en memoria."""
    estado = {"filas": []}
    monkeypatch.setattr(eval_tracking, "_obtener_o_crear_bbdd", lambda: "db")
    monkeypatch.setattr(eval_tracking, "_iter_filas", lambda db_id, filter=None: iter(estado["filas"]))
    return estado


def _tipos(cat):
    return {t["tipo"]: (t["realizadas"], t["enviadas"]) for t in cat["tipos"]}


def test_agrupa_por_anio_mes_categoria_y_tipo(filas):
    filas["filas"] = [
        _fila("Ana", "mensual", "2026-01-10", completada=True),
        _fila("Ana", "mensual", "2026-01-20", completada=False),
        _fila("Ana", "personal", "2026-01-15", completada=True),
        _fila("Ana", "proyecto", "2026-01-31", completada=True),
    ]
    detalle = detalle_por_persona("Ana")

    assert len(detalle) == 1
    assert detalle[0]["anio"] == 2026
    assert len(detalle[0]["meses"]) == 1
    mes = detalle[0]["meses"][0]
    assert mes["mes"] == 1

    # Primero las opcionales de Slack, luego las obligatorias de proyecto.
    assert [c["categoria"] for c in mes["categorias"]] == ["slack", "proyecto"]
    assert _tipos(mes["categorias"][0]) == {"personal": (1, 1), "mensual": (1, 2)}
    assert _tipos(mes["categorias"][1]) == {"proyecto": (1, 1)}


def test_un_mes_sin_evaluaciones_de_proyecto_no_pinta_esa_categoria(filas):
    filas["filas"] = [_fila("Ana", "mensual", "2026-03-05")]
    categorias = detalle_por_persona("Ana")[0]["meses"][0]["categorias"]
    assert [c["categoria"] for c in categorias] == ["slack"]


def test_separa_meses_y_anios_de_mas_reciente_a_mas_antiguo(filas):
    filas["filas"] = [
        _fila("Ana", "mensual", "2025-11-02"),
        _fila("Ana", "mensual", "2026-01-10"),
        _fila("Ana", "mensual", "2026-03-01"),
    ]
    detalle = detalle_por_persona("Ana")
    assert [a["anio"] for a in detalle] == [2026, 2025]
    assert [m["mes"] for m in detalle[0]["meses"]] == [3, 1]
    assert [m["mes"] for m in detalle[1]["meses"]] == [11]


def test_agrupa_por_fecha_de_envio_y_no_por_ciclo(filas):
    """Un mismo ciclo de 4 semanas cruza el cambio de mes: esas dos filas comparten
    Ciclo pero tienen que caer en meses distintos."""
    filas["filas"] = [
        _fila("Ana", "mensual", "2026-01-28", ciclo="2026-01-15"),
        _fila("Ana", "mensual", "2026-02-04", ciclo="2026-01-15"),
    ]
    meses = detalle_por_persona("Ana")[0]["meses"]
    assert [m["mes"] for m in meses] == [2, 1]


def test_si_falta_la_fecha_de_envio_cae_al_ciclo(filas):
    fila = _fila("Ana", "mensual", "2026-05-10", ciclo="2026-04-03")
    fila["properties"]["Fecha_envio"] = {"date": None}
    filas["filas"] = [fila]
    detalle = detalle_por_persona("Ana")
    assert detalle[0]["anio"] == 2026
    assert detalle[0]["meses"][0]["mes"] == 4


def test_un_tipo_desconocido_no_se_pierde(filas):
    """Si alguien añade un tipo nuevo al select de Notion, tiene que seguir contando:
    va a la categoría de obligatorias y se pinta detrás de los tipos conocidos."""
    filas["filas"] = [
        _fila("Ana", "proyecto", "2026-01-10"),
        _fila("Ana", "tipo_nuevo", "2026-01-11"),
    ]
    categorias = detalle_por_persona("Ana")[0]["meses"][0]["categorias"]
    assert [c["categoria"] for c in categorias] == ["proyecto"]
    assert [t["tipo"] for t in categorias[0]["tipos"]] == ["proyecto", "tipo_nuevo"]


def test_solo_cuenta_las_filas_de_esa_persona(filas):
    filas["filas"] = [
        _fila("Ana", "mensual", "2026-01-10"),
        _fila("Otro", "mensual", "2026-01-10"),
    ]
    tipos = detalle_por_persona("Ana")[0]["meses"][0]["categorias"][0]["tipos"]
    assert tipos == [{"tipo": "mensual", "enviadas": 1, "realizadas": 0}]


def test_el_nombre_casa_aunque_lleve_tildes_distintas(filas):
    filas["filas"] = [_fila("Irene Pedrós", "mensual", "2026-01-10")]
    assert detalle_por_persona("Irene Pedros") != []


def test_sin_filas_devuelve_lista_vacia(filas):
    filas["filas"] = []
    assert detalle_por_persona("Ana") == []