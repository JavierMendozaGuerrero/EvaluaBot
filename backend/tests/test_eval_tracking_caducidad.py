"""Caducidad de asignaciones y cierre de la pendiente correcta.

Las dos reglas que se verifican aquí:
  - una asignación pendiente deja de ser tarea cuando pasa su deadline (fecha_envio +
    frecuencia del tipo), pero su fila sigue con Completada=False para el cumplimiento;
  - `marcar_completada` cierra la pendiente más reciente aunque sea de un ciclo anterior,
    en vez de crear una fila nueva y dejar la vieja pendiente para siempre.
"""

from datetime import datetime, timedelta, timezone

import pytest

from backend import eval_tracking


def _dia(delta_dias: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=delta_dias)).date().isoformat()


def _fila(persona: str, tipo: str, envio: str, ciclo: str = "2026-01-01", completada: bool = False, page_id: str = "p"):
    return {
        "id": page_id,
        "properties": {
            "Persona": {"title": [{"plain_text": persona}]},
            "Tipo": {"select": {"name": tipo}},
            "Fecha_envio": {"date": {"start": envio}},
            "Ciclo": {"rich_text": [{"plain_text": ciclo}]},
            "Completada": {"checkbox": completada},
        },
    }


@pytest.fixture
def notion_falso(monkeypatch):
    """Sustituye la BD de tracking por una lista de filas en memoria."""
    estado = {"filas": [], "updates": [], "creadas": []}

    monkeypatch.setattr(eval_tracking, "_obtener_o_crear_bbdd", lambda: "db")
    monkeypatch.setattr(eval_tracking, "_iter_filas", lambda db_id, filter=None: iter(estado["filas"]))
    monkeypatch.setattr(
        eval_tracking, "obtener_frecuencias_evaluaciones", lambda: {"mensual": 30, "personal": 14, "ca": 30}
    )
    monkeypatch.setattr(
        eval_tracking, "_crear_pagina_en_bbdd", lambda db_id, props: estado["creadas"].append(props)
    )
    monkeypatch.setattr(
        eval_tracking.notion.pages,
        "update",
        lambda page_id, properties: estado["updates"].append((page_id, properties)),
    )
    return estado


# --- Lectura: qué sigue siendo una tarea -----------------------------------------------

def test_pendiente_dentro_de_plazo_es_tarea(notion_falso):
    notion_falso["filas"] = [_fila("Ana", "mensual", _dia(-10))]
    assert eval_tracking.pendientes_slack_de_persona("Ana") == [{"tipo": "mensual", "deadline": _dia(20)}]


def test_pendiente_caducada_no_es_tarea(notion_falso):
    # Enviada hace 40 días con frecuencia 30: la siguiente mensual ya debería haber llegado.
    notion_falso["filas"] = [_fila("Ana", "mensual", _dia(-40))]
    assert eval_tracking.pendientes_slack_de_persona("Ana") == []


def test_deadline_hoy_sigue_siendo_tarea(notion_falso):
    """Se caduca al pasar el deadline, no al alcanzarlo: queda el día entero."""
    notion_falso["filas"] = [_fila("Ana", "mensual", _dia(-30))]
    assert eval_tracking.pendientes_slack_de_persona("Ana") == [{"tipo": "mensual", "deadline": _dia(0)}]


def test_solo_cuenta_el_envio_mas_reciente_de_cada_tipo(notion_falso):
    """Una caducada vieja no reabre el tipo si hay un envío posterior vivo."""
    notion_falso["filas"] = [
        _fila("Ana", "mensual", _dia(-40), page_id="vieja"),
        _fila("Ana", "mensual", _dia(-5), page_id="nueva"),
    ]
    assert eval_tracking.pendientes_slack_de_persona("Ana") == [{"tipo": "mensual", "deadline": _dia(25)}]


def test_sin_frecuencia_no_caduca(notion_falso, monkeypatch):
    """Ante la duda, mejor mostrar una tarea de más que ocultar una real."""
    monkeypatch.setattr(eval_tracking, "obtener_frecuencias_evaluaciones", lambda: {})
    notion_falso["filas"] = [_fila("Ana", "mensual", _dia(-400))]
    assert eval_tracking.pendientes_slack_de_persona("Ana") == [{"tipo": "mensual", "deadline": ""}]


def test_cada_tipo_caduca_con_su_frecuencia(notion_falso):
    """El ciclo es de 28 días para todos, pero la caducidad es por tipo: a los 20 días la
    personal (14) ya caducó y la mensual (30) no."""
    notion_falso["filas"] = [
        _fila("Ana", "mensual", _dia(-20)),
        _fila("Ana", "personal", _dia(-20)),
    ]
    assert eval_tracking.pendientes_slack_de_persona("Ana") == [{"tipo": "mensual", "deadline": _dia(10)}]


def test_pendiente_de_otra_persona_se_ignora(notion_falso):
    notion_falso["filas"] = [_fila("Beatriz", "mensual", _dia(-1))]
    assert eval_tracking.pendientes_slack_de_persona("Ana") == []


def test_completada_posterior_apaga_pendiente_vieja(notion_falso):
    """El fantasma de la web: quedó una pendiente de un ciclo anterior aún en plazo, pero
    hay un envío posterior ya completado → la evaluación vigente está hecha, sin tarea."""
    notion_falso["filas"] = [
        _fila("Ana", "mensual", _dia(-10), page_id="vieja"),
        _fila("Ana", "mensual", _dia(-1), completada=True, page_id="hecha"),
    ]
    assert eval_tracking.pendientes_slack_de_persona("Ana") == []


def test_pendiente_posterior_a_la_completada_sigue_viva(notion_falso):
    """Un envío nuevo tras completar el anterior vuelve a ser tarea (no se oculta)."""
    notion_falso["filas"] = [
        _fila("Ana", "mensual", _dia(-3), completada=True, page_id="hecha"),
        _fila("Ana", "mensual", _dia(-1), page_id="nueva"),
    ]
    assert eval_tracking.pendientes_slack_de_persona("Ana") == [{"tipo": "mensual", "deadline": _dia(29)}]


def test_completada_de_otra_persona_no_apaga_pendiente(notion_falso):
    """Que Beatriz haya completado no cierra la tarea de Ana."""
    notion_falso["filas"] = [
        _fila("Ana", "mensual", _dia(-2), page_id="de_ana"),
        _fila("Beatriz", "mensual", _dia(-1), completada=True, page_id="de_beatriz"),
    ]
    assert eval_tracking.pendientes_slack_de_persona("Ana") == [{"tipo": "mensual", "deadline": _dia(28)}]


# --- Escritura: cerrar la pendiente correcta -------------------------------------------

def test_completar_cierra_la_pendiente_de_un_ciclo_anterior(notion_falso, monkeypatch):
    """El bug del fantasma: la fila viva es de un ciclo anterior (mensual dura 30 días, el
    ciclo 28). Debe cerrarse esa, no crearse una nueva."""
    monkeypatch.setattr(eval_tracking, "clave_ciclo_actual", lambda: "2026-02-01")
    notion_falso["filas"] = [_fila("Ana", "mensual", _dia(-29), ciclo="2026-01-01", page_id="vieja")]

    eval_tracking.marcar_completada("Ana", "mensual")

    assert notion_falso["creadas"] == []
    assert len(notion_falso["updates"]) == 1
    page_id, props = notion_falso["updates"][0]
    assert page_id == "vieja"
    assert props["Completada"] == {"checkbox": True}


def test_completar_cierra_la_pendiente_mas_reciente(notion_falso):
    notion_falso["filas"] = [
        _fila("Ana", "mensual", _dia(-40), page_id="vieja"),
        _fila("Ana", "mensual", _dia(-5), page_id="nueva"),
    ]

    eval_tracking.marcar_completada("Ana", "mensual")

    assert [pid for pid, _ in notion_falso["updates"]] == ["nueva"]


def test_completar_sin_envio_registrado_crea_fila_completada(notion_falso):
    """Auto-cura: no había ninguna asignación registrada, pero la evaluación se hizo."""
    eval_tracking.marcar_completada("Ana", "mensual")

    assert notion_falso["updates"] == []
    assert len(notion_falso["creadas"]) == 1
    assert notion_falso["creadas"][0]["Completada"] == {"checkbox": True}


# --- El endpoint que pinta la caja "Tareas pendientes" ---------------------------------

def test_endpoint_tareas_slack_solo_devuelve_las_vivas(notion_falso, client, as_session, user_session, monkeypatch):
    monkeypatch.setattr(
        "backend.api.routers.personal_slack._slack_deeplink", lambda: "slack://user?team=T&id=U"
    )
    notion_falso["filas"] = [
        _fila(user_session["persona"], "mensual", _dia(-5), page_id="viva"),
        _fila(user_session["persona"], "ca", _dia(-40), page_id="caducada"),
    ]
    as_session(user_session)

    resp = client.get("/api/tareas-slack")

    assert resp.status_code == 200
    assert resp.json() == {
        "pendientes": [{"tipo": "mensual", "deadline": _dia(25)}],
        "slackUrl": "slack://user?team=T&id=U",
    }
