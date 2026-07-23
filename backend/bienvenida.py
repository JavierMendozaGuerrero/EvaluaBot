"""Mensaje de bienvenida por Slack al dar de alta a un empleado nuevo.

Se envía un DM con una explicación por encima de la web y del bot, más sus
credenciales de acceso (usuario y contraseña temporal). Aislado en su propio
módulo para no acoplar el alta con los flujos grandes de slack_bot.py.
"""

import logging

from . import config
from .clients import slack_app


def _texto_bienvenida(nombre, idioma, username, password, web_url):
    nombre_corto = (nombre or "").split()[0] if (nombre or "").strip() else ""
    hola = f"¡Hola {nombre_corto}! 👋" if nombre_corto else "¡Hola! 👋"

    if idioma == "en":
        hola = f"Hi {nombre_corto}! 👋" if nombre_corto else "Hi! 👋"
        cuerpo = [
            hola,
            "",
            "Welcome to *EvaluaBot*, Igeneris' evaluation tool.",
            "",
            "• *Here on Slack* I (the bot) will message you every so often to ask for quick "
            "ratings about your project teammates and your personal goals. Just reply in the chat.",
            f"• *On the web* you can see your evaluation reports, your goals and your feedback: {web_url}",
        ]
        if password:
            cuerpo += [
                "",
                "*Your login details:*",
                f"• Username: `{username}`",
                f"• Temporary password: `{password}`",
                "",
                "We recommend changing your password the first time you log in "
                "(\"I forgot my password\" button). Any questions, reach out to HR!",
            ]
        elif username:
            cuerpo += [
                "",
                f"You already have an account (username: `{username}`). "
                "Use \"I forgot my password\" on the web to set your password.",
            ]
        return "\n".join(cuerpo)

    # Español (por defecto para es/pt y cualquier otro).
    cuerpo = [
        hola,
        "",
        "Te damos la bienvenida a *EvaluaBot*, la herramienta de evaluaciones de Igeneris.",
        "",
        "• *Por aquí, por Slack*, te escribiré yo (el bot) cada cierto tiempo para pedirte "
        "valoraciones rápidas sobre tus compañeros de proyecto y sobre tus objetivos personales. "
        "Solo tienes que responder en el propio chat.",
        f"• *En la web* puedes ver tus informes de evaluación, tus objetivos y tu feedback: {web_url}",
    ]
    if password:
        cuerpo += [
            "",
            "*Tus datos de acceso a la web:*",
            f"• Usuario: `{username}`",
            f"• Contraseña temporal: `{password}`",
            "",
            "Te recomendamos cambiar la contraseña la primera vez que entres "
            "(botón «He olvidado mi contraseña»). ¡Cualquier duda, escribe a RRHH!",
        ]
    elif username:
        cuerpo += [
            "",
            f"Ya tenías cuenta (usuario: `{username}`). "
            "Usa «He olvidado mi contraseña» en la web para establecer tu contraseña.",
        ]
    return "\n".join(cuerpo)


def enviar_bienvenida(slack_id, nombre, idioma="es", username="", password=None) -> dict:
    """Envía el DM de bienvenida. Devuelve {"enviado": bool, "motivo": str}.

    No lanza: cualquier fallo de Slack se registra y se devuelve como motivo, para que
    el alta del empleado no falle solo porque el mensaje no salió.
    """
    slack_id = (slack_id or "").strip()
    if not slack_id:
        return {"enviado": False, "motivo": "El empleado no tiene Slack ID, no se envió la bienvenida."}
    web_url = config.APP_PUBLIC_URL or ""
    texto = _texto_bienvenida(nombre, idioma or "es", username, password, web_url)
    try:
        dm = slack_app.client.conversations_open(users=[slack_id])
        canal = dm["channel"]["id"]
        slack_app.client.chat_postMessage(channel=canal, text=texto)
        logging.info("Bienvenida enviada por Slack a '%s' (%s)", nombre, slack_id)
        return {"enviado": True, "motivo": "Bienvenida enviada por Slack."}
    except Exception as exc:
        logging.exception("No se pudo enviar la bienvenida por Slack a '%s' (%s)", nombre, slack_id)
        return {"enviado": False, "motivo": f"No se pudo enviar la bienvenida por Slack: {exc}"}
