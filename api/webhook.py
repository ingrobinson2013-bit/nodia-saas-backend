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
LAST_WEBHOOK_ERROR = {}

@router.get("/webhook/last-error")
async def get_last_webhook_error():
    """Retorna el último error de entrega reportado por el webhook de Meta."""
    return LAST_WEBHOOK_ERROR or {"status": "no_errors_captured"}

@router.post("/webhook")
async def receive_message(request: Request, background_tasks: BackgroundTasks):
    global LAST_WEBHOOK_ERROR
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
                for st in statuses:
                    st_status = st.get("status")
                    st_id = st.get("id")
                    st_errors = st.get("errors", [])
                    if st_status == "failed":
                        LAST_WEBHOOK_ERROR = {
                            "recipient": st.get("recipient_id"),
                            "msg_id": st_id,
                            "timestamp": st.get("timestamp"),
                            "errors": st_errors,
                            "full_status": st
                        }
                        logger.error(f"🚨 META DELIVERY FAILED para {st.get('recipient_id')} | msg_id={st_id} | Errores={st_errors}")
                    else:
                        logger.info(f"📊 Status update de Meta: {st_id} -> {st_status}")

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

                message_text = None

                if msg_type == "text":
                    message_text = msg.get("text", {}).get("body", "")

                elif msg_type == "button":
                    # Respuesta a botón de plantilla aprobada (ej: "Más Info", "Darme de Baja")
                    btn = msg.get("button", {})
                    btn_text    = btn.get("text", "")
                    btn_payload = btn.get("payload", "")
                    logger.info(f"🔘 Botón de plantilla presionado: text='{btn_text}' payload='{btn_payload}'")
                    message_text = btn_text or btn_payload

                elif msg_type == "interactive":
                    # Botones de respuesta rápida de plantilla (ej: "Más Info", "Darme de Baja")
                    interactive = msg.get("interactive", {})
                    inter_type = interactive.get("type")  # "button_reply" o "list_reply"
                    if inter_type == "button_reply":
                        btn = interactive.get("button_reply", {})
                        btn_id    = btn.get("id", "")
                        btn_title = btn.get("title", "")
                        logger.info(f"🔘 Botón presionado: id='{btn_id}' title='{btn_title}'")
                        # Tratar el título del botón como texto de entrada al agente IA
                        message_text = btn_title
                    elif inter_type == "list_reply":
                        item = interactive.get("list_reply", {})
                        message_text = item.get("title", item.get("id", ""))
                    else:
                        logger.info(f"⏭️  Interactive tipo '{inter_type}' no soportado")
                        continue

                else:
                    logger.info(f"⏭️  Tipo '{msg_type}' no soportado aún")
                    continue

                if not message_text:
                    logger.info("⏭️  Mensaje vacío, ignorado")
                    continue

                sender_wa_id = msg["from"]
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
