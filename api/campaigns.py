# api/campaigns.py
# Módulo de Campañas de Remarketing Masivo (Scraping & Lead Outbound)
# Permite cargar listas de números extraídos por scraping, sanitizarlos, y enviar mensajes masivos vía WhatsApp Cloud API.

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import List, Optional, Union
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from infrastructure.repositories.tenant_repo import TenantRepository
from domain.whatsapp_service import WhatsAppService
from infrastructure.database import get_supabase

router = APIRouter()
logger = logging.getLogger(__name__)
tenant_repo = TenantRepository()


class ContactInput(BaseModel):
    phone: str
    name: Optional[str] = "Cliente"


class SendCampaignRequest(BaseModel):
    tenant_id: str
    campaign_name: str = "Campaña Remarketing Scraping"
    message_type: str = "text"  # "text" | "template"
    message: str = ""           # Texto del mensaje (admite {nombre})
    template_name: Optional[str] = None
    template_language: str = "es"
    contacts: List[Union[str, ContactInput]]
    delay_seconds: float = Field(default=1.0, ge=0.2, le=5.0)  # Pausa entre envíos anti-spam


def normalize_colombia_phone(raw_phone: str) -> Optional[str]:
    """
    Sanitiza y normaliza un número telefónico.
    Formatos soportados:
    - 3001234567 -> 573001234567
    - +57 300 123 4567 -> 573001234567
    - 573001234567 -> 573001234567
    """
    if not raw_phone:
        return None
    # Eliminar cualquier carácter que no sea número
    clean = re.sub(r"\D", "", str(raw_phone))
    
    if not clean:
        return None
        
    # Si es número de Colombia de 10 dígitos (empieza por 3)
    if len(clean) == 10 and clean.startswith("3"):
        return f"57{clean}"
    
    # Si ya trae código 57 de 12 dígitos
    if len(clean) == 12 and clean.startswith("573"):
        return clean
        
    # Si es internacional o tiene más de 8 dígitos, retornarlo tal cual
    if len(clean) >= 9:
        return clean
        
    return None


@router.post("/campaigns/send")
async def send_campaign(req: SendCampaignRequest):
    """
    Ejecuta el envío masivo de una campaña de remarketing a una lista de números (e.g. Scraping).
    Garantiza anti-spam con delays y crea la sesión en inbox automáticamente.
    """
    # 1. Obtener tenant
    tenant = tenant_repo.get_by_id(req.tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant no encontrado")

    if not tenant.get("activo"):
        raise HTTPException(status_code=403, detail="Tenant inactivo")

    phone_id = tenant.get("wa_phone_id")
    access_token = tenant.get("wa_access_token")

    if not phone_id or not access_token:
        raise HTTPException(
            status_code=400,
            detail="Credenciales de WhatsApp (wa_phone_id / wa_access_token) no configuradas para este negocio."
        )

    wa = WhatsAppService(phone_number_id=phone_id, access_token=access_token)
    db = get_supabase()

    total_contacts = len(req.contacts)
    sent_count = 0
    failed_count = 0
    details = []

    logger.info(f"[{tenant['nombre']}] Iniciando campaña '{req.campaign_name}' con {total_contacts} contactos.")

    # 2. Procesar cada contacto
    for index, item in enumerate(req.contacts):
        # Parsear input (string o ContactInput)
        if isinstance(item, dict):
            raw_phone = item.get("phone", "")
            contact_name = item.get("name", "") or "Cliente"
        elif hasattr(item, "phone"):
            raw_phone = item.phone
            contact_name = item.name or "Cliente"
        else:
            raw_phone = str(item)
            contact_name = "Cliente"

        clean_phone = normalize_colombia_phone(raw_phone)
        if not clean_phone:
            failed_count += 1
            details.append({
                "raw": raw_phone,
                "status": "failed",
                "reason": "Número inválido o mal formateado"
            })
            continue

        # Personalizar texto
        personalized_text = req.message.replace("{nombre}", contact_name).replace("{negocio}", tenant.get("nombre", ""))

        try:
            # Enviar por WhatsApp
            if req.message_type == "template" and req.template_name:
                # Sanitizar nombre de plantilla (eliminar " (APPROVED) - es_CO" si está presente)
                clean_tpl_name = req.template_name.split("(")[0].split()[0].strip()
                components = []

                # Si es la plantilla oficial de BeautySync Pro (contacto_inicial_beautysyncpro)
                if "contacto_inicial" in clean_tpl_name or clean_tpl_name == "contacto_inicial_beautysyncpro":
                    clean_tpl_name = "contacto_inicial_beautysyncpro"
                    components = [
                        {
                            "type": "header",
                            "parameters": [{
                                "type": "image",
                                "image": {
                                    "link": "https://blog.tesoconsulting.co/wp-content/uploads/2026/05/BeautySync_History_Meta.webp"
                                }
                            }]
                        },
                        {
                            "type": "body",
                            "parameters": [{
                                "type": "text",
                                "parameter_name": "nombre",
                                "text": contact_name
                            }]
                        }
                    ]
                else:
                    # Generic component format for other templates
                    components = [
                        {
                            "type": "body",
                            "parameters": [{
                                "type": "text",
                                "text": contact_name
                            }]
                        }
                    ]

                # Garantizar código de idioma es_CO para contacto_inicial_beautysyncpro
                lang_code = "es_CO"
                if req.template_language and req.template_language != "es":
                    lang_code = req.template_language
                if "contacto_inicial" in clean_tpl_name or clean_tpl_name == "contacto_inicial_beautysyncpro":
                    clean_tpl_name = "contacto_inicial_beautysyncpro"
                    lang_code = "es_CO"

                wa_response = await wa.send_template(
                    to=clean_phone,
                    template_name=clean_tpl_name,
                    lang=lang_code,
                    components=components
                )
            else:
                wa_response = await wa.send_text(to=clean_phone, message=personalized_text)

            sent_count += 1
            details.append({
                "phone": clean_phone,
                "name": contact_name,
                "status": "sent",
                "wa_response": wa_response
            })

            # 3. Crear o actualizar sesión en chat_sessions para que aparezca en el Inbox
            try:
                now_iso = datetime.now(timezone.utc).isoformat()
                
                # Buscar sesión existente (usando columna wa_from)
                session_query = db.table("chat_sessions") \
                    .select("id, history") \
                    .eq("tenant_id", req.tenant_id) \
                    .eq("wa_from", clean_phone) \
                    .execute()

                initial_history = [{
                    "role": "agent",
                    "content": f"📢 [CAMPAÑA REMARKETING: {req.campaign_name}]\n{personalized_text}",
                    "timestamp": now_iso
                }]

                if session_query.data:
                    existing_session = session_query.data[0]
                    h = existing_session.get("history") or []
                    h.extend(initial_history)
                    db.table("chat_sessions").update({
                        "history": h,
                        "estado": "agente_ia",
                        "updated_at": now_iso
                    }).eq("id", existing_session["id"]).execute()
                else:
                    db.table("chat_sessions").insert({
                        "tenant_id": req.tenant_id,
                        "wa_from": clean_phone,
                        "name": contact_name,
                        "estado": "agente_ia",
                        "history": initial_history,
                        "updated_at": now_iso
                    }).execute()
            except Exception as sess_err:
                logger.warning(f"No se pudo guardar la sesión en chat_sessions (omitido): {sess_err}")

        except Exception as e:
            failed_count += 1
            logger.error(f"Error enviando mensaje a {clean_phone}: {e}")
            details.append({
                "phone": clean_phone,
                "name": contact_name,
                "status": "failed",
                "reason": str(e)
            })

        # Pausa anti-spam entre mensajes
        if index < total_contacts - 1 and req.delay_seconds > 0:
            await asyncio.sleep(req.delay_seconds)

    # 4. Guardar registro de campaña en Supabase (si existe la tabla)
    try:
        db.table("campaigns").insert({
            "tenant_id": req.tenant_id,
            "name": req.campaign_name,
            "total_contacts": total_contacts,
            "sent_count": sent_count,
            "failed_count": failed_count,
            "message_type": req.message_type,
            "message": req.message,
            "created_at": datetime.now(timezone.utc).isoformat()
        }).execute()
    except Exception as db_err:
        logger.warning(f"No se pudo guardar registro de campaña en DB (omitido): {db_err}")

    return {
        "status": "success",
        "campaign_name": req.campaign_name,
        "total": total_contacts,
        "sent": sent_count,
        "failed": failed_count,
        "details": details
    }


@router.get("/campaigns/list/{tenant_id}")
async def list_campaigns(tenant_id: str):
    """
    Retorna el historial de campañas enviadas por el tenant.
    """
    db = get_supabase()
    try:
        res = db.table("campaigns").select("*").eq("tenant_id", tenant_id).order("created_at", desc=True).execute()
        return {"campaigns": res.data or []}
    except Exception as e:
        logger.warning(f"No se pudo consultar historial de campañas: {e}")
        return {"campaigns": []}


class CheckContactsRequest(BaseModel):
    tenant_id: str
    phones: List[str]


@router.post("/campaigns/check-contacts")
async def check_contacts(req: CheckContactsRequest):
    """
    Verifica qué contactos de la lista ya han recibido mensajes de remarketing en el pasado.
    Retorna el estado 'already_sent' y la fecha de la última interacción para evitar duplicados.
    """
    if not req.phones:
        return {"results": {}}

    db = get_supabase()

    # Normalizar teléfonos
    clean_phones = []
    for p in req.phones:
        norm = normalize_colombia_phone(p)
        if norm:
            clean_phones.append(norm)

    if not clean_phones:
        return {"results": {}}

    try:
        # Consultar sesiones existentes en chat_sessions
        res = db.table("chat_sessions") \
            .select("wa_from, updated_at, history, name") \
            .eq("tenant_id", req.tenant_id) \
            .in_("wa_from", clean_phones) \
            .execute()

        results = {}
        for session in (res.data or []):
            wa = session.get("wa_from")
            history = session.get("history") or []
            
            # Buscar si en el historial hay un mensaje de campaña
            has_campaign = any(
                "CAMPAÑA" in str(msg.get("content", "")).upper() or
                "REMARKETING" in str(msg.get("content", "")).upper()
                for msg in history if isinstance(msg, dict)
            )

            results[wa] = {
                "already_sent": True,
                "has_campaign_message": has_campaign,
                "last_interaction": session.get("updated_at"),
                "name": session.get("name") or "Cliente"
            }

        return {"results": results}

    except Exception as e:
        logger.error(f"Error verificando histórico de contactos: {e}")
        return {"results": {}}
