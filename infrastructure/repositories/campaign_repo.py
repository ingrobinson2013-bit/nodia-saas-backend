# infrastructure/repositories/campaign_repo.py
# Acceso a datos de campaigns — alineado con Clean Architecture y multi-tenant

from typing import List, Optional
from datetime import datetime, timezone
import logging
from infrastructure.database import get_supabase

logger = logging.getLogger(__name__)


class CampaignRepository:
    """
    Repositorio para la tabla public.campaigns.
    Schema esperado:
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
        db = get_supabase()
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
            logger.error(f"Error en CampaignRepository.insert_campaign: {e}")
            raise e

    def list_by_tenant(self, tenant_id: str) -> List[dict]:
        db = get_supabase()
        try:
            res = (
                db.table("campaigns")
                .select("*")
                .eq("tenant_id", tenant_id)
                .order("created_at", desc=True)
                .execute()
            )
            return res.data or []
        except Exception as e:
            logger.error(f"Error en CampaignRepository.list_by_tenant: {e}")
            return []
