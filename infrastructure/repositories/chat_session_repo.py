# infrastructure/repositories/chat_session_repo.py
# Acceso a datos de chat_sessions — 100% PostgreSQL Nativo & Clean Architecture

from typing import Optional, List, Dict, Any
from datetime import datetime, timezone
import json
import uuid
import logging
from infrastructure.database import fetch_one, fetch_all, execute_sql

logger = logging.getLogger(__name__)


class ChatSessionRepository:
    """
    Repositorio para la tabla public.chat_sessions.
    Schema:
        id, tenant_id, wa_from, history (JSONB []), estado,
        cita_odoo_id, updated_at, bot_mode, name, created_at
    """

    def get_by_id(self, session_id: str) -> Optional[dict]:
        sql = "SELECT * FROM chat_sessions WHERE id = %s LIMIT 1;"
        return fetch_one(sql, (session_id,))

    def get_by_tenant_and_phone(self, tenant_id: str, wa_from: str) -> Optional[dict]:
        sql = "SELECT * FROM chat_sessions WHERE tenant_id = %s AND wa_from = %s LIMIT 1;"
        return fetch_one(sql, (tenant_id, wa_from))

    def get_or_create(self, tenant_id: str, wa_from: str, name: Optional[str] = None) -> dict:
        existing = self.get_by_tenant_and_phone(tenant_id, wa_from)
        if existing:
            if not existing.get("name") and name:
                try:
                    self.update_name(existing["id"], name)
                    existing["name"] = name
                except Exception:
                    pass
            return existing

        session_id = str(uuid.uuid4())
        sql = """
        INSERT INTO chat_sessions (id, tenant_id, wa_from, history, bot_mode, estado, name, created_at, updated_at)
        VALUES (%s, %s, %s, %s::jsonb, %s, %s, %s, NOW(), NOW())
        RETURNING *;
        """
        res = fetch_one(sql, (session_id, tenant_id, wa_from, "[]", "auto", "activo", name))
        if res:
            return res
        
        return {"id": session_id, "history": [], "bot_mode": True}

    def update_history(self, session_id: str, new_history: List[dict]) -> bool:
        if not session_id:
            return False
        history_json = json.dumps(new_history, ensure_ascii=False)
        sql = "UPDATE chat_sessions SET history = %s::jsonb, updated_at = NOW() WHERE id = %s;"
        return execute_sql(sql, (history_json, session_id))

    def update_bot_mode(self, session_id: str, bot_mode: Any) -> bool:
        if not session_id:
            return False
        mode_str = "auto" if (bot_mode is True or bot_mode == "auto") else "manual"
        sql = "UPDATE chat_sessions SET bot_mode = %s, updated_at = NOW() WHERE id = %s;"
        return execute_sql(sql, (mode_str, session_id))

    def update_name(self, session_id: str, name: str) -> bool:
        if not session_id:
            return False
        sql = "UPDATE chat_sessions SET name = %s, updated_at = NOW() WHERE id = %s;"
        return execute_sql(sql, (name, session_id))

    def append_message_to_history(self, session_id: str, role: str, content: str) -> bool:
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
        sql = "SELECT wa_from, updated_at, history, name FROM chat_sessions WHERE tenant_id = %s AND wa_from = ANY(%s);"
        rows = fetch_all(sql, (tenant_id, phones))
        return rows or []

    def get_active_sessions_for_tenant(self, tenant_id: str, limit: int = 50) -> List[dict]:
        sql = """
        SELECT id, wa_from, name, estado, bot_mode, updated_at 
        FROM chat_sessions 
        WHERE tenant_id = %s 
        ORDER BY updated_at DESC 
        LIMIT %s;
        """
        rows = fetch_all(sql, (tenant_id, limit))
        return rows or []

    def update_session(self, session_id: str, updates: dict) -> bool:
        if not session_id or not updates:
            return False
        
        set_clauses = []
        values = []
        for k, v in updates.items():
            if k == "history":
                set_clauses.append("history = %s::jsonb")
                values.append(json.dumps(v, ensure_ascii=False))
            else:
                set_clauses.append(f"{k} = %s")
                values.append(v)
        
        set_clauses.append("updated_at = NOW()")
        values.append(session_id)
        
        sql = f"UPDATE chat_sessions SET {', '.join(set_clauses)} WHERE id = %s;"
        ok = execute_sql(sql, tuple(values))
        if not ok:
            db = get_supabase()
            if db:
                try:
                    if "updated_at" not in updates:
                        updates["updated_at"] = datetime.now(timezone.utc).isoformat()
                    db.table("chat_sessions").update(updates).eq("id", session_id).execute()
                    return True
                except Exception as e:
                    logger.error(f"Error actualizando sesión en fallback: {e}")
        return ok



