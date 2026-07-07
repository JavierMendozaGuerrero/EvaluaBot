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
