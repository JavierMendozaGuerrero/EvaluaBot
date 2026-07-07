"""Tests de los middlewares de hardening: límite de tamaño de body y rate limit
de los endpoints de generación. Se prueban con una app FastAPI mínima dedicada
para no depender de ninguna lógica de negocio."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.hardening import (
    BodySizeLimitMiddleware,
    GenerationRateLimitMiddleware,
    MAX_BODY_JSON,
)


@pytest.fixture
def app_minima():
    app = FastAPI()

    @app.post("/api/cualquiera")
    def _cualquiera():
        return {"ok": True}

    @app.post("/api/generar")
    def _generar():
        return {"ok": True}

    @app.post("/api/generar-pdf-completo")
    def _generar_pdf():
        return {"ok": True}

    @app.get("/api/lectura")
    def _lectura():
        return {"ok": True}

    return app


def test_body_dentro_del_limite_pasa(app_minima):
    app_minima.add_middleware(BodySizeLimitMiddleware)
    client = TestClient(app_minima)
    r = client.post("/api/cualquiera", json={"x": "y"})
    assert r.status_code == 200


def test_body_gigante_da_413(app_minima):
    app_minima.add_middleware(BodySizeLimitMiddleware)
    client = TestClient(app_minima)
    # No hace falta materializar 1MB de datos: basta con declarar el Content-Length
    r = client.post(
        "/api/cualquiera",
        content=b"x",
        headers={"Content-Length": str(MAX_BODY_JSON + 1)},
    )
    assert r.status_code == 413
    assert "demasiado grande" in r.json()["error"]


def test_get_no_se_ve_afectado_por_el_limite(app_minima):
    app_minima.add_middleware(BodySizeLimitMiddleware)
    client = TestClient(app_minima)
    r = client.get("/api/lectura")
    assert r.status_code == 200


def test_rate_limit_bloquea_tras_el_limite(app_minima):
    app_minima.add_middleware(GenerationRateLimitMiddleware, limite=3, ventana=60)
    client = TestClient(app_minima)
    headers = {"Authorization": "Bearer token-de-prueba"}
    for _ in range(3):
        assert client.post("/api/generar", headers=headers).status_code == 200
    r = client.post("/api/generar", headers=headers)
    assert r.status_code == 429
    assert "Espera un minuto" in r.json()["error"]


def test_rate_limit_cubre_todas_las_rutas_generar(app_minima):
    """El límite es compartido entre /api/generar y /api/generar-pdf-* (mismo prefijo)."""
    app_minima.add_middleware(GenerationRateLimitMiddleware, limite=2, ventana=60)
    client = TestClient(app_minima)
    headers = {"Authorization": "Bearer otro-token"}
    assert client.post("/api/generar", headers=headers).status_code == 200
    assert client.post("/api/generar-pdf-completo", headers=headers).status_code == 200
    assert client.post("/api/generar", headers=headers).status_code == 429


def test_rate_limit_es_por_cliente(app_minima):
    app_minima.add_middleware(GenerationRateLimitMiddleware, limite=1, ventana=60)
    client = TestClient(app_minima)
    assert client.post("/api/generar", headers={"Authorization": "Bearer cliente-A"}).status_code == 200
    # Cliente distinto -> contador aparte, no le afecta el límite del anterior
    assert client.post("/api/generar", headers={"Authorization": "Bearer cliente-B"}).status_code == 200
    assert client.post("/api/generar", headers={"Authorization": "Bearer cliente-A"}).status_code == 429


def test_rate_limit_no_afecta_a_rutas_normales(app_minima):
    app_minima.add_middleware(GenerationRateLimitMiddleware, limite=1, ventana=60)
    client = TestClient(app_minima)
    for _ in range(5):
        assert client.post("/api/cualquiera").status_code == 200
