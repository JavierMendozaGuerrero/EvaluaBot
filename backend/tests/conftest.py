"""Fixtures compartidas para los tests de backend/api.

Importante: backend/config.py exige varias variables de entorno (Slack, Notion) en el
momento de importarse (`_require_env`). En un entorno de test no queremos credenciales
reales -- estos tests nunca deben llegar a golpear Notion/Slack de verdad, todo lo que
toque esos servicios se mockea explícitamente en cada test. Por eso fijamos valores
dummy aquí, antes de que nada bajo `backend` se importe.
"""

import os

os.environ.setdefault("SLACK_BOT_TOKEN", "test-dummy-token")
os.environ.setdefault("SLACK_APP_TOKEN", "test-dummy-token")
os.environ.setdefault("NOTION_TOKEN", "test-dummy-token")
os.environ.setdefault("NOTION_DATABASE_ID", "test-dummy-database-id")

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from backend.api.app import app
from backend.api.deps import get_session


@pytest.fixture(autouse=True)
def sin_notion_en_dimensiones(monkeypatch):
    """Corta el acceso a Notion al resolver las dimensiones del informe.

    Los apartados del informe salen de la BD de criterios del área en Notion, así que
    construir un borrador o iniciar una sesión intenta leerla. Con el token dummy de
    arriba esa llamada no falla rápido: se queda reintentando y el test se cuelga. Sin
    dimensiones, `dimensiones_informe` cae a las fijas, que es lo que estos tests
    esperan; el que quiera probar las de Notion, que las mockee él.
    """
    from backend import eval_anual_sesion as ea
    from backend import skill_informes_anual as sk

    monkeypatch.setattr(sk, "obtener_dimensiones_evaluacion", lambda grupo: [])
    monkeypatch.setattr(sk, "_grupo_empleado", lambda nombre, cargo: "Negocio")
    # La huella de la plantilla incluye el TEXTO de los criterios, así que también lee
    # de Notion. Sin esto la suite pasa igual, pero tarda trece veces más reintentando.
    monkeypatch.setattr(ea, "obtener_criterios_evaluacion", lambda grupo, idioma="es": {})
    monkeypatch.setattr(sk, "obtener_criterios_evaluacion", lambda grupo, idioma="es": {})


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def admin_session():
    return {"username": "admin", "persona": "Admin Persona", "email": "admin@x.com", "is_admin": True}


@pytest.fixture
def user_session():
    return {"username": "carlos", "persona": "Carlos CA", "email": "carlos@x.com", "is_admin": False}


@pytest.fixture
def as_session(admin_session, user_session):
    """Devuelve una función que fuerza la sesión "actual" para el resto del test."""

    def _activar(sesion):
        def _fake_get_session(request: Request):
            return sesion

        app.dependency_overrides[get_session] = _fake_get_session
        return sesion

    yield _activar
    app.dependency_overrides.pop(get_session, None)
