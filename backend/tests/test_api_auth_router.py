from backend.api.routers import auth as auth_router


def test_health_no_requiere_sesion(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_me_sin_sesion_devuelve_user_null(client):
    r = client.get("/api/me")
    assert r.status_code == 200
    assert r.json() == {"user": None}


def test_login_correcto_devuelve_token_y_user(client, monkeypatch):
    monkeypatch.setattr(auth_router, "autenticar_usuario", lambda u, p: {"username": u, "persona": "Ana", "email": "a@x.com", "is_admin": False})
    monkeypatch.setattr(auth_router, "crear_sesion", lambda usuario: "token-123")
    monkeypatch.setattr(auth_router, "obtener_sesion_por_token", lambda token: {"username": "ana", "persona": "Ana", "email": "a@x.com", "is_admin": False})
    monkeypatch.setattr(auth_router, "idioma_por_sesion", lambda sesion: "es")

    r = client.post("/api/login", json={"username": "ana", "password": "correcta"})
    assert r.status_code == 200
    body = r.json()
    assert body["token"] == "token-123"
    assert body["user"]["idioma"] == "es"


def test_login_incorrecto_da_403(client, monkeypatch):
    def fake_autenticar(u, p):
        raise PermissionError("Usuario o contraseña incorrectos.")

    monkeypatch.setattr(auth_router, "autenticar_usuario", fake_autenticar)
    r = client.post("/api/login", json={"username": "ana", "password": "mala"})
    assert r.status_code == 403
    assert r.json() == {"error": "Usuario o contraseña incorrectos."}


def test_set_pais_invalido_da_400_no_500(client, as_session, user_session, monkeypatch):
    """Regresión del bug corregido en esta migración."""
    as_session(user_session)
    r = client.post("/api/set-pais", json={"pais": "Marte"})
    assert r.status_code == 400
    assert r.json() == {"error": "País no permitido."}


def test_set_pais_valido_ok(client, as_session, user_session, monkeypatch):
    as_session(user_session)
    monkeypatch.setattr(auth_router, "guardar_pais_por_sesion", lambda sesion, pais: pais)
    r = client.post("/api/set-pais", json={"pais": "España"})
    assert r.status_code == 200
    assert r.json() == {"ok": True, "pais": "España"}


def test_set_idioma_valor_no_soportado_cae_a_es(client, as_session, user_session, monkeypatch):
    as_session(user_session)
    monkeypatch.setattr(auth_router, "guardar_idioma_por_sesion", lambda sesion, idioma: True)
    r = client.post("/api/set-idioma", json={"idioma": "fr"})
    assert r.status_code == 200
    assert r.json() == {"ok": True, "idioma": "es"}


def test_rutas_protegidas_sin_sesion_dan_403(client):
    r = client.post("/api/set-idioma", json={"idioma": "en"})
    assert r.status_code == 403
    assert r.json() == {"error": "Inicia sesión para acceder."}
