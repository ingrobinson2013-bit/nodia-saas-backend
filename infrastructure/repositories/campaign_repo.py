# infrastructure/repositories/campaign_repo.py
# Acceso a datos de campaigns — PostgreSQL Nativo & Clean Architecture

from typing import List, Optional
from datetime import datetime, timezone
import logging
from infrastructure.database import fetch_one, fetch_all, execute_sql, get_supabase

logger = logging.getLogger(__name__)


class CampaignRepository:
    """
    Repositorio para la tabla public.campaigns.
    Schema:
        id, tenant_id, name, total_contacts, sent_count, failed_count, message_type, message, created_at
    """

    def insert_campaign(
        self,
        tenant_id: str,
        name: str,
        total_contacts: int,
        sent_count: int,
        failed_count: int,
        message_type: str,
        message: str
    ) -> Optional[dict]:
        sql = """
        INSERT INTO campaigns (tenant_id, name, total_contacts, sent_count, failed_count, message_type, message, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
        RETURNING *;
        """
        res = fetch_one(sql, (tenant_id, name, total_contacts, sent_count, failed_count, message_type, message))
        if res:
            return res
        
        db = get_supabase()
        if db:
            try:
                result = db.table("campaigns").insert({
                    "tenant_id": tenant_id,
                    "name": name,
                    "total_contacts": total_contacts,
                    "sent_count": sent_count,
                    "failed_count": failed_count,
                    "message_type": message_type,
                    "message": message,
                    "created_at": datetime.now(timezone.utc).isoformat()
                }).execute()
                return result.data[0] if result.data else None
            except Exception as e:
                logger.error(f"Error en insert_campaign fallback: {e}")
        return None

    def list_by_tenant(self, tenant_id: str) -> List[dict]:
        sql = "SELECT * FROM campaigns WHERE tenant_id = %s ORDER BY created_at DESC;"
        rows = fetch_all(sql, (tenant_id,))
        if rows:
            return rows
        
        db = get_supabase()
        if db:
            try:
                res = db.table("campaigns").select("*").eq("tenant_id", tenant_id).order("created_at", desc=True).execute()
                return res.data or []
            except Exception as e:
                logger.error(f"Error en list_by_tenant fallback: {e}")
        return []

