# infrastructure/repositories/tenant_config_repo.py
# Acceso a datos de tenant_config — alineado con Clean Architecture y multi-tenant

from typing import Optional
import logging
from infrastructure.database import get_supabase

logger = logging.getLogger(__name__)


class TenantConfigRepository:
    """
    Repositorio para la tabla public.tenant_config.
    Schema esperado:
        tenant_id, ... (configuraciones específicas del bot del tenant)
    """

    def get_by_tenant_id(self, tenant_id: str) -> Optional[dict]:
        db = get_supabase()
        try:
            result = (
                db.table("tenant_config")
                .select("*")
                .eq("tenant_id", tenant_id)
                .single()
                .execute()
            )
            return result.data if result.data else None
        except Exception:
            # Silenciar si no existe la configuración, ya que es opcional
            return None

    def update_config(self, tenant_id: str, data: dict) -> bool:
        if not tenant_id:
            return False
        db = get_supabase()
        try:
            db.table("tenant_config").update(data).eq("tenant_id", tenant_id).execute()
            return True
        except Exception as e:
            logger.error(f"Error en TenantConfigRepository.update_config: {e}")
            return False
