"""Fronteras admin / CA / self -- la parte más sensible a seguridad de la migración.

Estos tests existen para que un cambio futuro en deps.py o en un router no pueda
aflojar (ni endurecer sin querer) quién puede ver qué, sin que un test falle.
"""

from backend.api import deps
from backend.api.routers import ca as ca_router
from backend.api.routers import perfiles as perfiles_router


def test_perfil_empleado_bloqueado_a_no_admin(client, as_session, user_session):
    as_session(user_session)
    r = client.get("/api/perfil-empleado", params={"nombre": "Alguien"})
    assert r.status_code == 403
    assert r.json() == {"error": "Solo administradores pueden consultar perfiles de empleados."}


def test_perfil_empleado_permitido_a_admin(client, as_session, admin_session, monkeypatch):
    as_session(admin_session)
    monkeypatch.setattr(perfiles_router, "obtener_perfil_empleado", lambda nombre: {"nombre": nombre, "cargo": "X"})
    r = client.get("/api/perfil-empleado", params={"nombre": "Alguien"})
    assert r.status_code == 200
    assert r.json() == {"nombre": "Alguien", "cargo": "X"}


def test_cumplimiento_evaluaciones_bloqueado_a_no_admin(client, as_session, user_session):
    as_session(user_session)
    r = client.get("/api/cumplimiento-evaluaciones")
    assert r.status_code == 403


def test_feedback_confidencial_bloqueado_a_no_admin(client, as_session, user_session):
    as_session(user_session)
    r = client.get("/api/feedback-confidencial", params={"evaluado": "X"})
    assert r.status_code == 403


def test_objetivos_get_bloqueado_si_no_es_advisee_del_ca(client, as_session, user_session, monkeypatch):
    as_session(user_session)
    # exigir_acceso_advisee vive en deps.py y usa SU propia referencia a obtener_advisees,
    # no la de ca.py -- hay que mockear la del módulo que realmente la ejecuta.
    monkeypatch.setattr(deps, "obtener_advisees", lambda *a, **k: ["Otra Persona"])
    r = client.get("/api/objetivos", params={"nombre": "Alguien Que No Tutela"})
    assert r.status_code == 403


def test_objetivos_get_permitido_si_es_advisee_del_ca(client, as_session, user_session, monkeypatch):
    as_session(user_session)
    monkeypatch.setattr(deps, "obtener_advisees", lambda *a, **k: ["Juan Perez"])
    monkeypatch.setattr(ca_router, "obtener_objetivos_persona", lambda nombre: [{"titulo": "Meta 1"}])
    r = client.get("/api/objetivos", params={"nombre": "Juan Perez"})
    assert r.status_code == 200
    assert r.json() == {"objetivos": [{"titulo": "Meta 1"}]}


def test_objetivos_get_permitido_para_los_propios_objetivos(client, as_session, user_session, monkeypatch):
    """Cualquiera puede ver sus propios objetivos aunque no figure como su propio CA."""
    as_session(user_session)
    monkeypatch.setattr(deps, "obtener_advisees", lambda *a, **k: ["Otra Persona"])
    monkeypatch.setattr(ca_router, "obtener_objetivos_persona", lambda nombre: [{"titulo": "Meta propia"}])
    r = client.get("/api/objetivos", params={"nombre": user_session["persona"]})
    assert r.status_code == 200
    assert r.json() == {"objetivos": [{"titulo": "Meta propia"}]}


def test_objetivos_delete_sin_nombre_da_400(client, as_session, user_session):
    """Ahora el borrado exige `nombre` para poder comprobar permisos."""
    as_session(user_session)
    r = client.request("DELETE", "/api/objetivos", json={"page_id": "cualquier-id"})
    assert r.status_code == 400


def test_objetivos_delete_bloqueado_si_no_es_advisee_del_ca(client, as_session, user_session, monkeypatch):
    """El hueco conocido queda cerrado: un no-CA no puede borrar objetivos ajenos."""
    as_session(user_session)
    monkeypatch.setattr(deps, "obtener_advisees", lambda *a, **k: ["Otra Persona"])
    monkeypatch.setattr(ca_router, "eliminar_objetivo_persona", lambda page_id: True)
    r = client.request(
        "DELETE", "/api/objetivos",
        json={"page_id": "id-ajeno", "nombre": "Alguien Que No Tutela"},
    )
    assert r.status_code == 403


def test_objetivos_delete_bloqueado_si_page_id_no_es_de_la_persona(client, as_session, user_session, monkeypatch):
    """Aunque el CA tutele a la persona, no puede borrar un page_id que no le pertenece."""
    as_session(user_session)
    monkeypatch.setattr(deps, "obtener_advisees", lambda *a, **k: ["Juan Perez"])
    monkeypatch.setattr(ca_router, "obtener_objetivos_persona", lambda nombre: [{"page_id": "suyo-1"}])
    monkeypatch.setattr(ca_router, "eliminar_objetivo_persona", lambda page_id: True)
    r = client.request(
        "DELETE", "/api/objetivos",
        json={"page_id": "id-de-otro", "nombre": "Juan Perez"},
    )
    assert r.status_code == 403


def test_objetivos_delete_permitido_si_es_advisee_y_page_id_suyo(client, as_session, user_session, monkeypatch):
    as_session(user_session)
    monkeypatch.setattr(deps, "obtener_advisees", lambda *a, **k: ["Juan Perez"])
    monkeypatch.setattr(ca_router, "obtener_objetivos_persona", lambda nombre: [{"page_id": "suyo-1"}])
    monkeypatch.setattr(ca_router, "eliminar_objetivo_persona", lambda page_id: True)
    r = client.request(
        "DELETE", "/api/objetivos",
        json={"page_id": "suyo-1", "nombre": "Juan Perez"},
    )
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_historial_evaluaciones_solo_propio_para_no_admin(client, as_session, user_session):
    as_session(user_session)
    r = client.get(
        "/api/historial-evaluaciones",
        params={"evaluado": "X", "evaluador": "Otra Persona Distinta"},
    )
    assert r.status_code == 403
