# domain/message_handler.py
# Orquestador principal — alineado con schema real de chat_sessions
#
# Schema chat_sessions:
#   id, tenant_id, wa_from, history (JSONB []), estado, 
#   cita_odoo_id, updated_at, bot_mode, name

import logging
import json
from datetime import datetime, timezone
from infrastructure.repositories.tenant_repo import TenantRepository
from domain.ai_service import AIService
from domain.whatsapp_service import WhatsAppService
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
        # Si bot_mode=False, el agente humano tomó el control
        if not session.get("bot_mode", True):
            logger.info(f"[{tenant['nombre']}] bot_mode=False para {sender_wa_id}. IA pausada, solo se guarda el mensaje.")
            # Actualizamos el historial solo con el mensaje del usuario para que el panel lo vea
            self._update_session(db, session["id"], history + [user_message_entry])
            return

        # ── 5. Preparar historial para OpenAI ─────────────
        # Recortar a últimos MAX_HISTORY turnos (solo para la IA)
        history_context = history[-MAX_HISTORY:]

        # ── 6. Llamar a IA ────────────────────────────────
        ai_prompt = tenant.get("ai_prompt") or None
        
        odoo_config = None
        if tenant.get("plan") == "pro" and tenant.get("odoo_url"):
            odoo_config = {
                "odoo_url": tenant.get("odoo_url"),
                "odoo_db": tenant.get("odoo_db"),
                "odoo_user": tenant.get("odoo_user"),
                "odoo_api_key": tenant.get("odoo_api_key"),
            }
            
        ai = AIService()
        response_text = await ai.get_response(
            user_message=message_text,
            history=history_context,
            system_prompt=ai_prompt,
            odoo_config=odoo_config
        )

        # ── 7. Enviar respuesta por WhatsApp ───────────────
        wa = WhatsAppService(
            phone_number_id=tenant["wa_phone_id"],
            access_token=tenant["wa_access_token"],
        )
        await wa.send_text(to=sender_wa_id, message=response_text)
        await wa.mark_as_read(message_id)

        # ── 8. Actualizar historial en Supabase (Usuario + IA) ────────────
        new_history = history + [
            user_message_entry,
            {"role": "assistant", "content": response_text, "timestamp": datetime.now(timezone.utc).isoformat()},
        ]
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
