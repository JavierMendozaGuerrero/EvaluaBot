"""Inventario de rutas: ninguna ruta nueva puede quedarse sin control de acceso.

El fallo que motiva este archivo: /api/modificar-equipo-proyecto se sirvió durante meses
sin comprobar quién era el manager del proyecto, mientras el endpoint de justo al lado sí
lo comprobaba. Nadie lo vio porque los tests de permisos existentes (test_api_permission_
boundaries.py) enumeran endpoints A MANO, y ese no estaba en la lista.

Estos tests recorren las rutas REALES de la app, así que un endpoint nuevo entra solo en la
prueba. Si añades una ruta y este archivo falla, esa es la señal de que toca decidir
explícitamente su regla de acceso, no un obstáculo que rodear.
"""

import pytest

from backend.api.app import app

# Rutas que se sirven SIN sesión, a propósito. Añadir aquí es una decisión de seguridad:
# significa "cualquiera en internet puede llamar a esto". Justifica cada alta.
RUTAS_PUBLICAS = {
    ("GET", "/api/health"),                    # liveness del contenedor
    ("POST", "/api/login"),
    ("POST", "/api/logout"),                   # idempotente, no revela nada
    ("POST", "/api/register"),                 # paso 1: manda código al email
    ("POST", "/api/register/verify"),          # paso 2: valida el código
    ("POST", "/api/password-reset/request"),
    ("POST", "/api/password-reset/confirm"),
    ("GET", "/api/me"),                        # responde "no hay sesión" sin datos
}

# Sustituciones para rutas con parámetros de path.
_PARAMS = {"nombre_archivo": "no-existe.txt"}


def _todas_las_rutas():
    """(método, path) de cada operación declarada por la app, vía el esquema OpenAPI.

    Se lee del esquema y no de app.routes porque esta versión de FastAPI envuelve los
    routers incluidos en objetos perezosos que no exponen .path/.methods.
    """
    paths = app.openapi()["paths"]
    return sorted(
        (metodo.upper(), path)
        for path, operaciones in paths.items()
        for metodo in operaciones
        if metodo.upper() in ("GET", "POST", "DELETE", "PUT", "PATCH")
    )


def _concretar(path: str) -> str:
    for nombre, valor in _PARAMS.items():
        path = path.replace("{" + nombre + "}", valor)
    return path


RUTAS = _todas_las_rutas()
RUTAS_PROTEGIDAS = [r for r in RUTAS if r not in RUTAS_PUBLICAS]


def test_hay_rutas_que_inventariar():
    """Red de seguridad del propio inventario: si el descubrimiento se rompe y devuelve
    una lista vacía, los tests de abajo pasarían sin comprobar nada."""
    assert len(RUTAS) > 50, f"Se esperaban ~90 rutas, se descubrieron {len(RUTAS)}"


def test_las_rutas_publicas_declaradas_existen():
    """Impide que RUTAS_PUBLICAS acumule entradas fantasma de endpoints ya borrados que,
    si alguien recrea el path más tarde, lo dejarían público sin querer."""
    huerfanas = RUTAS_PUBLICAS - set(RUTAS)
    assert not huerfanas, f"RUTAS_PUBLICAS menciona rutas que ya no existen: {huerfanas}"


@pytest.mark.parametrize("metodo,path", RUTAS_PROTEGIDAS, ids=lambda v: str(v))
def test_toda_ruta_no_publica_exige_sesion(client, metodo, path):
    """Sin cabecera de sesión, todo lo que no esté en RUTAS_PUBLICAS debe rechazar.

    No comprueba QUÉ permiso pide cada endpoint (eso son los tests específicos), solo que
    ninguno responde datos a un anónimo.
    """
    respuesta = client.request(metodo, _concretar(path), json={})
    assert respuesta.status_code in (401, 403), (
        f"{metodo} {path} respondió {respuesta.status_code} sin sesión. "
        "Si es intencionadamente público, añádelo a RUTAS_PUBLICAS con su porqué; "
        "si no, le falta Depends(require_session)."
    )
