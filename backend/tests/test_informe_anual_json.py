"""El modelo, aunque se le pida solo JSON, a veces envuelve la respuesta en prosa.

Esto pasó en producción: antepuso ~25.000 caracteres de razonamiento en inglés ("I need to
analyze all sources...") antes del JSON. El `json.loads` pelado reventaba en el carácter 0
(la primera letra ya no es JSON) y, como JSONDecodeError hereda de ValueError, el handler
de ValueError de la API le pintaba al usuario el mensaje crudo de Python.
"""

from backend.skill_informes_anual import _extraer_json_objeto


PREAMBULO_REAL = (
    "I need to analyze all sources carefully before generating the JSON.\n\n"
    "**Sources inventory:**\n"
    '- E4: Monthly evaluation (jul 26) - "prueba jerarquia" - test content\n'
    "- E1: mantener foco, simplificar flujo, anticipar bloqueos\n\n"
    "**Dimension mapping:**\n\n**gestion_proyecto:**\n"
)
INFORME = '{"gestion_proyecto": {"lider": "Entrega a tiempo [E1]"}, "resultado": "Buen ano [E1]"}'


def test_json_limpio():
    assert _extraer_json_objeto(INFORME)["resultado"] == "Buen ano [E1]"


def test_json_con_fence_markdown():
    assert _extraer_json_objeto("```json\n" + INFORME + "\n```")["resultado"] == "Buen ano [E1]"


def test_preambulo_de_razonamiento_antes_del_json():
    """El caso real de producción."""
    assert _extraer_json_objeto(PREAMBULO_REAL + INFORME)["resultado"] == "Buen ano [E1]"


def test_comentario_despues_del_json():
    assert _extraer_json_objeto(INFORME + "\n\nEspero que te sirva.")["resultado"] == "Buen ano [E1]"


def test_ignora_llaves_sueltas_en_la_prosa():
    """Recortar por la primera '{' no vale: la prosa puede traer llaves suyas."""
    texto = "Analisis: el foco {ojo, llave suelta} y sigo.\n" + INFORME
    assert _extraer_json_objeto(texto)["resultado"] == "Buen ano [E1]"


def test_ignora_json_de_ejemplo_en_el_razonamiento():
    """Si el razonamiento incluye un JSON pequeño, hay que quedarse con el informe."""
    texto = 'Formato de ejemplo: {"no_soportadas": []}\nAhora el informe:\n' + INFORME
    assert _extraer_json_objeto(texto)["resultado"] == "Buen ano [E1]"


def test_llaves_y_comillas_escapadas_dentro_de_cadenas():
    texto = '{"a": "texto con } y { dentro", "b": "dice \\"hola}\\" y sigue"}'
    assert list(_extraer_json_objeto(texto)) == ["a", "b"]


def test_sin_json_devuelve_none():
    assert _extraer_json_objeto("Lo siento, no puedo ayudarte con eso.") is None


def test_json_truncado_devuelve_none():
    """Si se corta a medias no hay '}' pareja: mejor fallar que inventar."""
    assert _extraer_json_objeto('{"a": "sin cerrar') is None


def test_respuesta_vacia_devuelve_none():
    assert _extraer_json_objeto("") is None
