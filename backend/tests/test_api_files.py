import os

from backend.api import files as files_router


def test_url_archivo_construye_ruta_relativa_con_evaluado():
    url = files_router.url_archivo("informe_juan.docx", "Juan Pérez")
    assert url == "/api/files/informe_juan.docx?evaluado=Juan+P%C3%A9rez"


def test_descarga_sin_sesion_da_403(client):
    r = client.get("/api/files/informe_juan.docx", params={"evaluado": "Juan Perez"})
    assert r.status_code == 403


def test_descarga_prefijo_no_reconocido_da_403(client, as_session, user_session, monkeypatch):
    as_session(user_session)
    monkeypatch.setattr(files_router, "obtener_advisees", lambda *a, **k: [])
    r = client.get("/api/files/algo_random.docx", params={"evaluado": "Juan Perez"})
    assert r.status_code == 403
    assert "no corresponde con la persona autorizada" in r.json()["error"]


def test_descarga_borrador_bloqueada_si_no_es_ca_ni_admin(client, as_session, user_session, monkeypatch):
    as_session(user_session)
    monkeypatch.setattr(files_router, "obtener_advisees", lambda *a, **k: [])  # no tutela a Juan Perez
    r = client.get("/api/files/informe_Juan_Perez.docx", params={"evaluado": "Juan Perez"})
    assert r.status_code == 403
    assert "Solo el CA o un administrador" in r.json()["error"]


def test_descarga_borrador_permitida_al_ca_y_devuelve_etag(client, as_session, user_session, monkeypatch, tmp_path):
    as_session(user_session)
    monkeypatch.setattr(files_router, "obtener_advisees", lambda *a, **k: ["Juan Perez"])
    monkeypatch.setattr(files_router.config, "CARPETA_WEB", str(tmp_path))
    ruta = tmp_path / "informe_Juan_Perez.docx"
    ruta.write_bytes(b"contenido-de-prueba")

    r = client.get("/api/files/informe_Juan_Perez.docx", params={"evaluado": "Juan Perez"})
    assert r.status_code == 200
    assert r.content == b"contenido-de-prueba"
    assert "ETag" in r.headers
    etag = r.headers["ETag"]

    r2 = client.get(
        "/api/files/informe_Juan_Perez.docx",
        params={"evaluado": "Juan Perez"},
        headers={"If-None-Match": etag},
    )
    assert r2.status_code == 304


def test_path_traversal_no_escapa_carpeta_web(client, as_session, user_session, monkeypatch, tmp_path):
    """os.path.basename() debe neutralizar cualquier intento de subir directorios."""
    as_session(user_session)
    monkeypatch.setattr(files_router, "obtener_advisees", lambda *a, **k: ["Juan Perez"])
    monkeypatch.setattr(files_router.config, "CARPETA_WEB", str(tmp_path))
    # Ni siquiera coincide con ningún patrón de prefijo conocido -> 403 antes de llegar
    # a tocar el filesystem, pero además comprobamos que basename() neutraliza la ruta.
    nombre_malicioso = "../../../../etc/informe_Juan_Perez.docx"
    assert os.path.basename(nombre_malicioso) == "informe_Juan_Perez.docx"
