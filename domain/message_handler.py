# domain/message_handler.py
# Orquestador principal — alineado con schema real de chat_sessions
#
# Schema chat_sessions:
#   id, tenant_id, wa_from, history (JSONB []), estado,
#   cita_odoo_id, updated_at, bot_mode, name
#
# ARQUITECTURA DE BOOKING:
#   GPT llama la tool create_appointment (via ai_service) cuando el cliente confirma.
#   ai_service ejecuta la cita en Odoo y retorna booking_data.
#   message_handler persiste booking_data en citas_log y actualiza chat_sessions.

import logging
import json
import re
import asyncio
from datetime import datetime, timezone
from infrastructure.repositories.tenant_repo import TenantRepository
from domain.ai_service import AIService
from domain.whatsapp_service import WhatsAppService
from domain.prompt_builder import build_system_prompt
from infrastructure.database import get_supabase

logger = logging.getLogger(__name__)
tenant_repo = TenantRepository()

MAX_HISTORY = 10  # Turnos maximos en contexto para OpenAI


class MessageHandler:
    """
    Flujo completo de un mensaje entrante:
    1. Identificar tenant por wa_phone_id
    2. Recuperar/crear sesion en chat_sessions (upsert)
    3. Verificar bot_mode (si esta pausado, solo notifica)
    4. Llamar a IA con historial JSONB
    5. Si GPT llamo create_appointment tool -> persistir en Odoo + citas_log
    6. Si GPT emitio otro JSON CRM (ESCALATE/CANCEL/RESCHEDULE) -> procesar
    7. Responder al usuario via Meta API
    8. Actualizar historial en Supabase (JSONB append)
    """

    async def handle(
        self,
        phone_number_id: str,
        sender_wa_id: str,
        message_text: str,
        message_id: str,
        sender_name: str = None,
    ):
        # -- 1. Identificar tenant -------------------------------------------
        tenant = tenant_repo.get_by_phone_id(phone_number_id)
        if not tenant:
            logger.warning(f"Tenant no encontrado para phone_id: {phone_number_id}")
            return

        if not tenant.get("activo", False):
            logger.info(f"Tenant {tenant['nombre']} inactivo. Ignorado.")
            return

        tenant_id = tenant["tenant_id"]
        logger.info(f"[{tenant['nombre']}] Msg de {sender_wa_id}: {message_text[:60]}...")

        db = get_supabase()

        # -- 2. Recuperar o crear sesion -------------------------------------
        session = self._get_or_create_session(db, tenant_id, sender_wa_id, sender_name)

        # -- 3. Guardar el mensaje del usuario en el historial ---------------
        history = session.get("history") or []
        user_message_entry = {
            "role": "user",
            "content": message_text,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }

        # -- 4. Verificar bot_mode -------------------------------------------
        if not session.get("bot_mode", True):
            logger.info(f"[{tenant['nombre']}] bot_mode=False para {sender_wa_id}. IA pausada.")
            self._update_session(db, session["id"], history + [user_message_entry])
            return

        # -- 5. Preparar historial para OpenAI --------------------------------
        history_context = history[-MAX_HISTORY:]

        # -- 6. Cargar tenant_config ------------------------------------------
        tenant_config = None
        try:
            tc_result = db.table("tenant_config").select("*").eq("tenant_id", tenant_id).single().execute()
            tenant_config = tc_result.data
        except Exception:
            pass

        # -- 7. Configurar Odoo si el tenant tiene credenciales ---------------
        odoo_config = None
        if tenant.get("odoo_url") and tenant.get("odoo_url").strip():
            odoo_config = {
                "url":     tenant.get("odoo_url"),
                "db":      tenant.get("odoo_db"),
                "user":    tenant.get("odoo_user"),
                "api_key": tenant.get("odoo_api_key"),
            }

        # -- 8. Cargar citas desde Supabase citas_log -------------------------
        from datetime import date as date_cls
        hoy_iso = date_cls.today().strftime("%Y-%m-%d")
        citas_cliente = []
        citas_negocio = []

        try:
            r_cliente = (
                db.table("citas_log")
                .select("fecha_cita, hora_cita, servicio")
                .eq("tenant_id", tenant_id)
                .eq("wa_from", sender_wa_id)
                .neq("estado", "cancelada")
                .gte("fecha_cita", hoy_iso)
                .order("fecha_cita", desc=False)
                .limit(5)
                .execute()
            )
            citas_cliente = r_cliente.data or []
        except Exception as e:
            logger.warning(f"No se pudieron cargar citas del cliente: {e}")

        try:
            r_negocio = (
                db.table("citas_log")
                .select("fecha_cita, hora_cita")
                .eq("tenant_id", tenant_id)
                .neq("estado", "cancelada")
                .gte("fecha_cita", hoy_iso)
                .order("fecha_cita", desc=False)
                .order("hora_cita", desc=False)
                .limit(30)
                .execute()
            )
            citas_negocio = r_negocio.data or []
        except Exception as e:
            logger.warning(f"No se pudieron cargar citas del negocio: {e}")

        # -- 9. Construir prompt con contexto dinamico -----------------------
        from domain.prompt_builder import build_system_prompt, inject_dynamic_context

        profesionales = []
        if odoo_config:
            from domain.odoo_service import OdooService
            odoo_svc = OdooService(**odoo_config)
            profesionales_odoo = odoo_svc.get_professionals()
            for p in profesionales_odoo:
                p_name = p.get("name", "")
                specs = p.get("specialties", [])
                if p_name:
                    specs_text = ", ".join(specs) if specs else "Cualquiera"
                    profesionales.append(f"- {p_name} (ofrece: {specs_text})")

        ai_prompt_manual = tenant.get("ai_prompt") or ""
        if ai_prompt_manual and len(ai_prompt_manual.strip()) > 50:
            system_prompt = inject_dynamic_context(
                base_prompt=ai_prompt_manual,
                tenant=tenant,
                citas_cliente=citas_cliente,
                citas_negocio=citas_negocio,
                profesionales=profesionales,
            )
        else:
            system_prompt = build_system_prompt(
                tenant=tenant,
                tenant_config=tenant_config,
                citas_cliente=citas_cliente,
                citas_negocio=citas_negocio,
                profesionales=profesionales,
            )

        # -- 10. Llamar a la IA (con tool calling para check/create appointment) --
        ai = AIService()
        negocio_servicios = tenant_config.get("servicios_texto", "") if tenant_config else ""

        response_text, booking_data = await ai.get_response(
            user_message=message_text,
            history=history_context,
            system_prompt=system_prompt,
            odoo_config=odoo_config,
            sender_wa_id=sender_wa_id,
            sender_name=sender_name,
            negocio_servicios=negocio_servicios,
            tenant_id=tenant_id,
        )
        # -- 10b. Garantizar recordatorio de reprogramación/cancelación de 1h -------
        if response_text and "servicio:" in response_text.lower() and "fecha:" in response_text.lower() and "hora:" in response_text.lower():
            if "una hora antes" not in response_text.lower() and "1 hora antes" not in response_text.lower() and "reprogramar" not in response_text.lower():
                reminder_line = "*(Recuerda que si requieres reprogramar o cancelar tu cita, debes hacerlo al menos una hora antes)*"
                # Insertar antes de ¿Confirma? o Confirma?
                match_conf = re.search(r"(\n\s*¿?\s*confirma\??\s*👍?)", response_text, re.IGNORECASE)
                if match_conf:
                    idx = match_conf.start()
                    response_text = response_text[:idx] + "\n\n" + reminder_line + "\n" + response_text[idx:]
                else:
                    response_text = response_text.rstrip() + "\n\n" + reminder_line

        logger.debug(f"[{tenant['nombre']}] GPT response: {response_text[:300]}")

        # -- 11. Preparar WhatsApp -------------------------------------------
        wa = WhatsAppService(
            phone_number_id=tenant["wa_phone_id"],
            access_token=tenant["wa_access_token"],
        )

        # -- 12. Persistir booking (upsert: una cita activa por cliente) --------
        # booking_data viene de ai_service cuando GPT llamo create_appointment tool
        if booking_data:
            try:
                new_odoo_id  = str(booking_data["odoo_event_id"])
                new_fecha    = booking_data.get("fecha", "")
                new_hora     = booking_data.get("hora", "00:00") + ":00"
                new_servicio = booking_data.get("servicio", "")
                new_nombre   = booking_data.get("cliente_nombre", sender_name or sender_wa_id)

                from datetime import date as date_cls2
                hoy_str = date_cls2.today().strftime("%Y-%m-%d")
                existing_result = (
                    db.table("citas_log")
                    .select("id, odoo_event_id, fecha_cita")
                    .eq("tenant_id", tenant_id)
                    .eq("wa_from", sender_wa_id)
                    .eq("estado", "confirmada")
                    .gte("fecha_cita", hoy_str)
                    .execute()
                )
                existing_citas = existing_result.data or []

                if existing_citas:
                    if odoo_config:
                        try:
                            from domain.odoo_service import OdooService
                            odoo_svc = OdooService(**odoo_config)
                            for cita_v in existing_citas:
                                old_eid = cita_v.get("odoo_event_id")
                                if old_eid and str(old_eid) != new_odoo_id:
                                    try:
                                        odoo_svc.cancel_appointment(int(old_eid))
                                        logger.info(f"[{tenant['nombre']}] Odoo event {old_eid} cancelado (-> {new_odoo_id})")
                                    except Exception as ce:
                                        logger.warning(f"No se pudo cancelar Odoo event {old_eid}: {ce}")
                        except Exception as oe:
                            logger.warning(f"Error OdooService al cancelar cita vieja: {oe}")
                    old_ids = [c["id"] for c in existing_citas]
                    db.table("citas_log").update({"estado": "reagendada"}).in_("id", old_ids).execute()
                    logger.info(f"[{tenant['nombre']}] {len(old_ids)} cita(s) marcadas reagendadas")

                log_entry = {
                    "tenant_id":      tenant_id,
                    "wa_from":        sender_wa_id,
                    "cliente_nombre": new_nombre,
                    "servicio":       new_servicio,
                    "fecha_cita":     new_fecha,
                    "hora_cita":      new_hora,
                    "odoo_event_id":  new_odoo_id,
                    "origen":         "whatsapp_bot",
                    "estado":         "confirmada",
                }
                db.table("citas_log").insert(log_entry).execute()
                accion = "reagendada" if existing_citas else "nueva"
                logger.info(f"[{tenant['nombre']}] citas_log {accion}: {new_fecha} {new_hora} event_id={new_odoo_id}")
                db.table("chat_sessions").update({
                    "estado": "cita_confirmada",
                    "cita_odoo_id": new_odoo_id,
                }).eq("id", session["id"]).execute()

                # ── Email de notificación al dueño del negocio ──
                try:
                    await self._send_appointment_email_notification(
                        tenant=tenant,
                        booking_data=booking_data,
                        phone=sender_wa_id,
                    )
                except Exception as email_err:
                    logger.warning(f"[{tenant['nombre']}] Email de cita fallido (no crítico): {email_err}")
            except Exception as e:
                logger.error(f"Error persistiendo citas_log: {e}")

        # -- 13. Otras acciones CRM via JSON en texto (ESCALATE/CANCEL/RESCHEDULE) --
        # BOOK ya fue manejado por ai_service via OpenAI tool calling
        crm_action = self._extract_crm_action(response_text)
        clean_response = re.sub(r'\{"action".*?\}', '', response_text, flags=re.DOTALL).strip()

        if crm_action:
            action = crm_action.get("action")
            logger.info(f"[{tenant['nombre']}] Accion CRM detectada: {action}")

            if action == "ESCALATE":
                db.table("chat_sessions").update({"bot_mode": False}).eq("id", session["id"]).execute()
                logger.info(f"[{tenant['nombre']}] Bot pausado por ESCALATE para {sender_wa_id}")
                owner_phone = tenant.get("owner_phone")
                if owner_phone:
                    try:
                        wa_owner = WhatsAppService(
                            phone_number_id=tenant["wa_phone_id"],
                            access_token=tenant["wa_access_token"],
                        )
                        await wa_owner.send_text(
                            to=owner_phone,
                            message=(
                                f"*{tenant['nombre']}* Intervencion requerida\n"
                                f"Cliente: {sender_name or sender_wa_id}\n"
                                f"Numero: {sender_wa_id}\n"
                                f"El bot ha sido pausado. Por favor atiende al cliente."
                            )
                        )
                    except Exception as e:
                        logger.error(f"Error notificando al dueno por ESCALATE: {e}")

            elif action == "CANCEL" and odoo_config:
                date_str = crm_action.get("date", "")
                try:
                    from domain.odoo_service import OdooService
                    odoo = OdooService(**odoo_config)
                    cita_result = (
                        db.table("citas_log")
                        .select("id, odoo_event_id")
                        .eq("tenant_id", tenant_id)
                        .eq("wa_from", sender_wa_id)
                        .eq("fecha_cita", date_str)
                        .execute()
                    )
                    if cita_result.data:
                        cita = cita_result.data[0]
                        if cita.get("odoo_event_id"):
                            odoo.cancel_appointment(int(cita["odoo_event_id"]))
                        db.table("citas_log").update({"estado": "cancelada"}).eq("id", cita["id"]).execute()
                        logger.info(f"[{tenant['nombre']}] Cita cancelada: {date_str} — {sender_wa_id}")
                    else:
                        logger.warning(f"CANCEL: no se encontro cita para {sender_wa_id} {date_str}")
                except Exception as e:
                    logger.error(f"Error procesando CANCEL: {e}")

            elif action == "RESCHEDULE" and odoo_config:
                old_date = crm_action.get("old_date", "")
                new_date = crm_action.get("new_date", "")
                new_time = crm_action.get("new_time", "00:00")
                try:
                    from domain.odoo_service import OdooService
                    odoo = OdooService(**odoo_config)
                    cita_result = (
                        db.table("citas_log")
                        .select("id, odoo_event_id")
                        .eq("tenant_id", tenant_id)
                        .eq("wa_from", sender_wa_id)
                        .eq("fecha_cita", old_date)
                        .execute()
                    )
                    if cita_result.data:
                        cita = cita_result.data[0]
                        new_start_dt = f"{new_date} {new_time}:00"
                        if cita.get("odoo_event_id"):
                            odoo.reschedule_appointment(
                                event_id=int(cita["odoo_event_id"]),
                                date_str=new_date,
                                time_str=new_time,
                            )
                        db.table("citas_log").update({
                            "fecha_cita": new_date,
                            "hora_cita":  f"{new_time}:00",
                        }).eq("id", cita["id"]).execute()
                        logger.info(f"[{tenant['nombre']}] Cita reagendada a {new_start_dt} para {sender_wa_id}")
                    else:
                        logger.warning(f"RESCHEDULE: no se encontro cita para {sender_wa_id} {old_date}")
                except Exception as e:
                    logger.error(f"Error procesando RESCHEDULE: {e}")
        else:
            logger.info(f"[{tenant['nombre']}] Sin accion CRM en el response")

        # -- 14. Enviar respuesta al cliente ----------------------------------
        await wa.send_text(to=sender_wa_id, message=clean_response or response_text)
        await wa.mark_as_read(message_id)

        # -- 15. Actualizar historial en Supabase ----------------------------
        new_history = history + [
            user_message_entry,
            {
                "role": "assistant",
                "content": response_text,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        ]
        new_history = new_history[-(MAX_HISTORY * 2):]
        self._update_session(db, session["id"], new_history)

        # -- 16. Enviar notificación de correo para lead calificado (exclusivo ventas) --
        from config import settings
        if str(tenant_id) == settings.SALES_TENANT_ID:
            if booking_data:
                asyncio.create_task(
                    self._send_lead_email_notification(
                        tenant=tenant,
                        session=session,
                        history=new_history,
                        event_type="demo_scheduled",
                        booking_data=booking_data
                    )
                )
            elif crm_action and crm_action.get("action") == "ESCALATE":
                asyncio.create_task(
                    self._send_lead_email_notification(
                        tenant=tenant,
                        session=session,
                        history=new_history,
                        event_type="escalation_requested"
                    )
                )

        logger.info(f"[{tenant['nombre']}] Respuesta enviada a {sender_wa_id}")

    # -------------------------------------------------------------------------
    # HELPERS
    # -------------------------------------------------------------------------

    def _get_or_create_session(self, db, tenant_id: str, wa_from: str, name: str = None) -> dict:
        try:
            result = (
                db.table("chat_sessions")
                .select("*")
                .eq("tenant_id", tenant_id)
                .eq("wa_from", wa_from)
                .single()
                .execute()
            )
            if result.data:
                existing = result.data
                if not existing.get("name") and name:
                    try:
                        db.table("chat_sessions").update({"name": name}).eq("id", existing["id"]).execute()
                        existing["name"] = name
                    except Exception as ue:
                        logger.warning(f"Error actualizando nombre en sesion existente: {ue}")
                return existing
        except Exception:
            pass

        try:
            result = (
                db.table("chat_sessions")
                .insert({
                    "tenant_id": tenant_id,
                    "wa_from":   wa_from,
                    "name":      name,
                    "history":   [],
                    "bot_mode":  True,
                    "estado":    "activo",
                })
                .execute()
            )
            return result.data[0]
        except Exception as e:
            logger.error(f"Error creando sesion: {e}")
            return {"id": None, "history": [], "bot_mode": True}

    def _update_session(self, db, session_id: str, new_history: list):
        if not session_id:
            return
        try:
            db.table("chat_sessions").update({
                "history":    new_history,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }).eq("id", session_id).execute()
        except Exception as e:
            logger.error(f"Error actualizando historial: {e}")

    def _extract_crm_action(self, text: str) -> dict | None:
        """
        Extrae acciones CRM en JSON del texto (ESCALATE, CANCEL, RESCHEDULE, LEAD, PQR).
        BOOK ya NO se maneja aqui — lo gestiona ai_service via OpenAI tool calling.
        """
        try:
            match = re.search(
                r'\{"action"\s*:\s*"(LEAD|PQR|ESCALATE|CANCEL|RESCHEDULE)".*?\}',
                text, re.DOTALL
            )
            if match:
                return json.loads(match.group(0))
        except Exception as e:
            logger.warning(f"No se pudo parsear accion CRM: {e}")
        return None

    # ────────────────────────────────────────────────────────
    # EMAIL: Notificación de CITA RESERVADA al dueño del negocio
    # ────────────────────────────────────────────────────────
    async def _send_appointment_email_notification(
        self,
        tenant: dict,
        booking_data: dict,
        phone: str,
    ) -> None:
        """
        Envía un email de notificación al dueño del negocio cuando
        el bot agenda una cita vía WhatsApp.
        Solo se dispara si el tenant tiene 'notification_email' configurado.
        """
        notification_email = tenant.get("notification_email")
        if not notification_email:
            logger.info(
                f"[{tenant.get('nombre')}] Sin notification_email configurado — "
                "notificación de cita omitida."
            )
            return

        from infrastructure.email_service import EmailService
        from datetime import datetime

        negocio     = tenant.get("nombre", "Tu Negocio")
        cliente     = booking_data.get("cliente_nombre") or "Cliente"
        servicio    = booking_data.get("servicio") or "No especificado"
        precio      = booking_data.get("precio") or ""
        fecha_raw   = booking_data.get("fecha", "")
        hora_raw    = booking_data.get("hora", "")
        wa_link     = f"https://wa.me/{phone.lstrip('+')}"

        # Formatear fecha legible
        try:
            meses = ["enero","febrero","marzo","abril","mayo","junio",
                     "julio","agosto","septiembre","octubre","noviembre","diciembre"]
            dt = datetime.strptime(fecha_raw, "%Y-%m-%d")
            fecha_fmt = f"{dt.day} de {meses[dt.month-1]} de {dt.year}"
        except Exception:
            fecha_fmt = fecha_raw

        precio_html = (
            f'<tr><td style="color:#94a3b8;padding:6px 0">Valor estimado</td>'
            f'<td style="color:#fff;font-weight:700;text-align:right">$ {precio}</td></tr>'
        ) if precio else ""

        html_body = f"""
<!DOCTYPE html>
<html lang="es">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Nueva Cita — {negocio}</title></head>
<body style="margin:0;padding:0;background:#04060c;font-family:'Helvetica Neue',Arial,sans-serif">

  <table width="100%" cellpadding="0" cellspacing="0" style="background:#04060c;padding:32px 16px">
    <tr><td align="center">

      <table width="100%" style="max-width:560px" cellpadding="0" cellspacing="0">

        <!-- Header -->
        <tr><td style="background:linear-gradient(135deg,#1a1f35 0%,#0f1420 100%);border-radius:16px 16px 0 0;padding:32px;text-align:center;border-bottom:1px solid rgba(99,102,241,0.25)">
          <div style="display:inline-block;background:linear-gradient(135deg,#6366f1,#06b6d4);border-radius:12px;width:48px;height:48px;line-height:48px;font-size:24px;margin-bottom:16px">&#128197;</div>
          <h1 style="margin:0;color:#fff;font-size:22px;font-weight:800;letter-spacing:-0.03em">
            ¡Nueva Cita Agendada!
          </h1>
          <p style="margin:8px 0 0;color:#94a3b8;font-size:13px">{negocio} &mdash; Reserva vía WhatsApp Bot</p>
        </td></tr>

        <!-- Body -->
        <tr><td style="background:#0d1117;padding:32px;border:1px solid rgba(255,255,255,0.05);border-top:none">

          <!-- Alert badge -->
          <div style="background:linear-gradient(135deg,rgba(99,102,241,0.1),rgba(6,182,212,0.07));border:1px solid rgba(99,102,241,0.25);border-radius:10px;padding:12px 16px;margin-bottom:24px;color:#a5b4fc;font-size:13px;font-weight:600">
            &#128276; Una nueva cita ha sido confirmada automáticamente por el Agente IA.
          </div>

          <!-- Appointment details table -->
          <table width="100%" cellpadding="0" cellspacing="0" style="background:#080c14;border:1px solid rgba(255,255,255,0.06);border-radius:12px;padding:20px;margin-bottom:24px">
            <tr><td colspan="2" style="color:#64748b;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:0.1em;padding-bottom:12px;border-bottom:1px solid rgba(255,255,255,0.05)">DETALLES DE LA CITA</td></tr>
            <tr><td style="color:#94a3b8;padding:10px 0 6px">&#128100; Cliente</td>
                <td style="color:#fff;font-weight:800;text-align:right;padding:10px 0 6px">{cliente}</td></tr>
            <tr><td style="color:#94a3b8;padding:6px 0">&#128241; WhatsApp</td>
                <td style="text-align:right;padding:6px 0">
                  <a href="{wa_link}" style="color:#25d366;font-weight:700;text-decoration:none">{phone}</a>
                </td></tr>
            <tr style="border-top:1px solid rgba(255,255,255,0.04)">
                <td style="color:#94a3b8;padding:10px 0 6px">&#9986;&#65039; Servicio</td>
                <td style="color:#a5b4fc;font-weight:700;text-align:right;padding:10px 0 6px">{servicio}</td></tr>
            <tr><td style="color:#94a3b8;padding:6px 0">&#128197; Fecha</td>
                <td style="color:#fff;font-weight:700;text-align:right;padding:6px 0">{fecha_fmt}</td></tr>
            <tr><td style="color:#94a3b8;padding:6px 0">&#128336; Hora</td>
                <td style="color:#38bdf8;font-weight:800;text-align:right;padding:6px 0">{hora_raw}</td></tr>
            {precio_html}
          </table>

          <!-- CTA -->
          <div style="text-align:center;margin-bottom:24px">
            <a href="{wa_link}" style="display:inline-block;background:linear-gradient(135deg,#25d366,#128c7e);color:#fff;font-weight:800;font-size:14px;text-decoration:none;padding:14px 32px;border-radius:12px"
            >&#128172; Responder por WhatsApp</a>
          </div>

          <p style="color:#475569;font-size:11px;text-align:center;margin:0">
            Este correo fue enviado automáticamente por <strong style="color:#6366f1">BeautySync Pro+</strong>
            cuando el cliente confirmó su cita a través del Agente IA de WhatsApp.
          </p>
        </td></tr>

        <!-- Footer -->
        <tr><td style="background:#080a10;border-radius:0 0 16px 16px;padding:20px;text-align:center;border:1px solid rgba(255,255,255,0.04);border-top:none">
          <p style="margin:0;color:#334155;font-size:11px">&copy; 2024 BeautySync Pro+ &mdash; Todos los derechos reservados</p>
        </td></tr>

      </table>
    </td></tr>
  </table>

</body>
</html>
"""

        subject = f"\U0001f4c5 Nueva Cita \u2014 {cliente} | {fecha_fmt} {hora_raw} | {negocio}"
        email_svc = EmailService()
        ok = await email_svc.send_html_email(
            subject=subject,
            html_content=html_body,
            recipient=notification_email,
        )
        if ok:
            logger.info(f"[{negocio}] ✅ Email de cita enviado a {notification_email}")
        else:
            logger.warning(f"[{negocio}] ⚠️ Email de cita fallido — revisar credenciales SMTP")

    # ────────────────────────────────────────────────────────
    # EMAIL: Notificación de LEAD CALIFICADO al equipo de ventas
    # ────────────────────────────────────────────────────────
    async def _send_lead_email_notification(
        self,
        tenant: dict,
        session: dict,
        history: list,
        event_type: str,
        booking_data: dict = None
    ):
        """
        Envía un correo electrónico con los datos de un lead calificado.
        Solo se ejecuta si el tenant es el del embudo de ventas.
        """
        from config import settings
        from domain.ai_service import AIService
        from infrastructure.email_service import EmailService

        logger.info(f"[{tenant['nombre']}] Lead calificado detectado. Iniciando extracción de datos y envío de email...")

        try:
            # 1. Extraer detalles estructurados usando GPT
            ai = AIService()
            lead_details = await ai.extract_lead_details(history)

            # 2. Preparar los datos
            prospect_name = lead_details.get("nombre") or session.get("name") or "Desconocido"
            negocio_name = lead_details.get("negocio") or "No especificado"
            tipo_negocio = lead_details.get("tipo_negocio") or "No especificado"
            inversion_insumos = lead_details.get("inversion_insumos") or "No especificada"
            plan_interes = lead_details.get("plan_interes") or "No especificado"
            email = lead_details.get("email") or "No especificado"
            telefono_contacto = lead_details.get("telefono") or "No especificado"
            nit_rut = lead_details.get("nit_rut") or "No especificado"
            resumen = lead_details.get("resumen_interes") or "Lead calificado en el embudo."

            wa_number = session.get("wa_from", "")
            wa_link = f"https://wa.me/{wa_number}" if wa_number else "#"

            # Si es por agendamiento de cita/demo, enriquecer con booking_data
            event_title = "🔥 ¡Nuevo Lead Calificado detectado!"
            if event_type == "demo_scheduled" and booking_data:
                event_title = "📅 ¡Nueva Demo Agendada!"
                if not plan_interes or plan_interes == "No especificado":
                    plan_interes = "Demo"
                if booking_data.get("cliente_nombre"):
                    prospect_name = booking_data.get("cliente_nombre")

            # 3. Construir variables dinámicas para evitar errores de parseo en el f-string principal
            badge_class = "badge-demo" if event_type == "demo_scheduled" else "badge-escalate"
            badge_label = "Demo Agendada en Calendario" if event_type == "demo_scheduled" else "Solicitud de Contacto / Asesor"
            
            nit_section = ""
            if nit_rut and nit_rut != "No especificado":
                nit_section = f"""
                <div class="info-item">
                    <div class="info-label">NIT / RUT</div>
                    <div class="info-value">{nit_rut}</div>
                </div>
                """

            # 4. Construir cuerpo del correo en HTML
            html_body = f"""
            <!DOCTYPE html>
            <html>
            <head>
                <meta charset="utf-8">
                <style>
                    body {{
                        font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif;
                        background-color: #f4f6f9;
                        color: #333333;
                        margin: 0;
                        padding: 0;
                    }}
                    .container {{
                        max-width: 600px;
                        margin: 20px auto;
                        background: #ffffff;
                        border-radius: 12px;
                        overflow: hidden;
                        box-shadow: 0 4px 15px rgba(0,0,0,0.05);
                        border: 1px solid #e1e8ed;
                    }}
                    .header {{
                        background: linear-gradient(135deg, #6a11cb 0%, #2575fc 100%);
                        color: #ffffff;
                        padding: 30px 20px;
                        text-align: center;
                    }}
                    .header h1 {{
                        margin: 0;
                        font-size: 24px;
                        font-weight: 700;
                        letter-spacing: 0.5px;
                    }}
                    .header p {{
                        margin: 5px 0 0 0;
                        font-size: 14px;
                        opacity: 0.9;
                    }}
                    .content {{
                        padding: 30px 25px;
                    }}
                    .badge {{
                        display: inline-block;
                        padding: 6px 12px;
                        font-size: 12px;
                        font-weight: bold;
                        border-radius: 20px;
                        text-transform: uppercase;
                        margin-bottom: 20px;
                    }}
                    .badge-demo {{
                        background-color: #e3f2fd;
                        color: #0d47a1;
                    }}
                    .badge-escalate {{
                        background-color: #efebe9;
                        color: #4e342e;
                    }}
                    .section-title {{
                        font-size: 16px;
                        font-weight: bold;
                        border-bottom: 2px solid #f0f2f5;
                        padding-bottom: 8px;
                        margin-top: 25px;
                        margin-bottom: 15px;
                        color: #1a1a1a;
                    }}
                    .info-grid {{
                        display: grid;
                        grid-template-columns: 1fr;
                        gap: 12px;
                    }}
                    .info-item {{
                        padding: 10px 12px;
                        background-color: #f8fafc;
                        border-radius: 6px;
                        border-left: 3px solid #cbd5e1;
                    }}
                    .info-label {{
                        font-size: 11px;
                        text-transform: uppercase;
                        color: #64748b;
                        font-weight: bold;
                        margin-bottom: 2px;
                    }}
                    .info-value {{
                        font-size: 14px;
                        color: #0f172a;
                        font-weight: 500;
                    }}
                    .btn-whatsapp {{
                        display: block;
                        text-align: center;
                        background-color: #25d366;
                        color: #ffffff;
                        text-decoration: none;
                        padding: 14px 20px;
                        border-radius: 8px;
                        font-weight: bold;
                        margin: 30px 0 10px 0;
                        box-shadow: 0 4px 10px rgba(37,211,102,0.2);
                        transition: background-color 0.2s;
                    }}
                    .btn-whatsapp:hover {{
                        background-color: #128c7e;
                    }}
                    .history-box {{
                        background-color: #0f172a;
                        color: #e2e8f0;
                        padding: 15px;
                        border-radius: 8px;
                        font-family: 'Courier New', Courier, monospace;
                        font-size: 12px;
                        max-height: 250px;
                        overflow-y: auto;
                        white-space: pre-wrap;
                        border: 1px solid #1e293b;
                    }}
                    .footer {{
                        background-color: #f8fafc;
                        padding: 20px;
                        text-align: center;
                        font-size: 11px;
                        color: #94a3b8;
                        border-top: 1px solid #e2e8f0;
                    }}
                </style>
            </head>
            <body>
                <div class="container">
                    <div class="header">
                        <h1>{event_title}</h1>
                        <p>Plataforma BeautySync Pro — TESO CONSULTING SAS</p>
                    </div>
                    <div class="content">
                        <div style="text-align: center;">
                            <span class="badge {badge_class}">
                                {badge_label}
                            </span>
                        </div>
                        
                        <div class="section-title">Datos del Prospecto</div>
                        <div class="info-grid">
                            <div class="info-item" style="border-left-color: #6a11cb;">
                                <div class="info-label">Nombre del Cliente</div>
                                <div class="info-value">{prospect_name}</div>
                            </div>
                            <div class="info-item" style="border-left-color: #2575fc;">
                                <div class="info-label">Nombre del Negocio</div>
                                <div class="info-value">{negocio_name} ({tipo_negocio})</div>
                            </div>
                            <div class="info-item">
                                <div class="info-label">WhatsApp</div>
                                <div class="info-value">+{wa_number}</div>
                            </div>
                            <div class="info-item">
                                <div class="info-label">Email</div>
                                <div class="info-value">{email}</div>
                            </div>
                            <div class="info-item">
                                <div class="info-label">Inversión Mensual en Insumos</div>
                                <div class="info-value">{inversion_insumos}</div>
                            </div>
                            <div class="info-item">
                                <div class="info-label">Plan de Interés</div>
                                <div class="info-value">{plan_interes}</div>
                            </div>
                            {nit_section}
                        </div>

                        <div class="section-title">Resumen de Intención</div>
                        <p style="font-size: 14px; line-height: 1.5; color: #475569; margin: 0 0 20px 0;">
                            {resumen}
                        </p>

                        <a href="{wa_link}" class="btn-whatsapp" target="_blank">
                            💬 Chatear con {prospect_name} por WhatsApp
                        </a>

                        <div class="section-title">Historial Reciente de Conversación</div>
                        <div class="history-box">"""

            # Agregar el historial
            for msg in history[-12:]:
                role_label = "Cliente" if msg.get("role") == "user" else "BETH (IA)"
                html_body += f"[{role_label}]: {msg.get('content')}\n\n"

            html_body += """</div>
                    </div>
                    <div class="footer">
                        Este correo es una alerta automática generada por el agente de ventas de BeautySync Pro.<br>
                        Desarrollado y respaldado por <strong>TESO CONSULTING SAS</strong>.<br>
                        © 2026 TESO CONSULTING SAS. Todos los derechos reservados.
                    </div>
                </div>
            </body>
            </html>
            """

            # 4. Enviar el correo electrónico
            email_svc = EmailService()
            
            subject = f"🚨 Lead Calificado: {prospect_name} - {negocio_name}"
            if event_type == "demo_scheduled":
                subject = f"📅 Demo Agendada: {prospect_name} - {negocio_name}"

            await email_svc.send_html_email(subject, html_body)
        except Exception as ex:
            logger.error(f"Error procesando la notificación de lead para el correo: {ex}", exc_info=True)
