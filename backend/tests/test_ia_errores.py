"""Fallos de la API de Claude → mensajes que el usuario entiende.

Lo que se protege aquí es que un fallo de la API (sobre todo quedarse sin saldo) nunca
llegue al usuario como un error de código ni como un "inténtalo de nuevo" que no le sirve
de nada. Las excepciones se construyen como las construye el SDK de verdad —a partir de
una respuesta HTTP y del cuerpo que devuelve la API— para que el test siga valiendo si
cambian las clases internas del SDK.
"""

import httpx
import pytest

import anthropic

from backend import ia
from backend.excepciones import ErrorIA
from backend.i18n import IDIOMAS_SOPORTADOS, TEXTOS, texto_error_ia


def _respuesta(status: int) -> httpx.Response:
    return httpx.Response(status, request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"))


def _cuerpo(tipo: str, mensaje: str) -> dict:
    return {"type": "error", "error": {"type": tipo, "message": mensaje}}


# El mensaje literal con el que la API avisa de que se ha agotado el saldo.
MENSAJE_SIN_SALDO = (
    "Your credit balance is too low to access the Anthropic API. Please go to Plans & "
    "Billing to upgrade or purchase credits."
)


def _error_sin_saldo() -> anthropic.BadRequestError:
    """Sin saldo tal y como llega: un 400 invalid_request_error, no un 402."""
    return anthropic.BadRequestError(
        MENSAJE_SIN_SALDO, response=_respuesta(400), body=_cuerpo("invalid_request_error", MENSAJE_SIN_SALDO)
    )


class _MensajesQueFallan:
    """Las `messages` del SDK, pero fallando siempre. Cuenta las llamadas para poder
    comprobar quién reintenta."""

    def __init__(self, excepcion):
        self._excepcion = excepcion
        self.llamadas = 0

    def create(self, **kwargs):
        self.llamadas += 1
        raise self._excepcion

    def stream(self, **kwargs):
        # El SDK lanza al entrar en el contexto, no al construirlo (por eso ia.py envuelve
        # el gestor y no solo la llamada).
        self.llamadas += 1
        raise self._excepcion


class _ClienteQueFalla:
    def __init__(self, excepcion):
        self.messages = _MensajesQueFallan(excepcion)


# ── Traducción ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "excepcion, codigo_esperado, definitivo_esperado",
    [
        (_error_sin_saldo(), ia.CODIGO_SIN_SALDO, True),
        (
            anthropic.PermissionDeniedError(
                "blocked", response=_respuesta(403), body=_cuerpo("billing_error", "blocked")
            ),
            ia.CODIGO_SIN_SALDO,
            True,
        ),
        (anthropic.AuthenticationError("invalid x-api-key", response=_respuesta(401), body=None), ia.CODIGO_CONFIG, True),
        (anthropic.RateLimitError("slow down", response=_respuesta(429), body=None), ia.CODIGO_SATURADA, False),
        (anthropic.InternalServerError("overloaded", response=_respuesta(529), body=None), ia.CODIGO_SATURADA, False),
        (
            anthropic.RequestTooLargeError("too big", response=_respuesta(413), body=None),
            ia.CODIGO_ENTRADA_LARGA,
            False,
        ),
        (anthropic.APIConnectionError(request=httpx.Request("POST", "https://api.anthropic.com")), ia.CODIGO_CONEXION, False),
        (anthropic.APITimeoutError(request=httpx.Request("POST", "https://api.anthropic.com")), ia.CODIGO_CONEXION, False),
        # Un bug nuestro (no del SDK) también tiene que salir explicado, no en crudo.
        (TypeError("'NoneType' object is not subscriptable"), ia.CODIGO_GENERICO, False),
    ],
)
def test_cada_fallo_de_la_api_se_traduce(excepcion, codigo_esperado, definitivo_esperado):
    error = ia.traducir_error(excepcion)
    assert isinstance(error, ErrorIA)
    assert error.codigo == codigo_esperado
    assert error.definitivo is definitivo_esperado


def test_sin_saldo_dice_que_es_el_saldo_y_a_quien_avisar():
    """El texto es lo único que ve el usuario: tiene que poder actuar con él."""
    error = ia.traducir_error(_error_sin_saldo())
    assert "sin saldo" in str(error).lower()
    assert ia.CONTACTO in str(error)


def test_ningun_mensaje_filtra_texto_crudo_del_sdk():
    error = ia.traducir_error(_error_sin_saldo())
    assert "credit balance" not in str(error).lower()
    assert "Plans & Billing" not in str(error)


def test_un_error_ia_ya_traducido_no_se_vuelve_a_envolver():
    original = ErrorIA("mensaje ya escrito", ia.CODIGO_SIN_SALDO, definitivo=True)
    assert ia.traducir_error(original) is original


# ── Envoltorio del cliente ────────────────────────────────────────────────────

def test_el_cliente_convierte_el_fallo_y_no_deja_escapar_el_error_del_sdk():
    cliente = ia.ClienteIA(_ClienteQueFalla(_error_sin_saldo()))
    with pytest.raises(ErrorIA) as excinfo:
        cliente.messages.create(model="claude-sonnet-4-6", max_tokens=10, messages=[])
    assert excinfo.value.codigo == ia.CODIGO_SIN_SALDO
    # La excepción original se conserva encadenada: es lo que explica el fallo en el log.
    assert isinstance(excinfo.value.__cause__, anthropic.BadRequestError)


def test_el_cliente_envuelto_sigue_siendo_truthy():
    """Medio backend hace `if not anthropic_client`; si el envoltorio fuese falsy, esas
    ramas dirían "no hay IA" con la IA configurada."""
    assert bool(ia.ClienteIA(_ClienteQueFalla(_error_sin_saldo())))


# ── Que nadie se lo trague por el camino ──────────────────────────────────────

def test_el_resumen_de_evaluacion_deja_pasar_el_motivo(monkeypatch):
    """Este es el resumen que pide un CA desde Slack. Antes cualquier excepción acababa
    en un "no se pudo generar el resumen" que no decía nada; ahora tiene que llegar el
    ErrorIA con el motivo."""
    from backend import skill_resumen_evaluacion as skill

    monkeypatch.setattr(skill, "anthropic_client", ia.ClienteIA(_ClienteQueFalla(_error_sin_saldo())))
    with pytest.raises(ErrorIA) as excinfo:
        skill.generar_resumen_evaluacion("Ana", "Analista", "unas evaluaciones", "es")
    assert excinfo.value.codigo == ia.CODIGO_SIN_SALDO


def test_sin_saldo_no_gasta_una_segunda_llamada_reintentando_sin_cache(monkeypatch):
    """El informe anual reintenta sin prompt caching cuando la primera llamada falla, por
    si la caché no estuviera soportada. Sin saldo el reintento falla igual: solo gasta
    otra llamada y tarda más en decírselo al usuario."""
    from backend import skill_informes_anual as skill

    cliente_falso = _ClienteQueFalla(_error_sin_saldo())
    monkeypatch.setattr(skill, "anthropic_client", ia.ClienteIA(cliente_falso))
    with pytest.raises(ErrorIA) as excinfo:
        skill.interpretar_evaluaciones_anual(
            {"empleado": "Ana", "evaluaciones": []}, cargo="Analista", criterios="", idioma="es"
        )
    assert excinfo.value.codigo == ia.CODIGO_SIN_SALDO
    assert cliente_falso.messages.llamadas == 1, "reintentó sin caché pese a estar sin saldo"


# ── Idiomas ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "codigo",
    [
        ia.CODIGO_SIN_SALDO,
        ia.CODIGO_CONFIG,
        ia.CODIGO_SATURADA,
        ia.CODIGO_CONEXION,
        ia.CODIGO_ENTRADA_LARGA,
        ia.CODIGO_GENERICO,
        "ia_no_configurada",
    ],
)
def test_cada_codigo_esta_traducido_a_todos_los_idiomas(codigo):
    """Un CA en inglés o portugués también tiene que entender por qué ha fallado."""
    entrada = TEXTOS.get(f"ia.{codigo}")
    assert entrada is not None, f"falta la clave i18n 'ia.{codigo}'"
    for idioma in IDIOMAS_SOPORTADOS:
        assert entrada.get(idioma), f"'ia.{codigo}' sin traducción en '{idioma}'"


def test_un_codigo_sin_traducir_cae_al_mensaje_del_error_no_a_la_clave():
    """Si mañana se añade un código y se olvida traducirlo, el usuario debe ver el texto
    en español, no 'ia.lo_que_sea'."""
    assert texto_error_ia("codigo_nuevo_sin_traducir", "mensaje en español", "en") == "mensaje en español"
