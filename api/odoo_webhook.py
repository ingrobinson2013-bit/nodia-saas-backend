# api/odoo_webhook.py
# Endpoint para recibir notificaciones desde Odoo y enviar WhatsApp al cliente
#
# Odoo llama este endpoint via Accion del Servidor cuando crea una cita manual.
# Soporta dos modos:
#   1. Texto libre (default) → funciona sin template aprobado de Meta
#   2. Template Meta          → si se envia "template_name" en el payload

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
    Recibe un webhook desde una Accion del Servidor de Odoo.

    Payload esperado:
    {
        "phone":    "573235813942",   <- numero con o sin +57
        "name":     "Juan Perez",     <- nombre del cliente
        "date":     "06/05/2026 a las 04:00 PM",
        "servicio": "Corte clasico",
        "template_name": "cita_confirmada"  <- OPCIONAL: si no se envia, usa texto libre
    }

    Si NO se envia template_name, envia un mensaje de texto libre bonito
    (no requiere template aprobado por Meta - funciona de inmediato).
    """
    tenant = tenant_repo.get_by_id(tenant_id)
    if not tenant or not tenant.get("activo"):
        raise HTTPException(status_code=404, detail="Tenant inactivo o no encontrado")

    try:
        data = await request.json()
        phone = data.get("phone", "").replace(" ", "").replace("+", "")

        if not phone:
            return {"error": "No se envio numero de telefono"}

        # Normalizar a formato colombiano (57XXXXXXXXXX)
        if not phone.startswith("57"):
            phone = f"57{phone[-10:]}"

        nombre   = data.get("name", "Cliente")
        fecha    = data.get("date", "fecha no especificada")
        servicio = data.get("servicio", "tu cita")
        negocio  = tenant.get("nombre", "el negocio")
        template_name = data.get("template_name")

        wa = WhatsAppService(
            phone_number_id=tenant["wa_phone_id"],
            access_token=tenant["wa_access_token"],
        )

        if template_name:
            # -- Modo Template Meta (requiere template aprobado) ---------------
            components = [
                {
                    "type": "body",
                    "parameters": [
                        {"type": "text", "text": nombre},
                        {"type": "text", "text": fecha},
                    ]
                }
            ]
            await wa.send_template(
                to=phone,
                template_name=template_name,
                lang="es_CO",
                components=components,
            )
            logger.info(f"[{negocio}] Template '{template_name}' enviado a {phone}")

        else:
            # -- Modo Texto Libre (funciona sin aprobacion de Meta) --------
            mensaje = (
                f"Hola {nombre}! Tu cita ha sido confirmada. \n\n"
                f"*{servicio}*\n"
                f"*Fecha:* {fecha}\n"
                f"*Lugar:* {negocio}\n\n"
                f"Si necesitas reagendar o cancelar, respondenos aqui mismo.\n"
                f"Te esperamos! ✂️"
            )
            await wa.send_text(to=phone, message=mensaje)
            logger.info(f"[{negocio}] Confirmacion texto libre enviada a {phone}")

        return {"status": "success", "phone": phone, "negocio": negocio}

    except Exception as e:
        logger.error(f"Error procesando webhook de Odoo: {e}")
        raise HTTPException(status_code=500, detail=str(e))
