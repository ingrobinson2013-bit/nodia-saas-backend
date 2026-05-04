# api/meta_connect.py
# Recibe el código de autorización de Meta Embedded Signup
# y lo intercambia por un access_token permanente

import httpx
import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from infrastructure.database import get_supabase
from config import settings

router = APIRouter()
logger = logging.getLogger(__name__)

GRAPH_URL = "https://graph.facebook.com/v19.0"


class MetaConnectRequest(BaseModel):
    tenant_id: str
    code: str           # Código de autorización del Embedded Signup


@router.post("/meta-connect")
async def meta_connect(req: MetaConnectRequest):
    """
    Intercambia el código de Meta Embedded Signup por un access_token
    y obtiene el phone_number_id del número de WhatsApp conectado.
    """
    async with httpx.AsyncClient(timeout=15.0) as client:

        # ── 1. Intercambiar code por access_token ──────────
        token_res = await client.get(f"{GRAPH_URL}/oauth/access_token", params={
            "client_id":     settings.META_APP_ID,
            "client_secret": settings.META_APP_SECRET,
            "code":          req.code,
        })
        token_data = token_res.json()
        if "error" in token_data:
            logger.error(f"Meta token error: {token_data}")
            raise HTTPException(status_code=400, detail=f"Error Meta: {token_data['error']['message']}")

        access_token = token_data["access_token"]

        # ── 2. Obtener WhatsApp Business Accounts (WABA) ───
        waba_res = await client.get(f"{GRAPH_URL}/me/businesses", params={
            "access_token": access_token,
            "fields": "whatsapp_business_accounts{id,name,phone_numbers{id,display_phone_number}}"
        })
        waba_data = waba_res.json()

        # Extraer primer phone_number_id disponible
        phone_number_id = None
        try:
            waba = waba_data["data"][0]
            phones = waba["whatsapp_business_accounts"]["data"][0]["phone_numbers"]["data"]
            phone_number_id = phones[0]["id"]
        except (KeyError, IndexError) as e:
            logger.warning(f"No se pudo extraer phone_number_id: {e}")

        # ── 3. Guardar en tenants ──────────────────────────
        db = get_supabase()
        update_data = {"wa_access_token": access_token}
        if phone_number_id:
            update_data["wa_phone_id"] = phone_number_id

        db.table("tenants").update(update_data).eq("tenant_id", req.tenant_id).execute()

        logger.info(f"Tenant {req.tenant_id} conectó WhatsApp: phone_id={phone_number_id}")
        return {
            "status": "connected",
            "phone_number_id": phone_number_id,
            "message": "WhatsApp conectado exitosamente ✅"
        }
