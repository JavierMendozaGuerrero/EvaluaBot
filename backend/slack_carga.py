"""
Animación de "cargando" para Slack.

Muestra un mensaje "CARGANDO ..." con una barra de emojis en movimiento mientras
el bot realiza una tarea lenta (leer datos de Notion, generar un resumen con Claude,
etc.). Se usa como context manager y elimina el mensaje al terminar.

    from .slack_carga import AnimacionCargando

    with AnimacionCargando(channel, thread_ts, idioma):
        resultado = tarea_lenta()
    # al salir del bloque el mensaje de carga se elimina automáticamente
"""

import logging
import threading

from .clients import slack_app

_CARGANDO_ANCHO = 10   # nº de celdas de la barra
_CARGANDO_BLOQUE = 3   # nº de celdas encendidas que se desplazan


def frame_cargando(paso: int) -> str:
    """Devuelve un fotograma de la barra: un bloque naranja que se desplaza y envuelve."""
    pos = paso % _CARGANDO_ANCHO
    encendidas = {(pos + k) % _CARGANDO_ANCHO for k in range(_CARGANDO_BLOQUE)}
    return "".join("🟧" if i in encendidas else "⬜" for i in range(_CARGANDO_ANCHO))


class AnimacionCargando:
    """Barra de carga animada en un hilo de Slack. Ver docstring del módulo."""

    def __init__(self, channel: str, thread_ts: str | None = None, idioma: str = "es"):
        self._channel = channel
        self._thread_ts = thread_ts
        self._texto = "LOADING" if idioma == "en" else "CARGANDO"
        self._stop = threading.Event()
        self._hilo: threading.Thread | None = None
        self._ts: str | None = None

    def __enter__(self):
        try:
            resp = slack_app.client.chat_postMessage(
                channel=self._channel,
                thread_ts=self._thread_ts,
                text=f"⏳ *{self._texto} ...*\n{frame_cargando(0)}",
            )
            self._ts = resp["ts"]
            self._hilo = threading.Thread(target=self._bucle, daemon=True)
            self._hilo.start()
        except Exception:
            logging.exception("No se pudo iniciar la animación de carga")
        return self

    def __exit__(self, *exc):
        self._stop.set()
        if self._hilo:
            self._hilo.join(timeout=2)
        if self._ts:
            try:
                slack_app.client.chat_delete(channel=self._channel, ts=self._ts)
            except Exception:
                logging.exception("No se pudo borrar el mensaje de carga")
            self._ts = None
        return False

    def _bucle(self):
        paso = 1
        # _stop.wait devuelve True en cuanto se pide parar; así el sleep es interrumpible.
        while not self._stop.wait(0.9):
            try:
                slack_app.client.chat_update(
                    channel=self._channel,
                    ts=self._ts,
                    text=f"⏳ *{self._texto} ...*\n{frame_cargando(paso)}",
                )
            except Exception:
                # Rate limit u otro fallo: dejamos de animar sin romper el flujo principal.
                break
            paso += 1