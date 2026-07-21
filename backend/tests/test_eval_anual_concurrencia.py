"""Concurrencia de la sesión anual: nada de esto se ve hasta que falla en produccion.

La sesión vive en un JSON que se lee entero, se modifica y se reescribe entero. Sin
protección, dos peticiones a la vez sobre la misma persona se pisan, y el análisis de
~60s (de pago) se lanza dos veces.
"""

import json
import threading
import time
from unittest.mock import patch

import pytest

from backend import eval_anual_sesion as ses
from backend import ia
from backend.excepciones import ErrorIA


@pytest.fixture
def carpeta(tmp_path):
    with patch.object(ses.config, "CARPETA_WEB", str(tmp_path)):
        yield tmp_path


def test_guardar_es_atomico_no_deja_json_a_medias(carpeta):
    """Si el guardado peta a mitad, debe quedar la versión ANTERIOR intacta.

    Con el open(w) de antes, el fichero quedaba truncado y el CA perdia la sesion entera.
    """
    ses._guardar("laura", {"paso": "bueno", "areas": {"a": 1}})

    # Un set no es serializable: revienta a mitad de escritura, como un corte del proceso.
    with pytest.raises(Exception):
        ses._guardar("laura", {"paso": "malo", "roto": {1, 2, 3}})

    con = json.loads((carpeta / "sesion_anual_laura.json").read_text(encoding="utf-8"))
    assert con["paso"] == "bueno", "el guardado fallido ha corrompido la sesion anterior"


def test_no_deja_temporales_tirados_si_falla(carpeta):
    with pytest.raises(Exception):
        ses._guardar("laura", {"roto": {1, 2}})
    assert list(carpeta.glob("*.tmp")) == [], "quedan ficheros temporales sin limpiar"


def test_lectores_libres_contra_un_escritor_nunca_pierden_la_sesion(carpeta):
    """La forma REAL del sistema: los que escriben van serializados por el lock del
    evaluado (`_con_lock_sesion`), pero los de solo lectura (estado_sesion) no.

    Ese cruce lector/escritor es el que importa: si un lector pilla el os.replace a
    medias, `_leer` devuelve None y el CA ve "No hay sesion iniciada" con su sesion
    intacta en disco. En Windows el cruce da PermissionError; en Linux no.
    """
    ses._guardar("laura", {"n": 0, "advisee": "Laura"})
    errores, perdidas = [], []
    parar = threading.Event()

    def escritor():
        try:
            for i in range(40):
                with ses._lock_de("laura"):  # como hace _con_lock_sesion
                    ses._guardar("laura", {"n": i, "advisee": "Laura", "relleno": "x" * 500})
        except Exception as e:
            errores.append(e)
        finally:
            parar.set()

    def lector():
        try:
            while not parar.is_set():
                if ses._leer("laura") is None:
                    perdidas.append(1)  # el CA veria "No hay sesion iniciada"
        except Exception as e:
            errores.append(e)

    hilos = [threading.Thread(target=escritor)] + [threading.Thread(target=lector) for _ in range(8)]
    [h.start() for h in hilos]
    [h.join() for h in hilos]
    assert not errores, f"excepcion durante el acceso concurrente: {errores[:2]}"
    assert not perdidas, "una lectura devolvio None: el CA habria perdido la sesion"
    assert json.loads((carpeta / "sesion_anual_laura.json").read_text(encoding="utf-8"))


def test_dos_peticiones_a_la_vez_solo_lanzan_un_analisis(carpeta):
    """El caso del F5: dos GET del mismo evaluado no deben pagar dos analisis.

    Es el fallo mas caro: cada analisis son ~60s y una llamada facturada.
    """
    ses._guardar("laura", {"advisee": "Laura", "cargo": "Associate", "emp_data": {"empleado": "Laura"}})
    llamadas = []

    def analisis_lento(emp_data, cargo="", idioma="es", **kw):
        llamadas.append(cargo)
        time.sleep(0.3)  # ventana para que el otro hilo entre si no hubiera lock
        return {"resultado": "ok", "_fuentes": {}}

    vistos = []

    def pide():
        with ses._lock_de("laura"):
            sesion = ses._leer("laura")
            vistos.append(ses._asegurar_comentarios("laura", sesion))

    with patch.object(ses.sk, "interpretar_evaluaciones_anual", analisis_lento):
        hilos = [threading.Thread(target=pide) for _ in range(4)]
        [h.start() for h in hilos]
        [h.join() for h in hilos]

    assert len(llamadas) == 1, f"se han lanzado {len(llamadas)} analisis en vez de 1"
    assert all(v["resultado"] == "ok" for v in vistos), "alguien se quedo sin comentarios"


def test_cola_limita_los_analisis_simultaneos():
    """Con el tope en 3, nunca puede haber 4 analisis a la vez contra Claude."""
    a_la_vez, pico = 0, 0
    lock = threading.Lock()

    def trabajo():
        nonlocal a_la_vez, pico
        with ia.turno_analisis_anual():
            with lock:
                a_la_vez += 1
                pico = max(pico, a_la_vez)
            time.sleep(0.15)
            with lock:
                a_la_vez -= 1

    hilos = [threading.Thread(target=trabajo) for _ in range(12)]
    [h.start() for h in hilos]
    [h.join() for h in hilos]
    assert pico <= ia.LIMITE_ANALISIS_SIMULTANEOS, f"hubo {pico} analisis a la vez"


def test_cola_llena_avisa_al_usuario_en_vez_de_colgarse():
    """Si no llega el turno, el usuario debe leer que hay muchas a la vez, no esperar eternamente."""
    with patch.object(ia, "ESPERA_MAX_TURNO_S", 0.05):
        ocupados = [ia._semaforo_analisis.acquire() for _ in range(ia.LIMITE_ANALISIS_SIMULTANEOS)]
        try:
            with pytest.raises(ErrorIA) as exc:
                with ia.turno_analisis_anual():
                    pass
        finally:
            for _ in ocupados:
                ia._semaforo_analisis.release()

    assert exc.value.codigo == ia.CODIGO_COLA_LLENA
    assert not exc.value.definitivo, "es pasajero: debe invitar a reintentar"
    assert "varias evaluaciones" in str(exc.value)


def test_el_hueco_se_libera_aunque_el_analisis_falle():
    """Un fallo dentro no puede dejar el hueco pillado para siempre."""
    libres_antes = ia._semaforo_analisis._value
    with pytest.raises(RuntimeError):
        with ia.turno_analisis_anual():
            raise RuntimeError("la IA peto")
    assert ia._semaforo_analisis._value == libres_antes, "el hueco se ha quedado sin liberar"
