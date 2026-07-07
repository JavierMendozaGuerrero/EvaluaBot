"""/api/proyectos-progreso -- portada desde el api_server.py viejo (añadida por
Irene en main mientras esta rama migraba a FastAPI); se integró al fusionar."""

from backend.api.routers import project_evals as project_evals_router


def test_proyectos_progreso_sin_sesion_da_403(client):
    r = client.get("/api/proyectos-progreso")
    assert r.status_code == 403


def test_proyectos_progreso_devuelve_lo_que_da_la_capa_de_negocio(client, as_session, user_session, monkeypatch):
    as_session(user_session)
    falso = [{"nombre_proyecto": "Proyecto X", "activado_por": "Manager Y", "equipo": ["A", "B"], "completadas": []}]
    monkeypatch.setattr(project_evals_router, "obtener_progreso_proyectos_empleado", lambda persona: falso)
    r = client.get("/api/proyectos-progreso")
    assert r.status_code == 200
    assert r.json() == {"proyectos": falso}
