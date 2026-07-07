from fastapi import Request
from starlette.datastructures import Headers, QueryParams

from backend.api import deps


def _request(headers=None, query=""):
    scope = {
        "type": "http",
        "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
        "query_string": query.encode(),
    }
    return Request(scope)


def test_get_session_usa_bearer_header(monkeypatch):
    llamado_con = {}

    def fake_obtener_sesion_por_token(token):
        llamado_con["token"] = token
        return {"persona": "X"}

    monkeypatch.setattr(deps.users, "obtener_sesion_por_token", fake_obtener_sesion_por_token)
    request = _request(headers={"Authorization": "Bearer abc123"})
    sesion = deps.get_session(request)
    assert sesion == {"persona": "X"}
    assert llamado_con["token"] == "abc123"


def test_get_session_fallback_query_token(monkeypatch):
    """El fallback a ?token= es imprescindible: el frontend lo usa para descargas por window.open()."""
    llamado_con = {}

    def fake_obtener_sesion_por_token(token):
        llamado_con["token"] = token
        return {"persona": "Y"}

    monkeypatch.setattr(deps.users, "obtener_sesion_por_token", fake_obtener_sesion_por_token)
    request = _request(query="token=xyz789")
    sesion = deps.get_session(request)
    assert sesion == {"persona": "Y"}
    assert llamado_con["token"] == "xyz789"


def test_get_session_header_tiene_precedencia_sobre_query(monkeypatch):
    def fake_obtener_sesion_por_token(token):
        return {"token_usado": token}

    monkeypatch.setattr(deps.users, "obtener_sesion_por_token", fake_obtener_sesion_por_token)
    request = _request(headers={"Authorization": "Bearer del-header"}, query="token=del-query")
    sesion = deps.get_session(request)
    assert sesion == {"token_usado": "del-header"}


def test_get_session_sin_token_devuelve_none(monkeypatch):
    monkeypatch.setattr(deps.users, "obtener_sesion_por_token", lambda token: None)
    request = _request()
    assert deps.get_session(request) is None


def test_require_session_lanza_permission_error_sin_sesion():
    try:
        deps.require_session(None)
        assert False, "debía lanzar PermissionError"
    except PermissionError as e:
        assert str(e) == "Inicia sesión para acceder."


def test_require_session_devuelve_la_sesion():
    sesion = {"persona": "Z"}
    assert deps.require_session(sesion) is sesion


def test_require_admin_permite_admin():
    dep = deps.require_admin("mensaje custom")
    sesion = {"is_admin": True}
    assert dep(sesion) is sesion


def test_require_admin_bloquea_no_admin_con_mensaje_custom():
    dep = deps.require_admin("Solo administradores pueden hacer X.")
    try:
        dep({"is_admin": False})
        assert False, "debía lanzar PermissionError"
    except PermissionError as e:
        assert str(e) == "Solo administradores pueden hacer X."


def test_exigir_acceso_advisee_admin_pasa_siempre(monkeypatch):
    monkeypatch.setattr(deps, "obtener_advisees", lambda *a, **k: [])
    deps.exigir_acceso_advisee({"is_admin": True}, "Cualquiera")  # no debe lanzar


def test_exigir_acceso_advisee_permite_si_esta_en_la_lista(monkeypatch):
    monkeypatch.setattr(deps, "obtener_advisees", lambda *a, **k: ["Juan Perez"])
    deps.exigir_acceso_advisee({"is_admin": False, "persona": "CA1"}, "juan perez")  # no debe lanzar


def test_exigir_acceso_advisee_bloquea_si_no_esta_en_la_lista(monkeypatch):
    monkeypatch.setattr(deps, "obtener_advisees", lambda *a, **k: ["Otra Persona"])
    try:
        deps.exigir_acceso_advisee({"is_admin": False, "persona": "CA1"}, "Juan Perez")
        assert False, "debía lanzar PermissionError"
    except PermissionError:
        pass
