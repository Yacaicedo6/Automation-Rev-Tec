"""
Envía un correo de aviso cuando termina la revisión técnico-administrativa.

Usa una cuenta de Gmail con contraseña de aplicación (no la contraseña normal
de la cuenta). Se configura con estas variables en el .env de este proyecto:

    GMAIL_REMITENTE=tu_correo@gmail.com
    GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx
    GMAIL_DESTINATARIO=correo_a_donde_avisar@gmail.com

Si esas variables no están configuradas, no se puede enviar el correo y la
función lo indica en vez de fallar silenciosamente.
"""
import os
import smtplib
import sys
from email.message import EmailMessage

from dotenv import load_dotenv

load_dotenv()


def enviar_notificacion(asunto, cuerpo):
    remitente = os.getenv("GMAIL_REMITENTE")
    clave_app = os.getenv("GMAIL_APP_PASSWORD")
    destinatario = os.getenv("GMAIL_DESTINATARIO")

    if not remitente or not clave_app or not destinatario:
        print("No se pudo enviar el correo: falta configurar GMAIL_REMITENTE, "
              "GMAIL_APP_PASSWORD o GMAIL_DESTINATARIO en el .env.")
        return False

    mensaje = EmailMessage()
    mensaje["Subject"] = asunto
    mensaje["From"] = remitente
    mensaje["To"] = destinatario
    mensaje.set_content(cuerpo)

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as servidor:
            servidor.login(remitente, clave_app)
            servidor.send_message(mensaje)
        print(f"Correo enviado a {destinatario}.")
        return True
    except Exception as error:
        print(f"No se pudo enviar el correo: {error}")
        return False


if __name__ == "__main__":
    asunto = sys.argv[1] if len(sys.argv) > 1 else "Aviso de Automation-Rev-Tec"
    cuerpo = sys.argv[2] if len(sys.argv) > 2 else "Este es un correo de prueba."
    enviar_notificacion(asunto, cuerpo)
