"""Fija el comportamiento de "volver atrás" en la evaluación anual asistida:
reabrir y editar un área ya confirmada debe desconfirmarla, para que
finalizar_sesion() obligue a volver a confirmarla antes de generar el borrador
(y así el informe final nunca se genera con una edición sin validar)."""

import pytest

from backend import eval_anual_sesion as ea


@pytest.fixture(autouse=True)
def entorno_aislado(tmp_path, monkeypatch):
    monkeypatch.setattr(ea.config, "CARPETA_WEB", str(tmp_path))

    monkeypatch.setattr(
        ea.sk,
        "obtener_datos_empleado_anual",
        lambda advisee: {"ca": "CA Test", "opiniones_ca": [{"texto": "opinión de prueba"}]},
    )
    monkeypatch.setattr(ea.sk, "interpretar_evaluaciones_anual", lambda emp_data, cargo="": {})
    monkeypatch.setattr(ea.sk, "_formatear_contexto", lambda emp_data: ("", {}))
    monkeypatch.setattr(ea, "_cargo_de", lambda advisee: "Manager")
    monkeypatch.setattr(ea, "_criterios_area", lambda *a, **k: [])
    monkeypatch.setattr(ea.sk, "guardar_informe_anual_word", lambda *a, **k: None)
    monkeypatch.setattr(ea.sk, "guardar_informe_anual_html", lambda *a, **k: None)
    monkeypatch.setattr(ea, "guardar_log_evaluacion_anual", lambda *a, **k: None)

    def secciones_fake(cargo):
        return [("calidad_tecnica", "Calidad técnica"), ("comunicacion", "Comunicación")]

    monkeypatch.setattr(ea, "_secciones", secciones_fake)


def _responder(advisee, clave, texto, mensaje="ok"):
    """Como responder_area(), pero sin depender de Claude de verdad."""
    return ea._claude_conversa_area  # noqa: solo para referencia en el test siguiente


@pytest.fixture
def mock_claude_conversa(monkeypatch):
    llamadas = []

    def fake(*args, **kwargs):
        llamadas.append(kwargs.get("cargo"))
        return {"mensaje": "Respuesta de la IA", "propuesta": "Bullet acordado [E1]"}

    monkeypatch.setattr(ea, "_claude_conversa_area", fake)
    return llamadas


def test_confirmar_area_la_marca_confirmada(mock_claude_conversa):
    ea.iniciar_sesion("ZZZ Test Volver Atras")
    ea.responder_area("ZZZ Test Volver Atras", "calidad_tecnica", "Mis puntos sobre calidad")
    estado = ea.confirmar_area("ZZZ Test Volver Atras", "calidad_tecnica")
    seccion = next(s for s in estado["secciones"] if s["clave"] == "calidad_tecnica")
    assert seccion["confirmada"] is True


def test_reabrir_area_confirmada_la_desconfirma(mock_claude_conversa):
    """Este es el fix: antes de esto, no había forma de "volver atrás" -- y si la
    hubiera habido sin este cambio, el área seguiría marcada confirmada con
    texto_final desactualizado respecto a la nueva conversación."""
    advisee = "ZZZ Test Volver Atras 2"
    ea.iniciar_sesion(advisee)
    ea.responder_area(advisee, "calidad_tecnica", "Mis puntos iniciales")
    ea.confirmar_area(advisee, "calidad_tecnica")

    # El CA vuelve al área ya confirmada y añade algo más a la conversación.
    ea.responder_area(advisee, "calidad_tecnica", "Se me olvidó mencionar esto otro")

    estado = ea.estado_sesion(advisee)
    seccion = next(s for s in estado["secciones"] if s["clave"] == "calidad_tecnica")
    assert seccion["confirmada"] is False, "reabrir y editar debe desconfirmar el área"


def test_finalizar_bloquea_si_un_area_reabierta_no_se_reconfirma(mock_claude_conversa):
    advisee = "ZZZ Test Volver Atras 3"
    ea.iniciar_sesion(advisee)
    for clave in ("calidad_tecnica", "comunicacion"):
        ea.responder_area(advisee, clave, "puntos")
        ea.confirmar_area(advisee, clave)

    # Reabre y edita una, pero NO vuelve a confirmarla.
    ea.responder_area(advisee, "calidad_tecnica", "un cambio de última hora")

    with pytest.raises(ValueError, match="Calidad técnica"):
        ea.finalizar_sesion(advisee)


def test_reconfirmar_tras_editar_permite_finalizar(mock_claude_conversa):
    advisee = "ZZZ Test Volver Atras 4"
    ea.iniciar_sesion(advisee)
    for clave in ("calidad_tecnica", "comunicacion"):
        ea.responder_area(advisee, clave, "puntos")
        ea.confirmar_area(advisee, clave)

    ea.responder_area(advisee, "calidad_tecnica", "un cambio de última hora")
    ea.confirmar_area(advisee, "calidad_tecnica")  # el CA vuelve a confirmar tras editar

    resultado = ea.finalizar_sesion(advisee)
    assert resultado == {"ok": True, "estado": "completada"}
