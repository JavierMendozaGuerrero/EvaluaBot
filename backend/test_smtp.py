import argparse
import smtplib
from email.message import EmailMessage

from . import config


def enviar_prueba(destinatario):
    if not config.SMTP_HOST or not config.SMTP_FROM:
        raise RuntimeError("Faltan SMTP_HOST y SMTP_FROM en .env.")

    mensaje = EmailMessage()
    mensaje["Subject"] = "Prueba SMTP"
    mensaje["From"] = config.SMTP_FROM
    mensaje["To"] = destinatario
    mensaje.set_content("Si recibes este correo, la configuracion SMTP funciona.")

    with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=20) as smtp:
        if config.SMTP_USE_TLS:
            smtp.starttls()
        if config.SMTP_USER or config.SMTP_PASSWORD:
            smtp.login(config.SMTP_USER, config.SMTP_PASSWORD)
        smtp.send_message(mensaje)


def main():
    parser = argparse.ArgumentParser(description="Envia un correo de prueba con la configuracion SMTP.")
    parser.add_argument("destinatario", help="Email que recibira la prueba.")
    args = parser.parse_args()
    enviar_prueba(args.destinatario)
    print(f"Correo de prueba enviado a {args.destinatario}")


if __name__ == "__main__":
    main()
