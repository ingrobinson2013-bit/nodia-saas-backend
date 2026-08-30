# infrastructure/repositories/tenant_config_repo.py
# Acceso a datos de tenant_config — 100% PostgreSQL Nativo & Clean Architecture

from typing import Optional, Dict, Any
import json
import logging
from infrastructure.database import fetch_one, execute_sql

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
            config_data = res.get("config")
            if isinstance(config_data, str):
                try: config_data = json.loads(config_data)
                except Exception: pass
            if isinstance(config_data, dict):
                return config_data
            return res
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
        return execute_sql(sql, (tenant_id, config_json))


