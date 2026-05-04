# domain/message_handler.py
# Orquestador principal: recibe mensaje → IA → responde → persiste

import logging
from infrastructure.repositories.tenant_repo import TenantRepository
from domain.ai_service import AIService
from domain.whatsapp_service import WhatsAppService
from infrastructure.database import get_supabase

logger = logging.getLogger(__name__)

tenant_repo = TenantRepository()


class MessageHandler:
    """
    Orquesta el flujo completo de un mensaje entrante de WhatsApp:
    1. Identificar tenant por wa_phone_id
    2. Recuperar/crear sesión de chat
    3. Llamar a IA con contexto
    4. Responder al usuario
    5. Persistir en Supabase
    """

    async def handle(self, phone_number_id: str, sender_wa_id: str, message_text: str, message_id: str):
        # ── 1. Identificar tenant ──────────────────────────
        tenant = tenant_repo.get_by_phone_id(phone_number_id)
        if not tenant:
            logger.warning(f"Tenant no encontrado para phone_id: {phone_number_id}")
            return

        if not tenant.get("activo", False):
            logger.info(f"Tenant {tenant['nombre']} inactivo. Mensaje ignorado.")
            return

        tenant_id = tenant["tenant_id"]
        logger.info(f"[{tenant['nombre']}] Mensaje de {sender_wa_id}: {message_text[:50]}...")

        # ── 2. Recuperar historial de chat_sessions ────────
        db = get_supabase()
        history = self._get_history(db, tenant_id, sender_wa_id)

        # ── 3. Llamar a IA ────────────────────────────────
        # Cargar prompt personalizado del tenant (si existe)
        ai_prompt = tenant.get("ai_prompt") or None

        ai = AIService()
        response_text = await ai.get_response(
            user_message=message_text,
            history=history,
            system_prompt=ai_prompt,  # Prompt personalizado por cliente ✅
        )

        # ── 4. Enviar respuesta por WhatsApp ───────────────
        wa = WhatsAppService(
            phone_number_id=tenant["wa_phone_id"],
            access_token=tenant["wa_access_token"],
        )
        await wa.send_text(to=sender_wa_id, message=response_text)
        await wa.mark_as_read(message_id)

        # ── 5. Persistir conversación ──────────────────────
        self._save_messages(db, tenant_id, sender_wa_id, message_text, response_text)
        logger.info(f"[{tenant['nombre']}] Respuesta enviada a {sender_wa_id}")

    def _get_history(self, db, tenant_id: str, wa_id: str) -> list[dict]:
        """
        Recupera el historial reciente de chat_sessions para contexto de IA.
        Retorna lista OpenAI-compatible: [{"role": "user/assistant", "content": "..."}]
        """
        try:
            result = (
                db.table("chat_sessions")
                .select("role, content")
                .eq("tenant_id", tenant_id)
                .eq("wa_id", wa_id)
                .order("created_at", desc=False)
                .limit(10)
                .execute()
            )
            return result.data or []
        except Exception as e:
            logger.error(f"Error cargando historial: {e}")
            return []

    def _save_messages(self, db, tenant_id: str, wa_id: str, user_msg: str, bot_msg: str):
        """
        Guarda el par de mensajes (user + assistant) en chat_sessions.
        """
        try:
            db.table("chat_sessions").insert([
                {"tenant_id": tenant_id, "wa_id": wa_id, "role": "user",      "content": user_msg},
                {"tenant_id": tenant_id, "wa_id": wa_id, "role": "assistant", "content": bot_msg},
            ]).execute()
        except Exception as e:
            logger.error(f"Error guardando mensajes: {e}")
