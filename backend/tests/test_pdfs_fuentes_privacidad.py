"""La jerarquía de privacidad en los PDFs de fuentes que descarga el CA.

Reglas (decididas con negocio, 15/07/2026):
  - Evals mensuales: el CA solo lee las de un superior. Bottom-to-top, entre iguales y
    sin dirección registrada no salen (ver _solo_top_down).
  - Evals de proyecto: se ven las dos direcciones, el proyecto y —desde el 20/07/2026, a
    petición de negocio— el tipo de evaluación, agrupando por proyecto y dentro de él por
    tipo. Antes el tipo se ocultaba porque delata el nivel del evaluador; se acepta ese
    coste a cambio de que el CA entienda de dónde viene cada bloque.
  - En ninguna de las dos sale el nombre del evaluador salvo para admin (anonimo=False).
  - El PDF combinado hereda exactamente las mismas reglas.

Se comprueba sobre el TEXTO DEL PDF generado, no sobre los dicts intermedios: la fuga que
motivó esto (el nombre del evaluador en las evals de proyecto) vivía en cómo se montaba la
línea 'meta', y un test sobre la capa de datos no la habría visto.
"""

import pytest

from backend import skill_pdfs_fuentes as m

pypdf = pytest.importorskip("pypdf", reason="hace falta pypdf para leer el PDF generado")

pytestmark = pytest.mark.skipif(not m._REPORTLAB_OK, reason="hace falta reportlab")

ADVISEE = "Advisee De Prueba"

MENSUALES = [
    {"proyecto": "Proyecto Alfa", "q1": "4", "q2": "CUERPO_TOP_DOWN", "relacion": "superior",
     "persona_que_evalua": "Lucia Manager", "nombre": "Lucia Manager", "fecha": "2026-07-08"},
    {"proyecto": "Proyecto Beta", "q1": "2", "q2": "CUERPO_BOTTOM_UP", "relacion": "inferior",
     "persona_que_evalua": "Ana Subordinada", "nombre": "Ana Subordinada", "fecha": "2026-07-09"},
    {"proyecto": "Proyecto Gamma", "q1": "3", "q2": "CUERPO_PEER", "relacion": "igual",
     "persona_que_evalua": "Pedro Peer", "nombre": "Pedro Peer", "fecha": "2026-07-10"},
    {"proyecto": "Proyecto Delta", "q1": "3", "q2": "CUERPO_SIN_RELACION", "relacion": "",
     "persona_que_evalua": "Legacy Sinrel", "nombre": "Legacy Sinrel", "fecha": "2026-07-11"},
]

PROYECTO = [
    {"proyecto": "Proyecto Alfa", "evaluador": "Lucia Manager", "respuestas": "CUERPO_PROY_TOP_DOWN",
     "tipo": "Evaluación de managers a miembros del equipo", "fecha": "2026-07-08"},
    {"proyecto": "Proyecto Beta", "evaluador": "Ana Subordinada", "respuestas": "CUERPO_PROY_BOTTOM_UP",
     "tipo": "Evaluación de miembros del equipo a managers", "fecha": "2026-07-09"},
]

NOMBRES = ["Lucia Manager", "Ana Subordinada", "Pedro Peer", "Legacy Sinrel"]


@pytest.fixture
def fuentes(monkeypatch, tmp_path):
    """Fuentes falsas + carpeta de salida temporal: ningún test toca Notion ni deja PDFs."""
    monkeypatch.setattr(m.config, "CARPETA_WEB", str(tmp_path))
    monkeypatch.setattr(m, "obtener_evaluaciones_por_evaluado", lambda a: list(MENSUALES))
    monkeypatch.setattr(m, "obtener_evaluaciones_proyecto_por_evaluado", lambda a: list(PROYECTO))
    monkeypatch.setattr(m, "obtener_comentarios_personales", lambda a: [])
    monkeypatch.setattr(m, "obtener_opiniones_ca_por_advisee", lambda ca, a: [])
    monkeypatch.setattr(m, "obtener_evaluaciones_extra_por_evaluado", lambda a: [])
    monkeypatch.setattr(m, "_ca_de", lambda a: "CA De Prueba")
    return tmp_path


def _texto(carpeta, nombre):
    ruta = carpeta / nombre
    return "\n".join(p.extract_text() for p in pypdf.PdfReader(str(ruta)).pages)


def test_mensuales_solo_top_down_y_sin_nombre(fuentes):
    slug = m.generar_pdf_evals_mensuales(ADVISEE, anonimo=True)
    texto = _texto(fuentes, f"evals_mensuales_{slug}.pdf")

    assert "CUERPO_TOP_DOWN" in texto
    assert "Proyecto Alfa" in texto
    for oculto in ("CUERPO_BOTTOM_UP", "CUERPO_PEER", "CUERPO_SIN_RELACION"):
        assert oculto not in texto
    for nombre in NOMBRES:
        assert nombre not in texto
    # La etiqueta de nivel delataba la posición del evaluador aunque el nombre no saliera.
    for etiqueta in ("líder", "subordinado", "sin nivel"):
        assert etiqueta not in texto


def test_proyecto_ambas_direcciones_agrupadas_por_proyecto_y_tipo_sin_nombre(fuentes):
    slug = m.generar_pdf_evals_proyecto(ADVISEE, anonimo=True)
    texto = _texto(fuentes, f"evals_proyecto_{slug}.pdf")

    assert "CUERPO_PROY_TOP_DOWN" in texto
    assert "CUERPO_PROY_BOTTOM_UP" in texto
    assert "Proyecto Alfa" in texto and "Proyecto Beta" in texto
    assert "Recibida del manager" in texto
    assert "Recibida del equipo" in texto
    # El nombre del evaluador sigue oculto: el tipo se muestra, la identidad no.
    for nombre in NOMBRES:
        assert nombre not in texto


def test_proyecto_ordena_por_proyecto_y_dentro_por_tipo(fuentes):
    """Autoevaluación primero, luego lo recibido; y todo el bloque de un proyecto junto."""
    datos = [
        {"proyecto": "Proyecto Beta", "evaluador": "X", "respuestas": "B_MANAGER",
         "tipo": "Evaluación de managers a miembros del equipo", "fecha": "2026-07-01"},
        {"proyecto": "Proyecto Alfa", "evaluador": "X", "respuestas": "A_MANAGER",
         "tipo": "Evaluación de managers a miembros del equipo", "fecha": "2026-07-02"},
        {"proyecto": "Proyecto Alfa", "evaluador": ADVISEE, "respuestas": "A_AUTO",
         "tipo": "Autoevaluación", "fecha": "2026-07-03"},
    ]
    monkey = m.obtener_evaluaciones_proyecto_por_evaluado
    m.obtener_evaluaciones_proyecto_por_evaluado = lambda a: list(datos)
    try:
        entradas = m._entradas_evals_proyecto(ADVISEE, anonimo=True)
    finally:
        m.obtener_evaluaciones_proyecto_por_evaluado = monkey

    assert [(e["grupo"], e["subgrupo"]) for e in entradas] == [
        ("Proyecto Alfa", "Autoevaluación"),
        ("Proyecto Alfa", "Recibida del manager"),
        ("Proyecto Beta", "Recibida del manager"),
    ]


def test_pdf_combinado_hereda_las_mismas_reglas(fuentes):
    slug = m.generar_pdf_completo(ADVISEE, anonimo=True)
    texto = _texto(fuentes, f"info_completa_{slug}.pdf")

    assert "CUERPO_TOP_DOWN" in texto
    assert "CUERPO_PROY_BOTTOM_UP" in texto
    for oculto in ("CUERPO_BOTTOM_UP", "CUERPO_PEER", "CUERPO_SIN_RELACION"):
        assert oculto not in texto
    for nombre in NOMBRES:
        assert nombre not in texto
    # El combinado agrupa igual que el PDF individual de proyecto.
    assert "Recibida del manager" in texto


def test_admin_ve_nombres_pero_nunca_el_feedback_confidencial(fuentes):
    """anonimo=False es el único caso con nombres; el filtro de dirección no depende del rol."""
    slug = m.generar_pdf_evals_mensuales(ADVISEE, anonimo=False)
    texto = _texto(fuentes, f"evals_mensuales_{slug}.pdf")

    assert "Lucia Manager" in texto
    assert "CUERPO_BOTTOM_UP" not in texto
    assert "CUERPO_PEER" not in texto
