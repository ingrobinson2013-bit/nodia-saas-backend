# infrastructure/email_service.py
# Servicio de infraestructura para envío de correos vía SMTP de manera asíncrona

import smtplib
import logging
import asyncio
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional
from config import settings

logger = logging.getLogger(__name__)


class EmailService:
    def __init__(self):
        self.host = settings.SMTP_HOST
        self.port = settings.SMTP_PORT
        self.user = settings.SMTP_USER
        self.password = settings.SMTP_PASSWORD
        self.sender = settings.SMTP_SENDER or settings.SMTP_USER
        self.notify_email = settings.NOTIFY_EMAIL

    def _send_sync(self, subject: str, html_content: str, recipient: str) -> bool:
        """
        Envío síncrono del correo usando smtplib. Ejecutado en hilo de fondo.
        """
        if not self.user or not self.password:
            logger.warning("Configuración SMTP incompleta (SMTP_USER/SMTP_PASSWORD no definidos). Correo no enviado.")
            return False
        
        if not recipient:
            logger.warning("Destinatario de correo vacío. Correo no enviado.")
            return False

        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = self.sender
            msg["To"] = recipient

            part = MIMEText(html_content, "html", "utf-8")
            msg.attach(part)

            logger.info(f"Conectando a servidor SMTP {self.host}:{self.port}...")
            
            # Detectar puerto SSL (465) vs STARTTLS (587 o 25)
            if int(self.port) == 465:
                server = smtplib.SMTP_SSL(self.host, int(self.port), timeout=10)
            else:
                server = smtplib.SMTP(self.host, int(self.port), timeout=10)
                server.starttls()
            
            server.login(self.user, self.password)
            server.sendmail(self.sender, [recipient], msg.as_string())
            server.quit()
            logger.info(f"Correo enviado exitosamente a {recipient}: {subject}")
            return True
        except Exception as e:
            logger.error(f"Error yendo correo SMTP a {recipient}: {e}", exc_info=True)
            return False

    async def send_html_email(self, subject: str, html_content: str, recipient: Optional[str] = None) -> bool:
        """
        Envío asíncrono de correo electrónico (no bloquea el loop de eventos).
        """
        to_email = recipient or self.notify_email
        if not to_email:
            logger.warning("No hay destinatario definido para notificaciones de correo.")
            return False

        try:
            # Usar asyncio.to_thread para correr en pool de hilos y evitar bloquear
            return await asyncio.to_thread(self._send_sync, subject, html_content, to_email)
        except AttributeError:
            # Fallback en Python < 3.9
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(None, self._send_sync, subject, html_content, to_email)
        except Exception as e:
            logger.error(f"Error al programar envío de correo asíncrono: {e}")
            return False
