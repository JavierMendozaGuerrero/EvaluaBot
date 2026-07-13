"""/api/proyectos-progreso -- portada desde el api_server.py viejo (añadida por
Irene en main mientras esta rama migraba a FastAPI); se integró al fusionar.

Incluye también los tests de la lógica por JERARQUÍA DE EMPRESA de las
evaluaciones de proyecto: qué plantilla corresponde a cada par, qué se libera
al evaluado (Visible_evaluado) y los borradores server-side.
"""

import backend.eval_tracking as eval_tracking_mod
import backend.project_evals as pe
from backend.api.routers import project_evals as project_evals_router


def test_proyectos_progreso_sin_sesion_da_403(client):
    r = client.get("/api/proyectos-progreso")
    assert r.status_code == 403


def test_proyectos_progreso_devuelve_lo_que_da_la_capa_de_negocio(client, as_session, user_session, monkeypatch):
    as_session(user_session)
    falso = [{"nombre_proyecto": "Proyecto X", "activado_por": "Manager Y", "equipo": ["A", "B"], "completadas": [], "a_hacer": []}]
    monkeypatch.setattr(project_evals_router, "obtener_progreso_proyectos_empleado", lambda persona: falso)
    r = client.get("/api/proyectos-progreso")
    assert r.status_code == 200
    assert r.json() == {"proyectos": falso}


# ---------------------------------------------------------------------------
# Tarea 1: la plantilla depende de la JERARQUÍA DE EMPRESA, no del rol de proyecto
# ---------------------------------------------------------------------------

_CARGOS = {
    "Ana Partner": "Partner",
    "Diego Director": "Director",
    "Luis Analyst": "Analyst",
    "Marta Manager": "Manager",
    "Pepe Manager": "Manager",
    "Nuria Sincargo": None,  # no está en la escala / cargo vacío en Notion
}


def _mock_cargos(monkeypatch):
    monkeypatch.setattr(pe, "buscar_empleado_y_cargo", lambda nombre: (nombre, _CARGOS.get(nombre)))


def test_evaluador_por_encima_usa_plantilla_top_to_bottom(monkeypatch):
    _mock_cargos(monkeypatch)
    assert pe.tipo_evaluacion_por_jerarquia("Ana Partner", "Luis Analyst") == ("manager_a_miembros", "superior")


def test_evaluador_por_debajo_usa_plantilla_bottom_to_top(monkeypatch):
    _mock_cargos(monkeypatch)
    assert pe.tipo_evaluacion_por_jerarquia("Luis Analyst", "Ana Partner") == ("miembros_a_manager", "inferior")


def test_mismo_nivel_usa_manager_a_miembros_pero_relacion_igual(monkeypatch):
    # Decisión de negocio: mismo nivel usa la plantilla top-to-bottom, pero al ser
    # relacion 'igual' NUNCA se libera al evaluado.
    _mock_cargos(monkeypatch)
    assert pe.tipo_evaluacion_por_jerarquia("Marta Manager", "Pepe Manager") == ("manager_a_miembros", "igual")


def test_misma_persona_es_autoevaluacion(monkeypatch):
    _mock_cargos(monkeypatch)
    assert pe.tipo_evaluacion_por_jerarquia("Luis Analyst", "Luis Analyst") == ("autoevaluacion", "igual")


def test_cargo_desconocido_se_trata_como_mismo_nivel(monkeypatch):
    _mock_cargos(monkeypatch)
    assert pe.tipo_evaluacion_por_jerarquia("Nuria Sincargo", "Luis Analyst") == ("manager_a_miembros", "igual")
    assert pe.tipo_evaluacion_por_jerarquia("Luis Analyst", "Nuria Sincargo") == ("manager_a_miembros", "igual")


def test_manager_de_proyecto_por_debajo_en_jerarquia_no_recibe_plantilla_de_manager(monkeypatch):
    """Criterio de aceptación: aunque Luis (Analyst) sea el 'manager del proyecto'
    (quien lo activó), evalúa al Partner y al Manager con la plantilla bottom-to-top."""
    _mock_cargos(monkeypatch)
    lista = pe.construir_evaluaciones_a_hacer("Luis Analyst", ["Luis Analyst", "Ana Partner", "Marta Manager"])
    assert lista[0] == {"tipo": "autoevaluacion", "evaluado": "Luis Analyst", "relacion": "igual"}
    por_evaluado = {it["evaluado"]: it for it in lista[1:]}
    assert por_evaluado["Ana Partner"] == {"tipo": "miembros_a_manager", "evaluado": "Ana Partner", "relacion": "inferior"}
    assert por_evaluado["Marta Manager"] == {"tipo": "miembros_a_manager", "evaluado": "Marta Manager", "relacion": "inferior"}


# ---------------------------------------------------------------------------
# Tareas 2 y 3: qué se libera al evaluado (Visible_evaluado)
# ---------------------------------------------------------------------------

def _preparar_guardado(monkeypatch, relacion, tipo_calc="manager_a_miembros"):
    capturado = {}
    monkeypatch.setattr(project_evals_router, "tipo_evaluacion_por_jerarquia", lambda ev, evd: (tipo_calc, relacion))
    monkeypatch.setattr(project_evals_router, "idioma_por_sesion", lambda s: "es")
    monkeypatch.setattr(project_evals_router, "obtener_preguntas_tipo", lambda tipo, idi: [])
    monkeypatch.setattr(project_evals_router, "eliminar_borrador_evaluacion_proyecto", lambda *a, **k: True)

    def fake_guardar(evaluador, evaluado, proyecto, tipo_clave, respuestas, preguntas, visible_evaluado=False):
        capturado.update(tipo=tipo_clave, visible=visible_evaluado, evaluado=evaluado)
        return True

    monkeypatch.setattr(project_evals_router, "guardar_evaluacion_proyecto", fake_guardar)
    return capturado


def test_guardar_top_to_bottom_se_libera_al_evaluado(client, as_session, user_session, monkeypatch):
    as_session(user_session)
    capturado = _preparar_guardado(monkeypatch, "superior")
    r = client.post("/api/guardar-evaluacion-proyecto", json={"proyecto": "P", "tipo": "manager_a_miembros", "evaluado": "Luis Analyst", "respuestas": {}})
    assert r.status_code == 200
    assert capturado["visible"] is True
    assert r.json()["relacion"] == "superior"


def test_guardar_bottom_to_top_no_se_libera(client, as_session, user_session, monkeypatch):
    as_session(user_session)
    capturado = _preparar_guardado(monkeypatch, "inferior", tipo_calc="miembros_a_manager")
    # El cliente manda el tipo ANTIGUO por rol de proyecto: el servidor lo recalcula.
    r = client.post("/api/guardar-evaluacion-proyecto", json={"proyecto": "P", "tipo": "manager_a_miembros", "evaluado": "Ana Partner", "respuestas": {}})
    assert r.status_code == 200
    assert capturado["visible"] is False
    assert capturado["tipo"] == "miembros_a_manager"


def test_guardar_mismo_nivel_no_se_libera(client, as_session, user_session, monkeypatch):
    as_session(user_session)
    capturado = _preparar_guardado(monkeypatch, "igual")
    r = client.post("/api/guardar-evaluacion-proyecto", json={"proyecto": "P", "tipo": "manager_a_miembros", "evaluado": "Pepe Manager", "respuestas": {}})
    assert r.status_code == 200
    assert capturado["visible"] is False


def test_guardar_evaluacion_proyecto_escribe_checkbox_visible(monkeypatch):
    """La capa de negocio escribe Visible_evaluado en la fila de Notion."""
    creado = {}
    monkeypatch.setattr(pe, "_obtener_o_crear_bbdd_evals_proyecto", lambda p: "db-test")
    monkeypatch.setattr(pe, "_crear_pagina_en_bbdd", lambda db, props: creado.update(props))
    monkeypatch.setattr(pe, "_verificar_y_cerrar_proyecto", lambda p: None)
    monkeypatch.setattr(eval_tracking_mod, "marcar_completada", lambda *a, **k: None)

    assert pe.guardar_evaluacion_proyecto("Ana", "Luis", "P", "manager_a_miembros", {}, [], visible_evaluado=True)
    assert creado["Visible_evaluado"]["checkbox"] is True

    assert pe.guardar_evaluacion_proyecto("Luis", "Ana", "P", "miembros_a_manager", {}, [], visible_evaluado=False)
    assert creado["Visible_evaluado"]["checkbox"] is False


# ---------------------------------------------------------------------------
# Tarea 3 (REGRESIÓN CRÍTICA): las bottom-to-top NUNCA aparecen en el TO-SEE
# del evaluado. /api/mis-evaluaciones-proyecto-recibidas solo devuelve filas
# con visible_evaluado=True.
# ---------------------------------------------------------------------------

def test_recibidas_excluye_bottom_to_top_y_antiguas(client, as_session, user_session, monkeypatch):
    as_session(user_session)
    filas = [
        # top-to-bottom liberada: SÍ debe verse
        {"proyecto": "P", "evaluador": "Ana Partner", "tipo": "Evaluación de managers a miembros del equipo",
         "respuestas": "x", "fecha": "2026-07-01", "page_id": "1", "url": "", "visible_evaluado": True},
        # bottom-to-top: NUNCA debe verse (el caso que no debe cambiar)
        {"proyecto": "P", "evaluador": "Luis Analyst", "tipo": "Evaluación de miembros del equipo a managers",
         "respuestas": "y", "fecha": "2026-07-02", "page_id": "2", "url": "", "visible_evaluado": False},
        # fila histórica sin flag (anterior al cambio): tampoco se ve
        {"proyecto": "Q", "evaluador": "Marta Manager", "tipo": "Evaluación de managers a miembros del equipo",
         "respuestas": "z", "fecha": "2026-06-01", "page_id": "3", "url": "", "visible_evaluado": False},
    ]
    monkeypatch.setattr(project_evals_router, "obtener_evaluaciones_proyecto_por_evaluado", lambda persona: list(filas))
    r = client.get("/api/mis-evaluaciones-proyecto-recibidas")
    assert r.status_code == 200
    devueltas = r.json()["evaluaciones"]
    assert len(devueltas) == 1
    assert devueltas[0]["page_id"] == "1"
    assert all(e["evaluador"] != "Luis Analyst" for e in devueltas)


def test_recibidas_sin_sesion_da_403(client):
    r = client.get("/api/mis-evaluaciones-proyecto-recibidas")
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# Tarea 2a: borradores server-side
# ---------------------------------------------------------------------------

def test_borrador_guardar_leer_y_eliminar(client, as_session, user_session, monkeypatch):
    as_session(user_session)
    guardado = {}
    monkeypatch.setattr(
        project_evals_router, "guardar_borrador_evaluacion_proyecto",
        lambda evaluador, proyecto, tipo, evaluado, respuestas: guardado.update(
            evaluador=evaluador, proyecto=proyecto, tipo=tipo, evaluado=evaluado, respuestas=respuestas) or True,
    )
    r = client.post("/api/borrador-evaluacion-proyecto", json={"proyecto": "P", "tipo": "manager_a_miembros", "evaluado": "Luis", "respuestas": {"q1": "3"}})
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert guardado["evaluador"] == user_session["persona"]
    assert guardado["respuestas"] == {"q1": "3"}

    borrador = {"respuestas": {"q1": "3"}, "actualizado": "2026-07-13T10:00:00+00:00"}
    monkeypatch.setattr(project_evals_router, "obtener_borrador_evaluacion_proyecto", lambda *a: borrador)
    r = client.get("/api/borrador-evaluacion-proyecto", params={"proyecto": "P", "tipo": "manager_a_miembros", "evaluado": "Luis"})
    assert r.status_code == 200
    assert r.json() == {"borrador": borrador}

    eliminado = {}
    monkeypatch.setattr(
        project_evals_router, "eliminar_borrador_evaluacion_proyecto",
        lambda evaluador, proyecto, tipo, evaluado: eliminado.update(k=(evaluador, proyecto, tipo, evaluado)) or True,
    )
    r = client.post("/api/borrador-evaluacion-proyecto/eliminar", json={"proyecto": "P", "tipo": "manager_a_miembros", "evaluado": "Luis"})
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert eliminado["k"] == (user_session["persona"], "P", "manager_a_miembros", "Luis")


def test_borrador_tipo_invalido_da_400(client, as_session, user_session):
    as_session(user_session)
    r = client.post("/api/borrador-evaluacion-proyecto", json={"proyecto": "P", "tipo": "no-existe", "evaluado": "X", "respuestas": {}})
    assert r.status_code == 400
    r = client.get("/api/borrador-evaluacion-proyecto", params={"proyecto": "P", "tipo": "no-existe"})
    assert r.status_code == 400


def test_evaluaciones_a_hacer_usa_jerarquia(client, as_session, user_session, monkeypatch):
    as_session(user_session)
    monkeypatch.setattr(project_evals_router, "obtener_equipo_proyecto", lambda p: ["Carlos CA", "Ana Partner"])
    esperado = [
        {"tipo": "autoevaluacion", "evaluado": "Carlos CA", "relacion": "igual"},
        {"tipo": "miembros_a_manager", "evaluado": "Ana Partner", "relacion": "inferior"},
    ]
    monkeypatch.setattr(project_evals_router, "construir_evaluaciones_a_hacer", lambda persona, equipo: esperado)
    r = client.get("/api/evaluaciones-proyecto-a-hacer", params={"proyecto": "P"})
    assert r.status_code == 200
    assert r.json() == {"evaluaciones": esperado}
