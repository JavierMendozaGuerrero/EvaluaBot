"""Aportaciones del CA en la evaluación anual asistida.

El CA sabe cosas que nadie registró en Notion. Antes se perdían: la propuesta solo admitía
afirmaciones con cita, así que lo que el CA contaba de su cosecha no llegaba al informe.
Ahora la IA se lo admite —cuando lo que cuenta es un hecho observable y situado, no una
etiqueta— y queda registrado como fuente [C#] firmada por él, citable como cualquier
evaluación. `fecha` y `proyecto` solo etiquetan la fuente y son opcionales: exigirlos
convertía la conversación en un formulario.

Lo que se fija aquí:
  - una aportación admitida se convierte en fuente citable y en evidencia de SU área;
  - los ids los asigna el backend: si la IA numera mal, sus citas se reescriben (si no, el
    informe quedaría con un [C7] que no enlaza a nada en el anexo de fuentes);
  - no se duplican en el panel de "no citadas".
"""

import pytest

from backend import eval_anual_sesion as ea
from backend import skill_informes_anual as sk


def _sesion():
    return {
        "advisee": "Ada L", "ca": "Javier M", "cargo": "Associate",
        "emp_data": {
            "empleado": "Ada L", "ca": "Javier M",
            "evaluaciones": [{"relacion": "superior", "proyecto": "Alfa", "fecha": "2025-06-01",
                              "q1": "4", "q2": "entrega a tiempo", "anonimizado": True,
                              "persona_que_evalua": ""}],
        },
        "areas": {},
        "comentarios": {"comunicacion": {"lider": "Comunica con claridad [E1]"}},
    }


def test_aportacion_admitida_es_fuente_citable_de_su_area():
    sesion = _sesion()
    ea._registrar_aportaciones(sesion, "comunicacion", [
        {"cid": "C1", "fecha": "2025-06", "proyecto": "Beta",
         "texto": "No avisó del retraso hasta la víspera de la entrega"},
    ])

    _, fuentes = ea._emp_y_fuentes(sesion)
    assert fuentes["C1"]["tipo"] == "aportacion_ca"
    assert fuentes["C1"]["evaluador"] == "Javier M"          # firmada por el CA
    assert "Beta" in fuentes["C1"]["label"]
    assert "retraso" in fuentes["C1"]["texto"]

    evidencia = ea._evidencia_area(sesion, sesion["comentarios"], fuentes, "comunicacion")
    assert [e["cid"] for e in evidencia] == ["C1", "E1"]     # ordenadas por fecha

    # Y no se repite abajo, en "lo que la IA no citó".
    assert "C1" not in {e["cid"] for e in ea._fuentes_no_citadas(fuentes, evidencia)}

    # La aportación es de "comunicación": no se cuela como evidencia de otra área.
    otra = ea._evidencia_area(sesion, sesion["comentarios"], fuentes, "calidad_tecnica")
    assert "C1" not in {e["cid"] for e in otra}


def test_los_ids_los_asigna_el_backend_y_se_reescriben_las_citas():
    sesion = _sesion()
    # La IA numera C7 (no le toca) y lo cita así en su mensaje y en la propuesta.
    remapeo = ea._registrar_aportaciones(sesion, "comunicacion", [
        {"cid": "C7", "fecha": "2025-06", "proyecto": "Beta", "texto": "Ocultó el retraso"},
    ])
    assert remapeo == {"C7": "C1"}
    assert ea._aplicar_remapeo("Ocultó el retraso [C7]\nComunica bien [E1]", remapeo) \
        == "Ocultó el retraso [C1]\nComunica bien [E1]"

    # La siguiente aportación sigue la numeración, aunque la IA vuelva a proponer C1.
    remapeo = ea._registrar_aportaciones(sesion, "calidad_tecnica", [
        {"cid": "C1", "fecha": "2025-09", "proyecto": "Alfa", "texto": "Rehízo el modelo"},
    ])
    assert remapeo == {"C1": "C2"}
    assert [a["cid"] for a in ea._aportaciones(sesion)] == ["C1", "C2"]


def test_remapeo_simultaneo_no_se_pisa():
    """Dos aportaciones que se desplazan una sobre la otra (C4->C3, C3->C2): reemplazar
    en cadena convertiría el C4 en C2. Se resuelven en una sola pasada."""
    assert ea._aplicar_remapeo("uno [C3] dos [C4]", {"C4": "C3", "C3": "C2"}) == "uno [C2] dos [C3]"


def test_la_cita_del_ca_sobrevive_al_render_del_informe():
    assert sk._CITE_RE.findall("Ocultó el retraso [C1] pero mejoró [E1][C2]") == ["C1", "E1", "C2"]


@pytest.mark.parametrize("propuestas", [None, [], "texto suelto", [{"texto": "   "}], [None]])
def test_sin_aportaciones_utiles_no_se_registra_nada(propuestas):
    sesion = _sesion()
    assert ea._registrar_aportaciones(sesion, "comunicacion", propuestas) == {}
    assert ea._aportaciones(sesion) == []
