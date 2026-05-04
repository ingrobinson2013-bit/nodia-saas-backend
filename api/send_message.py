# api/send_message.py
# Endpoint para que el agente humano envíe mensajes via Meta desde el panel

import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from infrastructure.repositories.tenant_repo import TenantRepository
from domain.whatsapp_service import WhatsAppService
from infrastructure.database import get_supabase
from datetime import datetime, timezone

router = APIRouter()
logger = logging.getLogger(__name__)
tenant_repo = TenantRepository()


class SendMessageRequest(BaseModel):
    tenant_id: str
    wa_to: str        # Número destino del cliente
    message: str      # Texto del agente
    session_id: str   # ID de la chat_session


@router.post("/send-message")
async def send_message(req: SendMessageRequest):
    """
    Envía un mensaje desde el agente humano al cliente via WhatsApp.
    También actualiza el historial en chat_sessions.
    """
    # 1. Obtener tenant
    tenant = tenant_repo.get_by_id(req.tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant no encontrado")

    if not tenant.get("activo"):
        raise HTTPException(status_code=403, detail="Tenant inactivo")

    # 2. Enviar por WhatsApp
    wa = WhatsAppService(
        phone_number_id=tenant["wa_phone_id"],
        access_token=tenant["wa_access_token"],
    )
    try:
        await wa.send_text(to=req.wa_to, message=req.message)
    except Exception as e:
        logger.error(f"Error enviando mensaje WhatsApp: {e}")
        raise HTTPException(status_code=500, detail=f"Error Meta API: {str(e)}")

    # 3. Guardar en historial de la sesión
    db = get_supabase()
    session = db.table("chat_sessions").select("history").eq("id", req.session_id).single().execute()
    if session.data:
        history = session.data.get("history") or []
        history.append({
            "role": "agent",
            "content": req.message,
            "timestamp": datetime.now(timezone.utc).isoformat()
        })
        db.table("chat_sessions").update({
            "history": history,
            "updated_at": datetime.now(timezone.utc).isoformat()
        }).eq("id", req.session_id).execute()

    logger.info(f"Agente envió mensaje a {req.wa_to}: {req.message[:50]}")
    return {"status": "sent", "to": req.wa_to}
