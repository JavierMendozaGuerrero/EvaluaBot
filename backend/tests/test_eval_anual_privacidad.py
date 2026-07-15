"""La jerarquía de privacidad en el informe anual asistido con Claude.

Mismas reglas que los PDFs de fuentes (ver test_pdfs_fuentes_privacidad), aplicadas al
flujo en el que el CA redacta el informe final con Claude.

Se comprueba sobre el CONTEXTO QUE RECIBE CLAUDE, no solo sobre la evidencia que se pinta:
el modelo tiene instrucciones de citar la fuente literal, así que cualquier nombre que
entre en su contexto es extraíble desde el chat. Lo que no entra no se puede escapar.

El informe anual que genera el admin no pasa por _redactar_emp_data y conserva los
nombres: eso se decide aparte (pendiente de la conversación sobre el informe final).
"""

from backend import eval_anual_sesion as ea
from backend import skill_informes_anual as sk

EMP_DATA = {
    "empleado": "Advisee De Prueba",
    "ca": "CA De Prueba",
    "opiniones_ca": [{"fecha": "2026-01-05", "opinion": "CUERPO_OPINION_CA", "url": ""}],
    "evaluaciones": [
        {"proyecto": "Alfa", "q1": "4", "q2": "CUERPO_TOP_DOWN", "relacion": "superior",
         "persona_que_evalua": "Lucia Manager", "nombre": "Lucia Manager", "fecha": "2026-07-08", "url": ""},
        {"proyecto": "Beta", "q1": "2", "q2": "CUERPO_BOTTOM_UP", "relacion": "inferior",
         "persona_que_evalua": "Ana Subordinada", "nombre": "Ana Subordinada", "fecha": "2026-07-09", "url": ""},
        {"proyecto": "Gamma", "q1": "3", "q2": "CUERPO_PEER", "relacion": "igual",
         "persona_que_evalua": "Pedro Peer", "nombre": "Pedro Peer", "fecha": "2026-07-10", "url": ""},
        {"proyecto": "Delta", "q1": "3", "q2": "CUERPO_SIN_RELACION", "relacion": "",
         "persona_que_evalua": "Legacy Sinrel", "nombre": "Legacy Sinrel", "fecha": "2026-07-11", "url": ""},
    ],
    "evals_proyecto": [
        {"proyecto": "Alfa", "evaluador": "Lucia Manager", "respuestas": "CUERPO_PROY_TOP_DOWN",
         "tipo": "Evaluación de managers a miembros del equipo", "fecha": "2026-07-08", "url": ""},
        {"proyecto": "Beta", "evaluador": "Ana Subordinada", "respuestas": "CUERPO_PROY_BOTTOM_UP",
         "tipo": "Evaluación de miembros del equipo a managers", "fecha": "2026-07-09", "url": ""},
    ],
    "seguimiento": [], "barbecho": [], "evals_extra": [], "objetivos": [],
}

NOMBRES = ["Lucia Manager", "Ana Subordinada", "Pedro Peer", "Legacy Sinrel"]
NIVELES = ["managers a miembros", "miembros del equipo a managers", "líder"]


def _todo_lo_que_ve_claude_y_el_ca(emp_data):
    """Contexto del modelo + evidencia del panel, en un solo blob para inspeccionar."""
    contexto, fuentes = sk._formatear_contexto(emp_data)
    return contexto + "\n" + repr(fuentes)


def test_contexto_de_claude_sin_nombres_ni_nivel():
    blob = _todo_lo_que_ve_claude_y_el_ca(ea._redactar_emp_data(EMP_DATA))

    for nombre in NOMBRES:
        assert nombre not in blob
    for nivel in NIVELES:
        assert nivel not in blob


def test_solo_llegan_las_mensuales_de_un_superior():
    blob = _todo_lo_que_ve_claude_y_el_ca(ea._redactar_emp_data(EMP_DATA))

    assert "CUERPO_TOP_DOWN" in blob
    for oculto in ("CUERPO_BOTTOM_UP", "CUERPO_PEER", "CUERPO_SIN_RELACION"):
        assert oculto not in blob


def test_las_fuentes_permitidas_siguen_llegando_enteras():
    """La redacción quita identidad, no contenido: el informe se sigue pudiendo escribir."""
    blob = _todo_lo_que_ve_claude_y_el_ca(ea._redactar_emp_data(EMP_DATA))

    assert "CUERPO_OPINION_CA" in blob
    assert "CUERPO_TOP_DOWN" in blob
    # Evals de proyecto: las dos direcciones, con su proyecto.
    assert "CUERPO_PROY_TOP_DOWN" in blob
    assert "CUERPO_PROY_BOTTOM_UP" in blob
    assert "Alfa" in blob and "Beta" in blob


def test_labels_sin_separadores_huerfanos():
    _, fuentes = sk._formatear_contexto(ea._redactar_emp_data(EMP_DATA))

    for f in fuentes.values():
        assert " ·  · " not in f["label"]
        assert not f["label"].startswith("·")
        assert not f["label"].endswith("·")


def test_el_informe_del_admin_no_esta_redactado():
    """Regresión: _formatear_contexto sin redactar debe conservar nombre, tipo y nivel."""
    contexto, fuentes = sk._formatear_contexto(EMP_DATA)
    blob = contexto + "\n" + repr(fuentes)

    assert "Lucia Manager" in blob
    assert "managers a miembros" in blob
    assert "Alfa · líder · 2026-07-08" in blob


def test_redactar_es_idempotente():
    una_vez = ea._redactar_emp_data(EMP_DATA)
    dos_veces = ea._redactar_emp_data(una_vez)

    assert una_vez == dos_veces
    assert ea._esta_redactado(una_vez)
    assert not ea._esta_redactado(EMP_DATA)


def test_una_sesion_antigua_se_limpia_al_abrirla(monkeypatch, tmp_path):
    """Las sesiones creadas antes de este cambio tienen los nombres guardados en disco."""
    monkeypatch.setattr(ea.config, "CARPETA_WEB", str(tmp_path))
    sesion_vieja = {
        "advisee": "Advisee De Prueba",
        "emp_data": EMP_DATA,
        "comentarios": {"calidad": "Lo dijo Lucia Manager [E1]"},  # generado con datos en bruto
    }
    ea._guardar("advisee_de_prueba", sesion_vieja)

    sesion = ea._leer("advisee_de_prueba")

    assert ea._esta_redactado(sesion["emp_data"])
    # Lo que Claude escribió con los datos en bruto no se conserva: podía citar nombres.
    assert sesion["comentarios"] is None
    blob = _todo_lo_que_ve_claude_y_el_ca(sesion["emp_data"])
    for nombre in NOMBRES:
        assert nombre not in blob
    # Y el fichero queda ya limpio en disco, sin depender de que se vuelva a guardar.
    with open(ea._ruta_sesion("advisee_de_prueba"), encoding="utf-8") as f:
        assert "Lucia Manager" not in f.read()
