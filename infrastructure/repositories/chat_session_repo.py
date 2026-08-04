# infrastructure/repositories/chat_session_repo.py
# Acceso a datos de chat_sessions — alineado con Clean Architecture y multi-tenant

from typing import Optional, List
from datetime import datetime, timezone
import logging
from infrastructure.database import get_supabase

logger = logging.getLogger(__name__)


class ChatSessionRepository:
    """
    Repositorio para la tabla public.chat_sessions.
    Schema:
        id, tenant_id, wa_from, history (JSONB []), estado,
        cita_odoo_id, updated_at, bot_mode, name
    """

    def get_by_id(self, session_id: str) -> Optional[dict]:
        db = get_supabase()
        try:
            result = (
                db.table("chat_sessions")
                .select("*")
                .eq("id", session_id)
                .single()
                .execute()
            )
            return result.data if result.data else None
        except Exception as e:
            logger.error(f"Error en ChatSessionRepository.get_by_id: {e}")
            return None

    def get_by_tenant_and_phone(self, tenant_id: str, wa_from: str) -> Optional[dict]:
        db = get_supabase()
        try:
            result = (
                db.table("chat_sessions")
                .select("*")
                .eq("tenant_id", tenant_id)
                .eq("wa_from", wa_from)
                .single()
                .execute()
            )
            return result.data if result.data else None
        except Exception:
            # Silenciar error en caso de que no exista la sesión (comportamiento esperado)
            return None

    def get_or_create(self, tenant_id: str, wa_from: str, name: Optional[str] = None) -> dict:
        db = get_supabase()
        # Intentar obtener la sesión existente
        existing = self.get_by_tenant_and_phone(tenant_id, wa_from)
        if existing:
            if not existing.get("name") and name:
                try:
                    self.update_name(existing["id"], name)
                    existing["name"] = name
                except Exception as ue:
                    logger.warning(f"Error actualizando nombre en sesión existente: {ue}")
            return existing

        # Si no existe, crearla
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
            return result.data[0] if result.data else {"id": None, "history": [], "bot_mode": True}
        except Exception as e:
            logger.error(f"Error creando sesión: {e}")
            return {"id": None, "history": [], "bot_mode": True}

    def update_history(self, session_id: str, new_history: List[dict]) -> bool:
        if not session_id:
            return False
        db = get_supabase()
        try:
            db.table("chat_sessions").update({
                "history":    new_history,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }).eq("id", session_id).execute()
            return True
        except Exception as e:
            logger.error(f"Error actualizando historial en ChatSessionRepository: {e}")
            return False

    def update_bot_mode(self, session_id: str, bot_mode: bool) -> bool:
        if not session_id:
            return False
        db = get_supabase()
        try:
            db.table("chat_sessions").update({
                "bot_mode": bot_mode,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }).eq("id", session_id).execute()
            return True
        except Exception as e:
            logger.error(f"Error actualizando bot_mode en ChatSessionRepository: {e}")
            return False

    def update_name(self, session_id: str, name: str) -> bool:
        if not session_id:
            return False
        db = get_supabase()
        try:
            db.table("chat_sessions").update({
                "name": name,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }).eq("id", session_id).execute()
            return True
        except Exception as e:
            logger.error(f"Error actualizando nombre en ChatSessionRepository: {e}")
            return False

    def append_message_to_history(self, session_id: str, role: str, content: str) -> bool:
        """
        Método helper seguro que recupera, añade al historial y actualiza de manera atómica.
        """
        session = self.get_by_id(session_id)
        if not session:
            return False
        
        history = session.get("history") or []
        history.append({
            "role": role,
            "content": content,
            "timestamp": datetime.now(timezone.utc).isoformat()
        })
        return self.update_history(session_id, history)

    def get_sessions_by_phones(self, tenant_id: str, phones: List[str]) -> List[dict]:
        if not phones:
            return []
        db = get_supabase()
        try:
            res = (
                db.table("chat_sessions")
                .select("wa_from, updated_at, history, name")
                .eq("tenant_id", tenant_id)
                .in_("wa_from", phones)
                .execute()
            )
            return res.data or []
        except Exception as e:
            logger.error(f"Error en ChatSessionRepository.get_sessions_by_phones: {e}")
            return []

    def update_session(self, session_id: str, updates: dict) -> bool:
        if not session_id:
            return False
        db = get_supabase()
        try:
            if "updated_at" not in updates:
                updates["updated_at"] = datetime.now(timezone.utc).isoformat()
            db.table("chat_sessions").update(updates).eq("id", session_id).execute()
            return True
        except Exception as e:
            logger.error(f"Error en ChatSessionRepository.update_session: {e}")
            return False


