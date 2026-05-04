# api/webhook.py
# Endpoint para recibir mensajes de Meta WhatsApp Cloud API

import hashlib
import hmac
import logging
from fastapi import APIRouter, Request, Response, HTTPException, BackgroundTasks
from config import settings
from domain.message_handler import MessageHandler

router = APIRouter()
logger = logging.getLogger(__name__)
handler = MessageHandler()


# ──────────────────────────────────────────────────────────
# GET /webhook — Verificación del webhook con Meta
# Meta envía este request cuando configuras el webhook en
# el panel de Meta for Developers
# ──────────────────────────────────────────────────────────
@router.get("/webhook")
async def verify_webhook(request: Request):
    params = dict(request.query_params)
    mode      = params.get("hub.mode")
    token     = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")

    if mode == "subscribe" and token == settings.META_VERIFY_TOKEN:
        logger.info("✅ Webhook de Meta verificado correctamente")
        return Response(content=challenge, media_type="text/plain")

    logger.warning("❌ Verificación de webhook fallida")
    raise HTTPException(status_code=403, detail="Token de verificación inválido")


# ──────────────────────────────────────────────────────────
# POST /webhook — Recibe mensajes entrantes de WhatsApp
# ──────────────────────────────────────────────────────────
@router.post("/webhook")
async def receive_message(request: Request, background_tasks: BackgroundTasks):
    # Verificar firma HMAC-SHA256 de Meta (seguridad)
    if not await _verify_meta_signature(request):
        raise HTTPException(status_code=401, detail="Firma Meta inválida")

    body = await request.json()

    # Ignorar notificaciones que no son mensajes
    if body.get("object") != "whatsapp_business_account":
        return {"status": "ignored"}

    for entry in body.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})

            # Extraer phone_number_id (identifica al tenant)
            phone_number_id = value.get("metadata", {}).get("phone_number_id")
            messages = value.get("messages", [])

            for msg in messages:
                if msg.get("type") != "text":
                    # Por ahora solo procesamos texto
                    # TODO: manejar audio, imagen, documento
                    logger.info(f"Tipo de mensaje no soportado: {msg.get('type')}")
                    continue

                sender_wa_id = msg["from"]
                message_text = msg["text"]["body"]
                message_id   = msg["id"]

                # Procesar en background para responder 200 a Meta de inmediato
                # Meta reintenta si no recibe 200 en < 5 segundos
                background_tasks.add_task(
                    handler.handle,
                    phone_number_id=phone_number_id,
                    sender_wa_id=sender_wa_id,
                    message_text=message_text,
                    message_id=message_id,
                )

    return {"status": "ok"}


async def _verify_meta_signature(request: Request) -> bool:
    """
    Verifica la firma X-Hub-Signature-256 enviada por Meta.
    Garantiza que el request realmente viene de Meta.
    """
    if not settings.META_APP_SECRET:
        # En desarrollo sin secret configurado, permitir
        return True

    signature = request.headers.get("X-Hub-Signature-256", "")
    if not signature.startswith("sha256="):
        return False

    body = await request.body()
    expected = hmac.new(
        settings.META_APP_SECRET.encode(),
        body,
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(f"sha256={expected}", signature)
