# infrastructure/repositories/tenant_repo.py
# Acceso a datos de tenants — alineado con schema real de Supabase

from typing import Optional
from infrastructure.database import get_supabase


class TenantRepository:
    """
    Repositorio para la tabla public.tenants.
    Schema:
        tenant_id, nombre, wa_phone_id, wa_access_token,
        odoo_url, odoo_db, odoo_user, odoo_api_key,
        activo, plan, created_at
    """

    def get_by_phone_id(self, wa_phone_id: str) -> Optional[dict]:
        """
        Busca un tenant activo por su wa_phone_id.
        Este ID llega en cada webhook de Meta.
        """
        db = get_supabase()
        result = (
            db.table("tenants")
            .select("*")
            .eq("wa_phone_id", wa_phone_id)
            .eq("activo", True)
            .single()
            .execute()
        )
        return result.data if result.data else None

    def get_by_id(self, tenant_id: str) -> Optional[dict]:
        db = get_supabase()
        result = (
            db.table("tenants")
            .select("*")
            .eq("tenant_id", tenant_id)
            .single()
            .execute()
        )
        return result.data if result.data else None
