"""Prueba los exception handlers en aislamiento, con una app de test dedicada (no la
app real), para no arrastrar ninguna dependencia de negocio."""

import pytest
from fastapi import Body, FastAPI
from fastapi.testclient import TestClient

from backend.api.errors import MSG_ERROR_INESPERADO, register_exception_handlers
from backend.excepciones import ErrorIA


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

    @app.get("/boom/ia")
    def _ia():
        raise ErrorIA("La IA está saturada.", codigo="ia_saturada")

    @app.get("/boom/ia-definitivo")
    def _ia_definitivo():
        raise ErrorIA("Sin saldo.", codigo="ia_sin_saldo", definitivo=True)

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


def test_excepcion_generica_da_500_con_mensaje_generico(test_client):
    """El detalle del error NO debe filtrarse al cliente (info disclosure): el
    mensaje interno queda solo en el log y el cliente ve un texto genérico."""
    r = test_client.get("/boom/runtime")
    assert r.status_code == 500
    assert r.json() == {"error": MSG_ERROR_INESPERADO, "code": "error_inesperado"}
    assert "Algo raro" not in r.text


def test_error_ia_llega_al_usuario_con_su_mensaje_y_codigo(test_client):
    """Lo contrario que el handler genérico: el mensaje de ErrorIA está escrito para el
    usuario, así que debe llegarle tal cual en vez de quedar tapado por un texto genérico."""
    r = test_client.get("/boom/ia")
    assert r.status_code == 503  # temporal: invita a reintentar
    assert r.json() == {"error": "La IA está saturada.", "code": "ia_saturada"}


def test_error_ia_definitivo_da_500_no_503(test_client):
    """Reintentar no arregla un 'sin saldo', así que no debe pedirse con un 503."""
    r = test_client.get("/boom/ia-definitivo")
    assert r.status_code == 500
    assert r.json() == {"error": "Sin saldo.", "code": "ia_sin_saldo"}


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
