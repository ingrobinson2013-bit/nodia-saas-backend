# domain/message_handler.py
# Orquestador principal — alineado con schema real de chat_sessions
#
# Schema chat_sessions:
#   id, tenant_id, wa_from, history (JSONB []), estado, 
#   cita_odoo_id, updated_at, bot_mode, name

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

MAX_HISTORY = 10  # Máximo de turnos en contexto para OpenAI


class MessageHandler:
    """
    Flujo completo de un mensaje entrante:
    1. Identificar tenant por wa_phone_id
    2. Recuperar/crear sesión en chat_sessions (upsert)
    3. Verificar bot_mode (si está pausado, solo notifica)
    4. Llamar a IA con historial JSONB
    5. Responder al usuario via Meta API
    6. Actualizar historial en Supabase (JSONB append)
    """

    async def handle(
        self,
        phone_number_id: str,
        sender_wa_id: str,
        message_text: str,
        message_id: str,
        sender_name: str = None,
    ):
        # ── 1. Identificar tenant ──────────────────────────
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

        # ── 2. Recuperar o crear sesión ────────────────────
        session = self._get_or_create_session(db, tenant_id, sender_wa_id, sender_name)

        # ── 3. Guardar el mensaje del usuario en el historial ─────────
        history = session.get("history") or []
        user_message_entry = {"role": "user", "content": message_text, "timestamp": datetime.now(timezone.utc).isoformat()}
        
        # ── 4. Verificar bot_mode ──────────────────────────
        if not session.get("bot_mode", True):
            logger.info(f"[{tenant['nombre']}] bot_mode=False para {sender_wa_id}. IA pausada.")
            self._update_session(db, session["id"], history + [user_message_entry])
            return

        # ── 5. Preparar historial para OpenAI ─────────────
        history_context = history[-MAX_HISTORY:]

        # ── 6. Cargar tenant_config (dirección, horario, servicios) ───
        tenant_config = None
        try:
            tc_result = db.table("tenant_config").select("*").eq("tenant_id", tenant_id).single().execute()
            tenant_config = tc_result.data
        except Exception:
            pass  # tenant_config es opcional

        # ── 7. Obtener citas de Odoo (si tiene credenciales configuradas) ──
        citas_cliente = []
        citas_negocio = []
        odoo_config = None
        if tenant.get("odoo_url") and tenant.get("odoo_url").strip():
            odoo_config = {
                "url":     tenant.get("odoo_url"),
                "db":      tenant.get("odoo_db"),
                "user":    tenant.get("odoo_user"),
                "api_key": tenant.get("odoo_api_key"),
            }

        # ── 7. Cargar citas desde Supabase citas_log (igual que n8n) ────────────
        # Más rápido y no depende de disponibilidad de Odoo
        from datetime import date as date_cls
        hoy_iso = date_cls.today().strftime("%Y-%m-%d")

        try:
            # Citas futuras del CLIENTE en este negocio
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
            # Horas ocupadas del NEGOCIO (todas las citas confirmadas)
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

        # ── 8. Construir prompt con contexto dinámico ────────
        # Si el tenant tiene prompt personalizado en Supabase → usarlo como base
        # y siempre inyectar el bloque dinámico (fechas, calendario, citas de Odoo)
        from domain.prompt_builder import build_system_prompt, inject_dynamic_context

        ai_prompt_manual = tenant.get("ai_prompt") or ""
        if ai_prompt_manual and len(ai_prompt_manual.strip()) > 50:
            # ✅ Prompt personalizado del negocio + contexto dinámico inyectado
            system_prompt = inject_dynamic_context(
                base_prompt=ai_prompt_manual,
                tenant=tenant,
                citas_cliente=citas_cliente,
                citas_negocio=citas_negocio,
            )
        else:
            # Fallback: prompt genérico de VALE con todos los datos del negocio
            system_prompt = build_system_prompt(
                tenant=tenant,
                tenant_config=tenant_config,
                citas_cliente=citas_cliente,
                citas_negocio=citas_negocio,
            )

        ai = AIService()
        response_text = await ai.get_response(
            user_message=message_text,
            history=history_context,
            system_prompt=system_prompt,
            odoo_config=odoo_config
        )

        # ── 7. Enviar respuesta por WhatsApp ───────────────
        wa = WhatsAppService(
            phone_number_id=tenant["wa_phone_id"],
            access_token=tenant["wa_access_token"],
        )
        # ── 10. Parsear acciones CRM del response ─────────
        crm_action = self._extract_crm_action(response_text)
        # El JSON de accion se elimina del mensaje visible al cliente
        # pero se PRESERVA en el historial para que GPT mantenga contexto
        clean_response = re.sub(r'\{"action".*?\}', '', response_text, flags=re.DOTALL).strip()

        # DEBUG: loguear el response de GPT para diagnosticar BOOK
        logger.debug(f"[{tenant['nombre']}] GPT response: {response_text[:300]}")
        if crm_action:
            logger.info(f"[{tenant['nombre']}] Accion CRM detectada: {crm_action}")
        else:
            logger.info(f"[{tenant['nombre']}] Sin accion CRM en el response")

        if crm_action:
            action = crm_action.get("action")

            if action == "ESCALATE":
                # Pausar la IA y pasar el control al humano
                db.table("chat_sessions").update({"bot_mode": False}).eq("id", session["id"]).execute()
                logger.info(f"[{tenant['nombre']}] Bot pausado por ESCALATE para {sender_wa_id}")
                # Notificar al dueño si tiene owner_phone configurado
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
                                f"🚨 *{tenant['nombre']}* — Intervención requerida\n"
                                f"Cliente: {sender_name or sender_wa_id}\n"
                                f"Número: {sender_wa_id}\n"
                                f"El bot ha sido pausado. Por favor atiende al cliente."
                            )
                        )
                    except Exception as e:
                        logger.error(f"Error notificando al dueño por ESCALATE: {e}")

            elif action == "BOOK":
                date_str = crm_action.get("date", "")
                time_str = crm_action.get("time", "00:00")
                cliente_nombre = crm_action.get("name", sender_name or sender_wa_id)
                servicio = crm_action.get("service", "")
                precio   = crm_action.get("price", "")
                odoo_event_id = None

                # Crear en Odoo con conversión Bogotá → UTC
                if odoo_config:
                    try:
                        from domain.odoo_service import OdooService
                        odoo = OdooService(**odoo_config)
                        # Leer servicios del negocio para calcular duración
                        negocio_servicios = ""
                        if tenant_config:
                            negocio_servicios = tenant_config.get("servicios_texto", "")
                        odoo_event_id = odoo.create_appointment(
                            name=cliente_nombre,
                            phone=sender_wa_id,
                            date_str=date_str,
                            time_str=time_str,
                            service_name=servicio,
                            price=precio,
                            negocio_servicios=negocio_servicios,
                            description=f"Agendado vía WhatsApp Bot NODIA",
                        )
                        if odoo_event_id:
                            logger.info(f"[{tenant['nombre']}] ✅ Cita Odoo creada: {date_str} {time_str} Bogotá — {cliente_nombre}")
                        else:
                            logger.error(f"[{tenant['nombre']}] ❌ OdooService.create_appointment retornó None")
                    except Exception as e:
                        logger.error(f"[{tenant['nombre']}] ❌ Error creando cita en Odoo: {e}")

                # ✅ Siempre guardar en citas_log (incluso sin Odoo)
                try:
                    log_entry = {
                        "tenant_id": tenant_id,
                        "wa_from": sender_wa_id,
                        "cliente_nombre": cliente_nombre,
                        "servicio": servicio,
                        "fecha_cita": date_str,
                        "hora_cita": f"{time_str}:00",
                        "odoo_event_id": str(odoo_event_id) if odoo_event_id else None,
                        "origen": "whatsapp_bot",
                    }
                    db.table("citas_log").insert(log_entry).execute()
                    logger.info(f"[{tenant['nombre']}] Cita guardada en citas_log: {date_str} {time_str} — {cliente_nombre}")

                    # Actualizar chat_session con estado 'cita_confirmada' y referencia a Odoo
                    update_data = {"estado": "cita_confirmada"}
                    if odoo_event_id:
                        update_data["cita_odoo_id"] = str(odoo_event_id)
                        
                    db.table("chat_sessions").update(update_data).eq("id", session["id"]).execute()
                except Exception as e:
                    logger.error(f"Error guardando en citas_log: {e}")

            # Limpiar el JSON del texto visible para el cliente
            clean_response = re.sub(r'\{"action".*?\}', '', response_text, flags=re.DOTALL).strip()

            # ── CANCEL: cancelar cita en Odoo + Supabase ────────────────
            if action == "CANCEL" and odoo_config:
                date_str  = crm_action.get("date", "")
                time_str  = crm_action.get("time", "00:00")
                try:
                    from domain.odoo_service import OdooService
                    odoo = OdooService(**odoo_config)
                    # Buscar en citas_log la cita a cancelar
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
                        logger.info(f"[{tenant['nombre']}] Cita cancelada: {date_str} {time_str} — {sender_wa_id}")
                    else:
                        logger.warning(f"CANCEL: no se encontró cita en citas_log para {sender_wa_id} {date_str}")
                except Exception as e:
                    logger.error(f"Error procesando CANCEL: {e}")

            # ── RESCHEDULE: reagendar cita en Odoo + Supabase ───────────
            elif action == "RESCHEDULE" and odoo_config:
                old_date  = crm_action.get("old_date", "")
                new_date  = crm_action.get("new_date", "")
                new_time  = crm_action.get("new_time", "00:00")
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
                        logger.warning(f"RESCHEDULE: no se encontró cita en citas_log para {sender_wa_id} {old_date}")
                except Exception as e:
                    logger.error(f"Error procesando RESCHEDULE: {e}")

        # Enviar solo el texto limpio al cliente
        await wa.send_text(to=sender_wa_id, message=clean_response or response_text)
        await wa.mark_as_read(message_id)

        # ── 11. Actualizar historial en Supabase ──────────
        # IMPORTANTE: guardar response_text completo (con JSON de accion)
        # para que GPT tenga contexto en el proximo turno.
        # El cliente solo ve clean_response (sin JSON).
        new_history = history + [
            user_message_entry,
            {
                "role": "assistant",
                "content": response_text,  # historial interno conserva el JSON
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        ]
        # Limitar a MAX_HISTORY turnos para no saturar tokens
        new_history = new_history[-(MAX_HISTORY * 2):]
        self._update_session(db, session["id"], new_history)

        logger.info(f"[{tenant['nombre']}] ✅ Respuesta enviada a {sender_wa_id}")

    # ──────────────────────────────────────────────────────
    # HELPERS
    # ──────────────────────────────────────────────────────

    def _get_or_create_session(
        self, db, tenant_id: str, wa_from: str, name: str = None
    ) -> dict:
        """
        Busca la sesión existente o crea una nueva.
        Usa upsert sobre la unique key (tenant_id, wa_from).
        """
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
            pass  # No existe aún — la creamos

        # Crear nueva sesión
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
            logger.error(f"Error creando sesión: {e}")
            # Retornar sesión mínima en memoria para no bloquear el flujo
            return {"id": None, "history": [], "bot_mode": True}

    def _update_session(self, db, session_id: str, new_history: list):
        """
        Actualiza el historial JSONB y updated_at de la sesión.
        """
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
        Extrae la acción CRM en JSON del texto de respuesta de la IA.
        VALE emite acciones como: {"action":"BOOK","name":"..."}
        Soporta: BOOK | LEAD | PQR | ESCALATE | CANCEL | RESCHEDULE
        """
        try:
            match = re.search(
                r'\{"action"\s*:\s*"(BOOK|LEAD|PQR|ESCALATE|CANCEL|RESCHEDULE)".*?\}',
                text, re.DOTALL
            )
            if match:
                return json.loads(match.group(0))
        except Exception as e:
            logger.warning(f"No se pudo parsear acción CRM: {e}")
        return None

