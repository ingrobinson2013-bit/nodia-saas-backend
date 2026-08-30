# infrastructure/repositories/tenant_config_repo.py
# Acceso a datos de tenant_config — PostgreSQL Nativo & Clean Architecture

from typing import Optional
import json
import logging
from infrastructure.database import fetch_one, execute_sql, get_supabase

logger = logging.getLogger(__name__)


class TenantConfigRepository:
    """
    Repositorio para la tabla public.tenant_config.
    Schema:
        tenant_id, config (JSONB), updated_at
    """

    def get_by_tenant_id(self, tenant_id: str) -> Optional[dict]:
        sql = "SELECT * FROM tenant_config WHERE tenant_id = %s LIMIT 1;"
        res = fetch_one(sql, (tenant_id,))
        if res:
            return res
        
        db = get_supabase()
        if db:
            try:
                result = db.table("tenant_config").select("*").eq("tenant_id", tenant_id).single().execute()
                return result.data if result.data else None
            except Exception:
                pass
        return None

    def update_config(self, tenant_id: str, data: dict) -> bool:
        if not tenant_id:
            return False
        config_json = json.dumps(data, ensure_ascii=False)
        sql = """
        INSERT INTO tenant_config (tenant_id, config, updated_at)
        VALUES (%s, %s::jsonb, NOW())
        ON CONFLICT (tenant_id) DO UPDATE SET config = EXCLUDED.config, updated_at = NOW();
        """
        ok = execute_sql(sql, (tenant_id, config_json))
        if not ok:
            db = get_supabase()
            if db:
                try:
                    db.table("tenant_config").update(data).eq("tenant_id", tenant_id).execute()
                    return True
                except Exception as e:
                    logger.error(f"Error en update_config fallback: {e}")
        return ok

