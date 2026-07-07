"""Prueba los exception handlers en aislamiento, con una app de test dedicada (no la
app real), para no arrastrar ninguna dependencia de negocio."""

import pytest
from fastapi import Body, FastAPI
from fastapi.testclient import TestClient

from backend.api.errors import register_exception_handlers


@pytest.fixture
def test_app():
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/boom/permission")
    def _permission():
        raise PermissionError("No tienes acceso.")

    @app.get("/boom/value")
    def _value():
        raise ValueError("Valor no permitido.")

    @app.get("/boom/runtime")
    def _runtime():
        raise RuntimeError("Algo raro pasó.")

    @app.post("/boom/body")
    def _body(nombre: str = Body(...)):
        return {"nombre": nombre}

    return app


@pytest.fixture
def test_client(test_app):
    return TestClient(test_app, raise_server_exceptions=False)


def test_permission_error_da_403_con_forma_error(test_client):
    r = test_client.get("/boom/permission")
    assert r.status_code == 403
    assert r.json() == {"error": "No tienes acceso."}


def test_value_error_da_400_no_500(test_client):
    """Este es el fix aprobado: antes de la migración esto daba 500."""
    r = test_client.get("/boom/value")
    assert r.status_code == 400
    assert r.json() == {"error": "Valor no permitido."}


def test_excepcion_generica_da_500_con_forma_error(test_client):
    r = test_client.get("/boom/runtime")
    assert r.status_code == 500
    assert r.json() == {"error": "Algo raro pasó."}


def test_404_no_encontrado_tiene_forma_original(test_client):
    r = test_client.get("/no-existe-esta-ruta")
    assert r.status_code == 404
    assert r.json() == {"error": "No encontrado"}


def test_validation_error_se_reescribe_a_forma_error_400(test_client):
    """FastAPI devolvería 422 con {"detail": [...]} por defecto -- eso rompería el
    parseo del frontend (`data.error`), así que debe quedar reescrito a 400/{"error": ...}."""
    r = test_client.post("/boom/body", json={})
    assert r.status_code == 400
    body = r.json()
    assert "error" in body
    assert isinstance(body["error"], str)
