"""Fronteras de los endpoints de gestión de proyecto: solo el manager que activó el
proyecto puede leer su estado o tocar su equipo.

Motivación concreta: /api/modificar-equipo-proyecto se servía sin comprobar la propiedad
del proyecto. Cualquier sesión válida podía mandar {"accion": "eliminar", "proyecto": ...,
"empleado": ...} sobre un proyecto ajeno; eliminar_miembro_proyecto además lanza
_limpiar_registros_evaluacion_miembro, que BORRA las evaluaciones de esa persona. No era
una fuga de lectura: era destrucción de datos ajenos a petición de cualquier usuario.

Los endpoints se direccionan por NOMBRE de proyecto (un string que elige el cliente), así
que la propiedad hay que comprobarla explícitamente en cada uno.
"""

from backend.api.routers import project_evals as router

# El proyecto que la sesión de test SÍ gestiona. _no_es_su_proyecto compara contra lo que
# devuelve obtener_proyectos_manager(persona_de_la_sesion).
_PROPIO = "Proyecto Propio"
_AJENO = "Proyecto De Otro"


def _solo_gestiona_el_propio(monkeypatch):
    monkeypatch.setattr(
        router, "obtener_proyectos_manager",
        lambda persona: [{"nombre_proyecto": _PROPIO, "equipo": ["Alguien"]}],
    )
    # idioma_por_sesion() lee la Lista de empleados de Notion. Sin mock, cada 403 se va a
    # la red con el token dummy del conftest y el archivo tarda minutos en vez de segundos.
    monkeypatch.setattr(router, "idioma_por_sesion", lambda sesion: "es")


def _explota_si_se_llama(nombre):
    def _fn(*a, **k):
        raise AssertionError(f"{nombre} no debía llegar a ejecutarse sobre un proyecto ajeno")
    return _fn


# ---------------------------------------------------------------------------
# modificar-equipo-proyecto: el que borra datos
# ---------------------------------------------------------------------------

def test_eliminar_miembro_de_proyecto_ajeno_da_403(client, as_session, user_session, monkeypatch):
    as_session(user_session)
    _solo_gestiona_el_propio(monkeypatch)
    # Si la comprobación de propiedad falta, estas se llaman y el test lo dice en claro.
    monkeypatch.setattr(router, "eliminar_miembro_proyecto", _explota_si_se_llama("eliminar_miembro_proyecto"))
    monkeypatch.setattr(router, "añadir_miembro_proyecto", _explota_si_se_llama("añadir_miembro_proyecto"))

    r = client.post(
        "/api/modificar-equipo-proyecto",
        json={"accion": "eliminar", "proyecto": _AJENO, "empleado": "Victima"},
    )
    assert r.status_code == 403


def test_añadir_miembro_a_proyecto_ajeno_da_403(client, as_session, user_session, monkeypatch):
    as_session(user_session)
    _solo_gestiona_el_propio(monkeypatch)
    monkeypatch.setattr(router, "añadir_miembro_proyecto", _explota_si_se_llama("añadir_miembro_proyecto"))
    monkeypatch.setattr(router, "eliminar_miembro_proyecto", _explota_si_se_llama("eliminar_miembro_proyecto"))

    r = client.post(
        "/api/modificar-equipo-proyecto",
        json={"accion": "añadir", "proyecto": _AJENO, "empleado": "Colado"},
    )
    assert r.status_code == 403


def test_modificar_equipo_del_proyecto_propio_sigue_funcionando(client, as_session, user_session, monkeypatch):
    """La otra mitad del contrato: cerrar el agujero no puede romper al manager legítimo."""
    as_session(user_session)
    _solo_gestiona_el_propio(monkeypatch)
    llamadas = []
    monkeypatch.setattr(
        router, "eliminar_miembro_proyecto",
        lambda proyecto, empleado, idioma="es": llamadas.append((proyecto, empleado)) or {"ok": True},
    )
    r = client.post(
        "/api/modificar-equipo-proyecto",
        json={"accion": "eliminar", "proyecto": _PROPIO, "empleado": "Miembro"},
    )
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    assert llamadas == [(_PROPIO, "Miembro")]


# ---------------------------------------------------------------------------
# estado-proyecto: el que filtraba quién no ha evaluado
# ---------------------------------------------------------------------------

def test_estado_de_proyecto_ajeno_da_403(client, as_session, user_session, monkeypatch):
    as_session(user_session)
    _solo_gestiona_el_propio(monkeypatch)
    monkeypatch.setattr(
        router, "obtener_estado_evaluaciones_proyecto",
        _explota_si_se_llama("obtener_estado_evaluaciones_proyecto"),
    )
    r = client.get("/api/estado-proyecto", params={"proyecto": _AJENO})
    assert r.status_code == 403


def test_estado_del_proyecto_propio_sigue_funcionando(client, as_session, user_session, monkeypatch):
    as_session(user_session)
    _solo_gestiona_el_propio(monkeypatch)
    monkeypatch.setattr(
        router, "obtener_estado_evaluaciones_proyecto",
        lambda proyecto: [{"evaluado": "Ana", "evaluadores": ["Luis"], "pendientes": ["Marta"]}],
    )
    monkeypatch.setattr(router, "cargar_anonimato", lambda: {"global_anonimo": False})
    monkeypatch.setattr(router, "evaluadores_visibles_para_advisee", lambda nombre, cfg: True)
    r = client.get("/api/estado-proyecto", params={"proyecto": _PROPIO})
    assert r.status_code == 200
    assert r.json()["estado"][0]["evaluadores"] == ["Luis"]


# ---------------------------------------------------------------------------
# recordatorio-proyecto: ya comprobaba la propiedad, pero no lo cubría ningún test.
# Estos fijan el comportamiento que TENÍA, para que compartir el helper con los dos
# endpoints de arriba no lo haya cambiado sin querer.
# ---------------------------------------------------------------------------

def test_recordatorio_de_proyecto_ajeno_da_403(client, as_session, user_session, monkeypatch):
    as_session(user_session)
    _solo_gestiona_el_propio(monkeypatch)
    monkeypatch.setattr(
        router, "enviar_recordatorios_proyecto", _explota_si_se_llama("enviar_recordatorios_proyecto"),
    )
    r = client.post("/api/recordatorio-proyecto", json={"proyecto": _AJENO})
    assert r.status_code == 403


def test_recordatorio_del_proyecto_propio_sigue_funcionando(client, as_session, user_session, monkeypatch):
    as_session(user_session)
    _solo_gestiona_el_propio(monkeypatch)
    monkeypatch.setattr(
        router, "enviar_recordatorios_proyecto",
        lambda proyecto, manager: {"enviados": ["Ana"], "fallidos": [], "sin_pendientes": False},
    )
    r = client.post("/api/recordatorio-proyecto", json={"proyecto": _PROPIO})
    assert r.status_code == 200
    # La forma exacta de la respuesta la consume el frontend (data.ok, data.enviados,
    # data.sin_pendientes en main.jsx): se fija aquí entera, no solo el 200.
    assert r.json() == {"ok": True, "enviados": ["Ana"], "fallidos": [], "sin_pendientes": False}


def test_recordatorio_sin_proyecto_da_400(client, as_session, user_session, monkeypatch):
    as_session(user_session)
    _solo_gestiona_el_propio(monkeypatch)
    r = client.post("/api/recordatorio-proyecto", json={"proyecto": "  "})
    assert r.status_code == 400


def test_estado_anonimizado_no_filtra_el_recuento_de_pendientes(client, as_session, user_session, monkeypatch):
    """Con anonimato activo no basta con vaciar las listas: el recuento también revela.

    n_pendientes se calculaba ANTES de vaciar 'pendientes', así que un equipo anonimizado
    seguía publicando cuánta gente faltaba por evaluar -- con un equipo pequeño eso
    identifica a las personas por descarte, que es justo lo que el anonimato evita.
    """
    as_session(user_session)
    _solo_gestiona_el_propio(monkeypatch)
    monkeypatch.setattr(
        router, "obtener_estado_evaluaciones_proyecto",
        lambda proyecto: [{"evaluado": "Ana", "evaluadores": ["Luis"], "pendientes": ["Marta", "Jose"]}],
    )
    monkeypatch.setattr(router, "cargar_anonimato", lambda: {"global_anonimo": True})
    monkeypatch.setattr(router, "evaluadores_visibles_para_advisee", lambda nombre, cfg: False)

    r = client.get("/api/estado-proyecto", params={"proyecto": _PROPIO})
    assert r.status_code == 200
    fila = r.json()["estado"][0]
    assert fila["evaluadores"] == []
    assert fila["pendientes"] == []
    assert "n_pendientes" not in fila, f"El recuento de pendientes sigue expuesto: {fila}"
