# infrastructure/repositories/tenant_repo.py
# Acceso a datos de tenants — PostgreSQL Nativo & Clean Architecture

from typing import Optional, List, Dict, Any
import logging
from infrastructure.database import fetch_one, fetch_all, execute_sql, get_supabase

logger = logging.getLogger(__name__)


class TenantRepository:
    """
    Repositorio para la tabla public.tenants.
    Schema:
        tenant_id, nombre, wa_phone_id, wa_access_token,
        odoo_url, odoo_db, odoo_user, odoo_api_key,
        activo, plan, created_at, ai_prompt, user_id,
        owner_phone, waba_id, notification_email
    """

    def get_by_phone_id(self, wa_phone_id: str) -> Optional[dict]:
        """
        Busca un tenant activo por su wa_phone_id.
        Este ID llega en cada webhook de Meta.
        """
        sql = "SELECT * FROM tenants WHERE wa_phone_id = %s AND activo = TRUE LIMIT 1;"
        res = fetch_one(sql, (wa_phone_id,))
        if res:
            return res
        
        # Fallback a Supabase si no se encuentra en local
        db = get_supabase()
        if db:
            try:
                r = db.table("tenants").select("*").eq("wa_phone_id", wa_phone_id).eq("activo", True).single().execute()
                return r.data if r.data else None
            except Exception:
                pass
        return None

    def get_by_id(self, tenant_id: str) -> Optional[dict]:
        sql = "SELECT * FROM tenants WHERE tenant_id = %s LIMIT 1;"
        res = fetch_one(sql, (tenant_id,))
        if res:
            return res
        
        # Fallback Supabase
        db = get_supabase()
        if db:
            try:
                r = db.table("tenants").select("*").eq("tenant_id", tenant_id).single().execute()
                return r.data if r.data else None
            except Exception:
                pass
        return None

    def get_all_active_with_odoo(self) -> list:
        """
        Devuelve tenants activos con Odoo configurado.
        Usado por el notification_job para el polling multi-tenant.
        """
        sql = """
        SELECT * FROM tenants 
        WHERE activo = TRUE 
          AND odoo_url IS NOT NULL 
          AND odoo_url != '' 
          AND odoo_api_key IS NOT NULL 
          AND odoo_api_key != '';
        """
        tenants = fetch_all(sql)
        if tenants:
            return tenants
        
        # Fallback Supabase
        db = get_supabase()
        if db:
            try:
                result = (
                    db.table("tenants")
                    .select("*")
                    .eq("activo", True)
                    .not_.is_("odoo_url", "null")
                    .not_.is_("odoo_api_key", "null")
                    .execute()
                )
                raw = result.data or []
                return [t for t in raw if t.get("odoo_url", "").strip()]
            except Exception:
                pass
        return []

    def update_waba_id(self, tenant_id: str, waba_id: str) -> bool:
        sql = "UPDATE tenants SET waba_id = %s WHERE tenant_id = %s;"
        ok = execute_sql(sql, (waba_id, tenant_id))
        if not ok:
            db = get_supabase()
            if db:
                try:
                    db.table("tenants").update({"waba_id": waba_id}).eq("tenant_id", tenant_id).execute()
                    return True
                except Exception as e:
                    logger.error(f"Error actualizando waba_id: {e}")
        return ok

    def update_tenant_credentials(self, tenant_id: str, credentials: dict) -> bool:
        if not credentials:
            return True
        set_clauses = [f"{k} = %s" for k in credentials.keys()]
        values = list(credentials.values()) + [tenant_id]
        sql = f"UPDATE tenants SET {', '.join(set_clauses)} WHERE tenant_id = %s;"
        ok = execute_sql(sql, tuple(values))
        if not ok:
            db = get_supabase()
            if db:
                try:
                    db.table("tenants").update(credentials).eq("tenant_id", tenant_id).execute()
                    return True
                except Exception as e:
                    logger.error(f"Error actualizando credenciales del tenant: {e}")
        return ok


def get_supabase_client():
    return get_supabase()


