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

        ai_prompt_manual = tenant.get("ai_prompt") or ""
        if ai_prompt_manual and len(ai_prompt_manual.strip()) > 50:
            system_prompt = inject_dynamic_context(
                base_prompt=ai_prompt_manual,
                tenant=tenant,
                citas_cliente=citas_cliente,
                citas_negocio=citas_negocio,
            )
        else:
            system_prompt = build_system_prompt(
                tenant=tenant,
                tenant_config=tenant_config,
                citas_cliente=citas_cliente,
                citas_negocio=citas_negocio,
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
        )

        logger.debug(f"[{tenant['nombre']}] GPT response: {response_text[:300]}")

        # -- 11. Preparar WhatsApp -------------------------------------------
        wa = WhatsAppService(
            phone_number_id=tenant["wa_phone_id"],
            access_token=tenant["wa_access_token"],
        )

        # -- 12. Persistir booking si ai_service creo cita en Odoo -----------
        # booking_data viene de ai_service cuando GPT llamo create_appointment tool
        if booking_data:
            try:
                log_entry = {
                    "tenant_id":      tenant_id,
                    "wa_from":        sender_wa_id,
                    "cliente_nombre": booking_data.get("cliente_nombre", sender_name or sender_wa_id),
                    "servicio":       booking_data.get("servicio", ""),
                    "fecha_cita":     booking_data.get("fecha", ""),
                    "hora_cita":      booking_data.get("hora", "00:00") + ":00",
                    "odoo_event_id":  str(booking_data["odoo_event_id"]),
                    "origen":         "whatsapp_bot",
                    "estado":         "confirmada",
                }
                db.table("citas_log").insert(log_entry).execute()
                logger.info(
                    f"[{tenant['nombre']}] citas_log OK: "
                    f"{booking_data.get('fecha')} {booking_data.get('hora')} "
                    f"event_id={booking_data['odoo_event_id']}"
                )
                db.table("chat_sessions").update({
                    "estado":      "cita_confirmada",
                    "cita_odoo_id": str(booking_data["odoo_event_id"]),
                }).eq("id", session["id"]).execute()
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
                            odoo.reschedule_appointment(int(cita["odoo_event_id"]), new_start_dt)
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
                return result.data
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
