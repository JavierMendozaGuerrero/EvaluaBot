"""Traduce los fallos de la API de Claude a mensajes que el usuario entiende.

Todo el backend llama a Claude a través de `anthropic_client` (backend/clients.py), que
NO es el cliente oficial sino el envoltorio de este módulo: expone `.messages.create(...)`
con la misma firma, pero convierte cualquier excepción del SDK en un ErrorIA cuyo mensaje
ya está escrito para el usuario final. Envolvemos el cliente en lugar de pedir a cada
punto de uso que capture los errores porque así también quedan cubiertos los que se
añadan mañana: ningún sitio puede dejar escapar un error crudo del SDK por olvido.

Quien llame solo tiene que dejar pasar el ErrorIA hasta el usuario (la API lo devuelve
tal cual con un 503; en Slack se publica su mensaje). Capturarlo para sustituirlo por un
texto genérico es justo lo que hay que evitar: tapa el motivo por el que puede pedir ayuda.
"""

import logging
import threading
from contextlib import contextmanager

try:
    import anthropic
except ImportError:  # mismo criterio que clients.py: el paquete puede no estar instalado
    anthropic = None

from .excepciones import ErrorIA


CODIGO_SIN_SALDO = "ia_sin_saldo"
CODIGO_CONFIG = "ia_config"
CODIGO_SATURADA = "ia_saturada"
CODIGO_CONEXION = "ia_conexion"
CODIGO_ENTRADA_LARGA = "ia_entrada_larga"
CODIGO_COLA_LLENA = "ia_cola_llena"
CODIGO_GENERICO = "ia_error"

CONTACTO = "tech@igeneris.com"

MSG_SIN_SALDO = (
    "La API de Claude asociada a esta herramienta se ha quedado sin saldo. "
    f"Contacta con el organizador de la cuenta de Claude ({CONTACTO}) o con el "
    "responsable de la herramienta."
)
MSG_CONFIG = (
    "La API de Claude asociada a esta herramienta no está bien configurada y ha "
    f"rechazado la petición. Contacta con el responsable de la herramienta ({CONTACTO})."
)
MSG_SATURADA = (
    "La IA está saturada en este momento. Espera un par de minutos y vuelve a intentarlo; "
    f"si sigue fallando, avisa al responsable de la herramienta ({CONTACTO})."
)
MSG_CONEXION = (
    "No se ha podido conectar con la IA. Comprueba tu conexión y vuelve a intentarlo; "
    f"si sigue fallando, avisa al responsable de la herramienta ({CONTACTO})."
)
MSG_ENTRADA_LARGA = (
    "Hay demasiada información para que la IA la procese de una vez. Acorta el texto y "
    f"vuelve a intentarlo; si no puedes, avisa al responsable de la herramienta ({CONTACTO})."
)
MSG_GENERICO = (
    "La IA no ha podido responder ahora mismo. Vuelve a intentarlo; si sigue fallando, "
    f"avisa al responsable de la herramienta ({CONTACTO})."
)
MSG_NO_DISPONIBLE = (
    "La IA no está disponible: a esta herramienta le falta la clave de la API de Claude. "
    f"Contacta con el responsable de la herramienta ({CONTACTO})."
)
MSG_COLA_LLENA = (
    "Ahora mismo se están analizando varias evaluaciones a la vez y no ha llegado tu "
    "turno. No es un fallo tuyo: espera unos minutos y vuelve a intentarlo. Lo que ya "
    "tengas confirmado sigue guardado."
)


# ── Cola de análisis anuales ──────────────────────────────────────────────────
#
# El análisis anual es la llamada más cara del sistema: ~60s y todo el historial de la
# persona en el prompt. Sin límite, N usuarios a la vez lanzan N análisis simultáneos y
# la API de Claude empieza a devolver 429 a todos: nadie termina y se paga igual.
# Con un tope, los que sobran esperan turno y entran en cuanto se libera un hueco.
#
# La espera está ACOTADA a propósito. Los endpoints de la API son síncronos, así que
# quien espera turno retiene un hilo del pool de FastAPI. Sin tope, una punta de usuarios
# dejaría todos los hilos esperando y la API entera (login incluido) dejaría de responder.
# Al vencer el plazo se falla con un mensaje que explica que hay cola, en vez de colgarse.
LIMITE_ANALISIS_SIMULTANEOS = 3
ESPERA_MAX_TURNO_S = 180

_semaforo_analisis = threading.BoundedSemaphore(LIMITE_ANALISIS_SIMULTANEOS)
_en_cola = 0
_lock_cola = threading.Lock()


def analisis_en_cola() -> int:
    """Cuántas peticiones están esperando turno (para avisar al usuario)."""
    with _lock_cola:
        return _en_cola


@contextmanager
def turno_analisis_anual():
    """Reserva uno de los huecos de análisis; espera turno si están todos ocupados."""
    global _en_cola
    with _lock_cola:
        _en_cola += 1
    try:
        conseguido = _semaforo_analisis.acquire(timeout=ESPERA_MAX_TURNO_S)
    finally:
        with _lock_cola:
            _en_cola -= 1
    if not conseguido:
        logging.warning(
            "Cola de análisis anual llena: se rechaza tras %ss esperando (%s en cola).",
            ESPERA_MAX_TURNO_S, analisis_en_cola(),
        )
        raise ErrorIA(MSG_COLA_LLENA, CODIGO_COLA_LLENA)
    try:
        yield
    finally:
        _semaforo_analisis.release()


def _sin_saldo(exc) -> bool:
    """¿Es un 'te has quedado sin saldo'?

    La API lo devuelve como 400 invalid_request_error con "credit balance is too low" en
    el mensaje (no como un 402), y como 403 billing_error cuando el bloqueo es de
    facturación. Miramos el texto porque el status por sí solo no distingue este caso de
    cualquier otra petición inválida.
    """
    if getattr(exc, "type", None) == "billing_error":
        return True
    texto = str(getattr(exc, "message", "") or exc).lower()
    return "credit balance" in texto or "insufficient credit" in texto


def traducir_error(exc: Exception) -> ErrorIA:
    """Convierte una excepción del SDK en el ErrorIA que verá el usuario."""
    if isinstance(exc, ErrorIA):
        return exc
    if anthropic is None:
        return ErrorIA(MSG_GENERICO, CODIGO_GENERICO)
    # El saldo se comprueba primero: llega como BadRequestError/PermissionDeniedError y
    # si no lo miramos antes acabaría en las ramas de "petición inválida" o "config".
    if isinstance(exc, anthropic.APIStatusError) and _sin_saldo(exc):
        return ErrorIA(MSG_SIN_SALDO, CODIGO_SIN_SALDO, definitivo=True)
    if isinstance(exc, (anthropic.AuthenticationError, anthropic.PermissionDeniedError)):
        return ErrorIA(MSG_CONFIG, CODIGO_CONFIG, definitivo=True)
    if isinstance(exc, anthropic.RateLimitError):
        return ErrorIA(MSG_SATURADA, CODIGO_SATURADA)
    if isinstance(exc, anthropic.RequestTooLargeError):
        return ErrorIA(MSG_ENTRADA_LARGA, CODIGO_ENTRADA_LARGA)
    if isinstance(exc, (anthropic.InternalServerError, anthropic.OverloadedError)):
        return ErrorIA(MSG_SATURADA, CODIGO_SATURADA)
    # APITimeoutError hereda de APIConnectionError: las dos son "no hemos llegado".
    if isinstance(exc, anthropic.APIConnectionError):
        return ErrorIA(MSG_CONEXION, CODIGO_CONEXION)
    return ErrorIA(MSG_GENERICO, CODIGO_GENERICO)


def _traducido(exc: Exception) -> ErrorIA:
    """Traduce y deja constancia en el log. Llamar siempre dentro de un `except`."""
    error = traducir_error(exc)
    if error.codigo == CODIGO_GENERICO:
        # No sabemos qué ha pasado: la traza es lo único que lo explicará.
        logging.exception("Fallo inesperado llamando a la API de Claude")
    else:
        logging.warning("Fallo de la API de Claude [%s]: %s", error.codigo, exc)
    return error


class _GestorStream:
    """Envoltorio del gestor de contexto de `messages.stream(...)`.

    Con `create` basta con envolver la llamada, pero en streaming la petición se lanza al
    entrar en el contexto y los fallos de red pueden saltar después, mientras se lee. Si
    solo se tradujera la llamada, esos errores llegarían crudos al usuario.
    """

    def __init__(self, gestor_real):
        self._real = gestor_real
        self._stream = None

    def __enter__(self):
        try:
            self._stream = self._real.__enter__()
        except Exception as exc:
            raise _traducido(exc) from exc
        return self

    def __exit__(self, *excepcion):
        return self._real.__exit__(*excepcion)

    def get_final_message(self):
        try:
            return self._stream.get_final_message()
        except Exception as exc:
            raise _traducido(exc) from exc

    def __iter__(self):
        try:
            for evento in self._stream:
                yield evento
        except Exception as exc:
            raise _traducido(exc) from exc

    def __getattr__(self, nombre):
        return getattr(self._stream, nombre)


class _Mensajes:
    def __init__(self, mensajes_reales):
        self._reales = mensajes_reales

    def create(self, *args, **kwargs):
        try:
            return self._reales.create(*args, **kwargs)
        except Exception as exc:
            raise _traducido(exc) from exc

    def stream(self, *args, **kwargs):
        try:
            gestor = self._reales.stream(*args, **kwargs)
        except Exception as exc:
            raise _traducido(exc) from exc
        return _GestorStream(gestor)

    def __getattr__(self, nombre):
        return getattr(self._reales, nombre)


class ClienteIA:
    """Cliente de Claude que falla con mensajes para el usuario en vez de con errores del SDK."""

    def __init__(self, cliente_real):
        self._real = cliente_real
        self.messages = _Mensajes(cliente_real.messages)

    def __getattr__(self, nombre):
        return getattr(self._real, nombre)
