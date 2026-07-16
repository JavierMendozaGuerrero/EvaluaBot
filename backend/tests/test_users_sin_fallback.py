"""Los usuarios viven en Notion y no hay copia local.

Antes, si Notion fallaba, `cargar_usuarios` se caia a un users.json local. Ese fichero
quedaba congelado en quien estuviera de alta el dia que se creo, asi que un tropiezo de
Notion cambiaba en silencio quien tiene cuenta: dejaba fuera a casi todos y dentro a los
del fichero. Un fallo de red no puede decidir eso.
"""

import pytest

from backend import users


class NotionCaido(Exception):
    pass


def test_cargar_usuarios_propaga_el_fallo_de_notion(monkeypatch):
    """No puede devolver {} ni una lista vieja: eso se leeria como 'no existes'."""
    def peta():
        raise NotionCaido("Notion no responde")

    monkeypatch.setattr(users, "_obtener_o_crear_bbdd_usuarios", peta)
    with pytest.raises(NotionCaido):
        users.cargar_usuarios()


def test_guardar_usuario_propaga_el_fallo_de_notion(monkeypatch):
    """Quien cambia su contrasena tiene que saber que NO se ha guardado."""
    def peta():
        raise NotionCaido("Notion no responde")

    monkeypatch.setattr(users, "_obtener_o_crear_bbdd_usuarios", peta)
    with pytest.raises(NotionCaido):
        users.guardar_usuario({"username": "ana", "salt": "s", "password_hash": "h"})


def test_guardar_usuarios_propaga_el_fallo_de_notion(monkeypatch):
    def peta():
        raise NotionCaido("Notion no responde")

    monkeypatch.setattr(users, "_obtener_o_crear_bbdd_usuarios", peta)
    with pytest.raises(NotionCaido):
        users.guardar_usuarios({"ana": {"username": "ana", "salt": "s", "password_hash": "h"}})


def test_no_queda_rastro_del_fallback_local():
    """Si alguien lo reintroduce, que este test lo cante: era un riesgo de seguridad
    (el users.json commiteado traia un password_hash) y de correccion."""
    for nombre in ("_cargar_usuarios_local", "_guardar_usuarios_local", "_ruta_usuarios"):
        assert not hasattr(users, nombre), f"ha vuelto el fallback local: {nombre}"


def test_autenticar_no_deja_entrar_si_notion_falla(monkeypatch):
    """El caso que de verdad importa: sin usuarios no se autentica a nadie."""
    def peta():
        raise NotionCaido("Notion no responde")

    monkeypatch.setattr(users, "_obtener_o_crear_bbdd_usuarios", peta)
    with pytest.raises(NotionCaido):
        users.autenticar_usuario("irene", "loquesea")
