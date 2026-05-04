# api/odoo_webhook.py
# Endpoint para recibir notificaciones desde Odoo y enviar Templates por WhatsApp

import logging
from fastapi import APIRouter, Request, HTTPException
from infrastructure.repositories.tenant_repo import TenantRepository
from domain.whatsapp_service import WhatsAppService

router = APIRouter()
logger = logging.getLogger(__name__)
tenant_repo = TenantRepository()

@router.post("/odoo-webhook/{tenant_id}")
async def handle_odoo_webhook(tenant_id: str, request: Request):
    """
    Recibe un webhook desde una Acción Automatizada de Odoo.
    Odoo debe enviar un JSON con:
    {
        "phone": "573235813942",
        "name": "Juan Perez",
        "date": "2024-05-10 10:00",
        "template_name": "cita_confirmada"
    }
    """
    tenant = tenant_repo.get_by_id(tenant_id)
    if not tenant or not tenant.get("activo"):
        raise HTTPException(status_code=404, detail="Tenant inactivo o no encontrado")

    try:
        data = await request.json()
        phone = data.get("phone")
        template_name = data.get("template_name", "cita_confirmada")
        
        if not phone:
            return {"error": "No se envió número de teléfono"}

        # Asegurar formato de teléfono
        if not phone.startswith("57"):
            phone = f"57{phone[-10:]}"

        # Parámetros para la plantilla de Meta (Ej: {{1}} = Nombre, {{2}} = Fecha)
        components = [
            {
                "type": "body",
                "parameters": [
                    {"type": "text", "text": data.get("name", "Cliente")},
                    {"type": "text", "text": data.get("date", "tu cita")}
                ]
            }
        ]

        wa = WhatsAppService(
            phone_number_id=tenant["wa_phone_id"],
            access_token=tenant["wa_access_token"],
        )
        
        await wa.send_template(
            to=phone,
            template_name=template_name,
            lang="es_CO",
            components=components
        )
        
        logger.info(f"[{tenant['nombre']}] Template '{template_name}' enviado a {phone} desde webhook de Odoo")
        return {"status": "success", "message": "Template enviado"}
        
    except Exception as e:
        logger.error(f"Error procesando webhook de Odoo: {e}")
        raise HTTPException(status_code=500, detail=str(e))
