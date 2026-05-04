# domain/whatsapp_service.py
# Envío de mensajes via Meta WhatsApp Cloud API

import httpx
import logging

logger = logging.getLogger(__name__)

GRAPH_API_URL = "https://graph.facebook.com/v19.0"


class WhatsAppService:
    """
    Servicio para enviar mensajes via Meta WhatsApp Cloud API.
    Usa el wa_access_token y wa_phone_id del tenant (BYOK).
    """

    def __init__(self, phone_number_id: str, access_token: str):
        self.phone_number_id = phone_number_id
        self.access_token = access_token
        self.base_url = f"{GRAPH_API_URL}/{phone_number_id}/messages"

    async def send_text(self, to: str, message: str) -> dict:
        """Envía un mensaje de texto simple."""
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "text",
            "text": {"body": message, "preview_url": False},
        }
        return await self._post(payload)

    async def send_template(
        self, to: str, template_name: str, lang: str = "es_CO", components: list = None
    ) -> dict:
        """Envía un template aprobado por Meta."""
        payload = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "template",
            "template": {
                "name": template_name,
                "language": {"code": lang},
                "components": components or [],
            },
        }
        return await self._post(payload)

    async def mark_as_read(self, message_id: str) -> None:
        """Marca un mensaje como leído (doble palomita azul)."""
        payload = {
            "messaging_product": "whatsapp",
            "status": "read",
            "message_id": message_id,
        }
        await self._post(payload)

    async def _post(self, payload: dict) -> dict:
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(self.base_url, json=payload, headers=headers)
            if response.status_code != 200:
                logger.error(
                    f"WhatsApp API error {response.status_code}: {response.text}"
                )
            response.raise_for_status()
            return response.json()
