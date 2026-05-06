# domain/notification_job.py
# Job periodico: detecta citas nuevas en Odoo y envia WhatsApp de confirmacion
#
# Arquitectura Pull (backend consulta Odoo):
#   - Cada 5 minutos, consulta calendar.event creados recientemente en cada tenant
#   - Si el evento tiene telefono y no ha sido notificado → envia WhatsApp
#   - Registra en Supabase notificaciones_wa para no duplicar envios
#
# VENTAJA vs Odoo Server Action:
#   - Sin restricciones de import
#   - Multi-tenant automatico
#   - Retry logic integrado
#   - No requiere configuracion en Odoo

import logging
import asyncio
from datetime import datetime, timezone
from infrastructure.database import get_supabase
from infrastructure.repositories.tenant_repo import TenantRepository
from domain.odoo_service import OdooService
from domain.whatsapp_service import WhatsAppService

logger = logging.getLogger(__name__)
tenant_repo = TenantRepository()

POLL_INTERVAL_SECONDS = 30    # polling cada 30 segundos → notificacion casi instantanea
LOOKBACK_MINUTES      = 2     # buscar eventos creados en los ultimos 2 min (margen vs 30s)


async def run_notification_job():
    """
    Job principal que corre en background.
    Se inicia una vez al arrancar el servidor (via asyncio.create_task en main.py).
    """
    logger.info("NotificationJob: iniciado (intervalo=%ds)", POLL_INTERVAL_SECONDS)

    while True:
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
        try:
            await _process_all_tenants()
        except Exception as e:
            logger.error(f"NotificationJob: error en ciclo principal: {e}")


async def _process_all_tenants():
    """Recorre todos los tenants activos con Odoo configurado."""
    try:
        tenants = tenant_repo.get_all_active_with_odoo()
    except Exception as e:
        logger.warning(f"NotificationJob: no se pudo listar tenants: {e}")
        return

    logger.info(f"NotificationJob: procesando {len(tenants)} tenants con Odoo")

    for tenant in tenants:
        try:
            await _process_tenant(tenant)
        except Exception as e:
            logger.error(f"NotificationJob [{tenant.get('nombre')}]: {e}")


async def _process_tenant(tenant: dict):
    """Consulta Odoo de un tenant y envia WhatsApp para eventos nuevos no notificados."""
    tenant_id = tenant["tenant_id"]
    nombre    = tenant.get("nombre", tenant_id)

    odoo_config = {
        "url":     tenant.get("odoo_url"),
        "db":      tenant.get("odoo_db"),
        "user":    tenant.get("odoo_user"),
        "api_key": tenant.get("odoo_api_key"),
    }

    # Conectar a Odoo
    try:
        odoo = OdooService(**odoo_config)
        if not odoo.uid:
            logger.warning(f"NotificationJob [{nombre}]: Odoo auth fallida, skip")
            return
    except Exception as e:
        logger.warning(f"NotificationJob [{nombre}]: Odoo no disponible: {e}")
        return

    # Obtener eventos recientes
    recent = odoo.get_recent_events(since_minutes=LOOKBACK_MINUTES)
    if not recent:
        return

    db = get_supabase()

    # Filtrar los que ya fueron notificados (via tabla notificaciones_wa o descripcion)
    for ev in recent:
        event_id = ev.get("id")
        phone    = ev.get("phone", "")
        nombre_cliente = ev.get("partner_name", "Cliente")
        fecha_str      = ev.get("start_bogota", "")
        servicio       = ev.get("name", "Cita")

        # Evitar re-enviar (los creados por el bot ya tienen "AgenteIA VALE" en descripcion)
        descripcion = ev.get("description", "") or ""
        if "AgenteIA VALE" in descripcion:
            logger.debug(f"NotificationJob [{nombre}]: event {event_id} ya fue del bot, skip")
            continue

        if not phone or len(phone) < 10:
            logger.debug(f"NotificationJob [{nombre}]: event {event_id} sin telefono, skip")
            continue

        # Normalizar telefono
        if not phone.startswith("57"):
            phone = f"57{phone[-10:]}"

        # Verificar si ya notificamos este evento en Supabase
        try:
            ya_notificado = (
                db.table("notificaciones_wa")
                .select("id")
                .eq("tenant_id", tenant_id)
                .eq("odoo_event_id", str(event_id))
                .execute()
            )
            if ya_notificado.data:
                continue
        except Exception:
            pass  # Si la tabla no existe aun, continuamos

        # Enviar WhatsApp — primero intenta con template, fallback a texto libre
        try:
            wa = WhatsAppService(
                phone_number_id=tenant["wa_phone_id"],
                access_token=tenant["wa_access_token"],
            )

            # Intento 1: Template aprobado por Meta (funciona con clientes nuevos)
            enviado = False
            try:
                components = [
                    {
                        "type": "body",
                        "parameters": [
                            {"type": "text", "text": nombre_cliente},
                            {"type": "text", "text": fecha_str},
                            {"type": "text", "text": servicio},
                            {"type": "text", "text": nombre},
                        ]
                    }
                ]
                await wa.send_template(
                    to=phone,
                    template_name="cita_confirmada",
                    lang="es",
                    components=components,
                )
                logger.info(f"NotificationJob [{nombre}]: Template WA enviado a {phone} evento {event_id}")
                enviado = True
            except Exception as te:
                logger.warning(f"NotificationJob [{nombre}]: Template falló ({te}), intentando texto libre")

            # Intento 2: Texto libre (ventana 24h abierta)
            if not enviado:
                mensaje = (
                    f"Hola {nombre_cliente}! Tu cita ha sido confirmada. \n\n"
                    f"*{servicio}*\n"
                    f"*Fecha:* {fecha_str}\n"
                    f"*Lugar:* {nombre}\n\n"
                    f"Si necesitas reagendar o cancelar, respondenos aqui mismo.\n"
                    f"Te esperamos! ✂️"
                )
                await wa.send_text(to=phone, message=mensaje)
                logger.info(f"NotificationJob [{nombre}]: Texto libre WA enviado a {phone} evento {event_id}")

            # Registrar envio en Supabase
            try:
                db.table("notificaciones_wa").insert({
                    "tenant_id":      tenant_id,
                    "odoo_event_id":  str(event_id),
                    "phone":          phone,
                    "enviado_at":     datetime.now(timezone.utc).isoformat(),
                    "estado":         "enviado",
                }).execute()
            except Exception as e:
                logger.warning(f"NotificationJob: no se pudo registrar en notificaciones_wa: {e}")

        except Exception as e:
            logger.error(f"NotificationJob [{nombre}]: error enviando WA a {phone}: {e}")
