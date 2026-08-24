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
import mimetypes
import os
import smtplib
import sys
from email.message import EmailMessage
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def enviar_notificacion(asunto, cuerpo, adjuntos=None, destinatario=None):
    """
    adjuntos: lista opcional de rutas de archivo para adjuntar (por ejemplo,
    el log de una verificación del panel). destinatario: si no se indica,
    se usa GMAIL_DESTINATARIO del .env (el aviso de escritorio de siempre);
    el panel web lo pasa explícito para avisarle a cada quien a su propio
    correo.
    """
    remitente = os.getenv("GMAIL_REMITENTE")
    clave_app = os.getenv("GMAIL_APP_PASSWORD")
    destinatario = destinatario or os.getenv("GMAIL_DESTINATARIO")

    if not remitente or not clave_app or not destinatario:
        print("No se pudo enviar el correo: falta configurar GMAIL_REMITENTE, "
              "GMAIL_APP_PASSWORD o el destinatario.")
        return False

    mensaje = EmailMessage()
    mensaje["Subject"] = asunto
    mensaje["From"] = remitente
    mensaje["To"] = destinatario
    mensaje.set_content(cuerpo)

    for ruta_adjunto in (adjuntos or []):
        ruta_adjunto = Path(ruta_adjunto)
        if not ruta_adjunto.is_file():
            continue
        tipo_detectado, _ = mimetypes.guess_type(ruta_adjunto.name)
        tipo_principal, tipo_secundario = (tipo_detectado.split("/", 1) if tipo_detectado else ("application", "octet-stream"))
        mensaje.add_attachment(
            ruta_adjunto.read_bytes(),
            maintype=tipo_principal,
            subtype=tipo_secundario,
            filename=ruta_adjunto.name,
        )

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as servidor:
            servidor.login(remitente, clave_app)
            servidor.send_message(mensaje)
        print(f"Correo enviado a {destinatario}.")
        return True
    except Exception as error:
        print(f"No se pudo enviar el correo: {error}")
        return False


def notificar_resultado_revision(resultados, nombres_totales):
    """
    Envía la notificación solo en los dos casos que realmente importan:
    las 5 verificaciones corrieron y todas terminaron bien, o corrieron
    y todas terminaron con error. Un resultado mixto (unas sí, otras no)
    o incompleto (quedaron verificaciones sin correr) no envía aviso,
    porque de todas formas hay que revisar la consola a mano.
    """
    nombres_ejecutados = [nombre for nombre, _ in resultados]
    pendientes = [nombre for nombre in nombres_totales if nombre not in nombres_ejecutados]

    exitosos = sum(1 for _, ok in resultados if ok)
    fallidos = sum(1 for _, ok in resultados if not ok)

    todo_bien = not pendientes and fallidos == 0 and exitosos == len(nombres_totales)
    todo_mal = not pendientes and exitosos == 0 and fallidos == len(nombres_totales)

    if not todo_bien and not todo_mal:
        print("No se envía notificación: el resultado fue mixto o quedaron verificaciones sin correr.")
        return False

    lineas = [f"  [{'completado' if ok else 'terminó con errores'}] {nombre}" for nombre, ok in resultados]
    asunto = (
        "Revisión técnico-administrativa: completada sin errores"
        if todo_bien
        else "Revisión técnico-administrativa: todas las verificaciones fallaron"
    )
    return enviar_notificacion(asunto, "\n".join(lineas))


if __name__ == "__main__":
    asunto = sys.argv[1] if len(sys.argv) > 1 else "Aviso de Automation-Rev-Tec"
    cuerpo = sys.argv[2] if len(sys.argv) > 2 else "Este es un correo de prueba."
    enviar_notificacion(asunto, cuerpo)
