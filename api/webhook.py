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
        logger.error("❌ Firma Meta inválida — request rechazado")
        raise HTTPException(status_code=401, detail="Firma Meta inválida")

    body = await request.json()
    logger.info(f"📥 Webhook recibido de Meta: {str(body)[:500]}")

    # Ignorar notificaciones que no son mensajes
    if body.get("object") != "whatsapp_business_account":
        logger.info(f"⏭️  Ignorado — object={body.get('object')}")
        return {"status": "ignored"}

    for entry in body.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})

            # Extraer phone_number_id (identifica al tenant)
            phone_number_id = value.get("metadata", {}).get("phone_number_id")
            messages = value.get("messages", [])

            logger.info(f"📱 phone_number_id={phone_number_id} | mensajes entrantes={len(messages)}")

            statuses = value.get("statuses", [])
            if statuses:
                logger.info(f"📊 Status update de Meta: {statuses}")

            if not messages:
                logger.info("⚠️ No hay mensajes en este change (status update), ignorado")
                continue

            contacts = value.get("contacts", [])
            sender_name = None
            if contacts:
                sender_name = contacts[0].get("profile", {}).get("name")

            for msg in messages:
                msg_type = msg.get("type")
                logger.info(f"📨 Tipo={msg_type} | from={msg.get('from')}")

                if msg_type != "text":
                    logger.info(f"⏭️  Tipo '{msg_type}' no soportado aún")
                    continue

                sender_wa_id = msg["from"]
                message_text = msg["text"]["body"]
                message_id   = msg["id"]

                logger.info(f"✉️  Procesando: '{message_text[:80]}' de {sender_wa_id} (nombre={sender_name})")

                # Procesar en background para responder 200 a Meta de inmediato
                # Meta reintenta si no recibe 200 en < 5 segundos
                background_tasks.add_task(
                    handler.handle,
                    phone_number_id=phone_number_id,
                    sender_wa_id=sender_wa_id,
                    message_text=message_text,
                    message_id=message_id,
                    sender_name=sender_name,
                )

    return {"status": "ok"}


async def _verify_meta_signature(request: Request) -> bool:
    """
    Verifica la firma X-Hub-Signature-256 enviada por Meta.
    Si META_APP_SECRET no está configurado, permite todos los requests (modo dev).
    """
    if not settings.META_APP_SECRET:
        logger.info("🔓 META_APP_SECRET no configurado — verificación de firma desactivada (modo dev)")
        return True

    signature = request.headers.get("X-Hub-Signature-256", "")
    if not signature.startswith("sha256="):
        logger.error(f"❌ Firma inválida — header X-Hub-Signature-256 ausente o malformado: '{signature[:30]}'")
        return False

    body = await request.body()
    expected = hmac.new(
        settings.META_APP_SECRET.strip().encode(),
        body,
        hashlib.sha256,
    ).hexdigest()

    valid = hmac.compare_digest(f"sha256={expected}", signature)
    if not valid:
        logger.error(f"❌ Firma HMAC no coincide. ¿META_APP_SECRET correcto en EasyPanel?")
    return valid
