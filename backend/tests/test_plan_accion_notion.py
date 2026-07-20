"""El plan de acción se persiste en Notion (TO-SEE → Planes de acción → 'Plan de acción -
{Nombre}') al pulsar Guardar, y SOLO ahí: lo que genera la IA es borrador local hasta que
el CA lo confirma. Antes vivía únicamente en el JSON de sesión, así que se perdía al
rotar la carpeta local."""

import pytest

from backend import eval_anual_sesion as ea
from backend import notion_service as ns


@pytest.fixture(autouse=True)
def entorno_aislado(tmp_path, monkeypatch):
    monkeypatch.setattr(ea.config, "CARPETA_WEB", str(tmp_path))
    monkeypatch.setattr(
        ea.sk, "obtener_datos_empleado_anual",
        lambda advisee: {"ca": "CA Test", "opiniones_ca": [{"texto": "opinión de prueba"}]},
    )
    monkeypatch.setattr(ea.sk, "interpretar_evaluaciones_anual", lambda emp_data, cargo="", idioma="es": {})
    monkeypatch.setattr(ea.sk, "_formatear_contexto", lambda emp_data: ("", {}))
    monkeypatch.setattr(ea, "_cargo_de", lambda advisee: "Manager")
    monkeypatch.setattr(ea, "_criterios_area", lambda *a, **k: [])
    monkeypatch.setattr(ea, "_secciones", lambda cargo: [("calidad_tecnica", "Calidad técnica")])


@pytest.fixture
def notion_llamado(monkeypatch):
    llamadas = []

    def fake(empleado, texto, ca_nombre=""):
        llamadas.append({"empleado": empleado, "texto": texto, "ca": ca_nombre})
        return {"page_id": "pag-1", "url": "https://notion.so/plan-1"}

    monkeypatch.setattr(ea, "guardar_plan_accion_en_notion", fake)
    return llamadas


def test_guardar_plan_lo_sube_a_notion(notion_llamado):
    ea.iniciar_sesion("ZZZ Test Plan")
    resp = ea.guardar_plan_accion("ZZZ Test Plan", "  - Formarse en arquitectura\n- Liderar un proyecto  ")

    assert len(notion_llamado) == 1
    subido = notion_llamado[0]
    assert subido["empleado"] == "ZZZ Test Plan"
    assert subido["ca"] == "CA Test"
    # Se sube el texto ya normalizado, el mismo que queda en la sesión local.
    assert subido["texto"] == "- Formarse en arquitectura\n- Liderar un proyecto"
    assert resp["notionUrl"] == "https://notion.so/plan-1"


def test_generar_plan_con_ia_no_toca_notion(notion_llamado, monkeypatch):
    """Crear/ajustar el plan con Claude es borrador: no debe escribir en Notion."""
    monkeypatch.setattr(ea, "_generar_plan_accion", lambda *a, **k: "Plan sugerido por la IA")
    ea.iniciar_sesion("ZZZ Test Plan")

    ea.obtener_plan_accion("ZZZ Test Plan", forzar=True)
    ea.pedir_cambios_plan("ZZZ Test Plan", "hazlo más corto")

    assert notion_llamado == []


def test_fallo_de_notion_no_pierde_el_guardado_local(monkeypatch):
    """Notion es best-effort: si falla, el plan sigue guardado en la sesión."""
    def explota(*a, **k):
        raise RuntimeError("Notion caído")

    monkeypatch.setattr(ea, "guardar_plan_accion_en_notion", explota)
    ea.iniciar_sesion("ZZZ Test Plan")

    resp = ea.guardar_plan_accion("ZZZ Test Plan", "Mi plan")

    assert resp["ok"] is True
    assert resp["notionUrl"] == ""
    assert ea.obtener_plan_guardado("ZZZ Test Plan")["plan"] == "Mi plan"


def test_bloques_plan_separa_vinetas_numeracion_y_parrafos():
    bloques = ns._bloques_plan("Objetivos del año\n\n- Uno\n• Dos\n1. Tres\nTexto suelto")
    tipos = [b["type"] for b in bloques]
    assert tipos == ["paragraph", "bulleted_list_item", "bulleted_list_item",
                     "numbered_list_item", "paragraph"]
    # La marca de lista se quita: Notion ya la pinta.
    assert bloques[1]["bulleted_list_item"]["rich_text"][0]["text"]["content"] == "Uno"
    assert bloques[3]["numbered_list_item"]["rich_text"][0]["text"]["content"] == "Tres"


def test_negritas_markdown_se_traducen_a_negrita_de_notion():
    """Notion no interpreta Markdown: sin traducir, los planes salían con \\*\\* literales."""
    rich = ns._rt_markdown("Antes **Dominio de PowerPoint** después")
    assert [r["text"]["content"] for r in rich] == ["Antes ", "Dominio de PowerPoint", " después"]
    assert [r["annotations"]["bold"] for r in rich] == [False, True, False]


def test_linea_que_empieza_por_negrita_no_se_toma_por_vineta():
    """'**Título**' empieza por '*': sin exigir espacio tras la marca, perdía un asterisco."""
    bloques = ns._bloques_plan("**Título en negrita**")
    assert bloques[0]["type"] == "paragraph"
    rich = bloques[0]["paragraph"]["rich_text"]
    assert rich[0]["text"]["content"] == "Título en negrita"
    assert rich[0]["annotations"]["bold"] is True


def test_bloques_plan_respeta_el_limite_de_notion():
    """Notion rechaza >100 hijos por creación: mejor recortar con aviso que fallar entero."""
    bloques = ns._bloques_plan("\n".join(f"- Punto {i}" for i in range(150)))
    assert len(bloques) == 100
    assert "recortado" in bloques[-1]["paragraph"]["rich_text"][0]["text"]["content"]