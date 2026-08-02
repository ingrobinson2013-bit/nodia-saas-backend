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
        activo, plan, created_at,
        notificaciones_citas (bool, default=True)
            — False en tenants de ventas/leads que no deben
              recibir notificaciones automáticas de citas Odoo.
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

    def get_all_active_with_odoo(self) -> list:
        """
        Devuelve tenants activos con Odoo configurado Y notificaciones_citas habilitadas.
        Excluye tenants de tipo ventas/leads (notificaciones_citas=False).
        Usado por el notification_job para el polling multi-tenant.
        """
        db = get_supabase()
        result = (
            db.table("tenants")
            .select("*")
            .eq("activo", True)
            .not_.is_("odoo_url", "null")
            .not_.is_("odoo_api_key", "null")
            .execute()
        )
        tenants = result.data or []
        # Excluir tenants con odoo_url vacío (columna NOT NULL pero seteada a "")
        return [t for t in tenants if t.get("notificaciones_citas", True) is not False and t.get("odoo_url", "").strip()]
