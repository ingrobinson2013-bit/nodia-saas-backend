# infrastructure/database.py
# Conexión a Supabase usando el service role (backend only)

from supabase import create_client, Client
from functools import lru_cache
from config import settings


@lru_cache(maxsize=1)
def get_supabase() -> Client:
    """
    Cliente Supabase singleton.
    Usa service_role key — NUNCA exponer en frontend.
    """
    return create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)
