"""Al sacar a alguien de un proyecto activo se le avisa por DM de Slack.

Antes la baja era silenciosa: al miembro le desaparecían las evaluaciones de la web
sin explicación. El aviso es del mismo tipo que el de activación, pero además le dice
a quién reclamar si ha sido un error.

Contrato que se fija aquí:
  - se manda el DM con la clave web.eval_proyecto_eliminado y el nombre del manager,
  - en el idioma de QUIEN RECIBE el DM, no en el de quien lo eliminó,
  - y un fallo de Slack nunca convierte una baja correcta en un error para el manager.
"""

import backend.project_evals as pe


class _SlackFalso:
    """Sustituye a slack_app.client y apunta lo que se ha enviado."""

    def __init__(self, falla=False):
        self.enviados = []
        self.falla = falla

    def conversations_open(self, users):
        if self.falla:
            raise RuntimeError("Slack caído")
        return {"channel": {"id": f"D-{users[0]}"}}

    def chat_postMessage(self, channel, text, **kwargs):
        self.enviados.append({"channel": channel, "text": text})
        return {"ok": True}


def _montar(monkeypatch, slack, idioma="es", empleados=None):
    """Deja eliminar_miembro_proyecto sin Notion real y con un Slack de mentira."""
    monkeypatch.setattr(pe, "_obtener_o_crear_bbdd_activaciones", lambda: "db-1")
    monkeypatch.setattr(pe, "_query_bbdd", lambda *a, **k: {"results": [{"id": "pagina-1"}]})
    monkeypatch.setattr(pe.notion.pages, "update", lambda **k: {"ok": True})
    # El hilo de limpieza toca Notion de verdad: fuera en los tests.
    monkeypatch.setattr(pe.threading, "Thread", lambda *a, **k: type("T", (), {"start": lambda self: None})())
    monkeypatch.setattr(pe, "_invalidar_cache_activaciones", lambda: None)
    monkeypatch.setattr(pe, "_invalidar_cache_completadas", lambda proyecto: None)
    monkeypatch.setattr(pe, "obtener_registros_empleados", lambda: empleados if empleados is not None else [
        {"nombre": "Luis Analyst", "id_usuario": "U-LUIS"},
    ])
    monkeypatch.setattr(pe, "idioma_por_slack_id", lambda slack_id: idioma)
    # slack_app.client es una property de solo lectura: se sustituye el app entero.
    monkeypatch.setattr(pe, "slack_app", type("App", (), {"client": slack})())


def test_la_baja_manda_dm_con_proyecto_y_manager(monkeypatch):
    slack = _SlackFalso()
    _montar(monkeypatch, slack)

    assert pe.eliminar_miembro_proyecto("Proyecto X", "Luis Analyst", "es", "Marta Manager") == {"ok": True}

    assert len(slack.enviados) == 1
    assert slack.enviados[0]["channel"] == "D-U-LUIS"
    texto = slack.enviados[0]["text"]
    assert "Proyecto X" in texto
    assert "Marta Manager" in texto


def test_el_dm_va_en_el_idioma_de_quien_lo_recibe(monkeypatch):
    # Manager en español (idioma='es' en la llamada), miembro portugués: manda el suyo.
    slack = _SlackFalso()
    _montar(monkeypatch, slack, idioma="pt")

    pe.eliminar_miembro_proyecto("Proyecto X", "Luis Analyst", "es", "Marta Manager")

    assert "Foste removido" in slack.enviados[0]["text"]


def test_sin_manager_usa_el_texto_generico_y_no_deja_un_hueco(monkeypatch):
    slack = _SlackFalso()
    _montar(monkeypatch, slack)

    pe.eliminar_miembro_proyecto("Proyecto X", "Luis Analyst", "es")

    texto = slack.enviados[0]["text"]
    assert "responsable del proyecto" in texto
    assert "**" not in texto  # no queda un *{manager}* vacío


def test_si_el_empleado_no_tiene_slack_la_baja_sigue_siendo_correcta(monkeypatch):
    slack = _SlackFalso()
    _montar(monkeypatch, slack, empleados=[{"nombre": "Luis Analyst", "id_usuario": ""}])

    assert pe.eliminar_miembro_proyecto("Proyecto X", "Luis Analyst", "es", "Marta Manager") == {"ok": True}
    assert slack.enviados == []


def test_si_slack_falla_la_baja_no_se_reporta_como_error(monkeypatch):
    # La persona ya está fuera del proyecto en Notion: devolver error haría que el
    # manager lo reintentase creyendo que no se ha aplicado.
    slack = _SlackFalso(falla=True)
    _montar(monkeypatch, slack)

    assert pe.eliminar_miembro_proyecto("Proyecto X", "Luis Analyst", "es", "Marta Manager") == {"ok": True}
