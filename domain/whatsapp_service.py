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
        self.phone_number_id = phone_number_id.strip() if phone_number_id else ""
        self.access_token = access_token.strip() if access_token else ""
        self.base_url = f"{GRAPH_API_URL}/{self.phone_number_id}/messages"

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
        if not message_id or message_id.startswith("test_"):
            return
        payload = {
            "messaging_product": "whatsapp",
            "status": "read",
            "message_id": message_id,
        }
        try:
            await self._post(payload)
        except Exception as e:
            logger.warning(f"No se pudo marcar mensaje {message_id} como leído: {e}")

    async def download_media(self, media_id: str) -> tuple[bytes, str]:
        """
        Descarga un archivo multimedia (ej. nota de voz) desde Meta Graph API.
        Retorna una tupla (bytes_del_archivo, mime_type).
        """
        if not media_id:
            raise ValueError("media_id no proporcionado")

        headers = {"Authorization": f"Bearer {self.access_token}"}
        
        async with httpx.AsyncClient(timeout=15.0) as client:
            # 1. Obtener URL temporal de descarga
            media_info_url = f"{GRAPH_API_URL}/{media_id}"
            res_info = await client.get(media_info_url, headers=headers)
            if res_info.status_code != 200:
                logger.error(f"Error consultando media_id {media_id} en Meta: {res_info.text}")
                res_info.raise_for_status()

            info_data = res_info.json()
            download_url = info_data.get("url")
            mime_type = info_data.get("mime_type", "audio/ogg")

            if not download_url:
                raise ValueError(f"No se encontró URL de descarga para media_id {media_id}")

            # 2. Descargar los bytes reales del archivo de audio
            res_file = await client.get(download_url, headers=headers)
            if res_file.status_code != 200:
                logger.error(f"Error descargando archivo de audio desde {download_url}: {res_file.status_code}")
                res_file.raise_for_status()

            logger.info(f"✅ Audio descargado exitosamente: media_id={media_id} ({len(res_file.content)} bytes, mime={mime_type})")
            return res_file.content, mime_type


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
