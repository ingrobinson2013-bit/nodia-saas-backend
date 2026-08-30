# infrastructure/database.py
# Capa de Acceso a Base de Datos — PostgreSQL 17 Nativo con Connection Pooling

import logging
import os
from contextlib import contextmanager
from typing import Optional, Generator, Any, List, Dict
import psycopg2
from psycopg2.pool import ThreadedConnectionPool
from psycopg2.extras import RealDictCursor
from config import settings

logger = logging.getLogger(__name__)

# Connection Pool Singleton para PostgreSQL Local
_pg_pool: Optional[ThreadedConnectionPool] = None

def get_database_url() -> str:
    return (
        settings.DATABASE_URL
        or os.getenv("DATABASE_URL")
        or "postgresql://postgres:Ashley2023@nodia-saas_nodia-postgres:5432/nodia-saas"
    )

def init_postgres_pool(minconn: int = 2, maxconn: int = 20) -> ThreadedConnectionPool:
    global _pg_pool
    if _pg_pool is None:
        db_url = get_database_url()
        try:
            _pg_pool = ThreadedConnectionPool(minconn, maxconn, db_url)
            logger.info("✅ PostgreSQL Connection Pool inicializado con éxito.")
        except Exception as e:
            logger.error(f"❌ Error crítico inicializando PostgreSQL Pool: {e}")
            raise e
    return _pg_pool

@contextmanager
def get_db_cursor() -> Generator[Any, None, None]:
    """
    Context manager seguro para obtener un cursor PostgreSQL con RealDictCursor.
    Garantiza commit automático y retorno de conexión al pool.
    """
    pool = init_postgres_pool()
    conn = pool.getconn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            yield cur
        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.error(f"Error en consulta PostgreSQL: {e}")
        raise e
    finally:
        pool.putconn(conn)

def fetch_one(sql: str, params: tuple = ()) -> Optional[Dict[str, Any]]:
    try:
        with get_db_cursor() as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
            return dict(row) if row else None
    except Exception as e:
        logger.error(f"fetch_one error: {e} | SQL: {sql}")
        return None

def fetch_all(sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
    try:
        with get_db_cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
            return [dict(r) for r in rows]
    except Exception as e:
        logger.error(f"fetch_all error: {e} | SQL: {sql}")
        return []

def execute_sql(sql: str, params: tuple = ()) -> bool:
    try:
        with get_db_cursor() as cur:
            cur.execute(sql, params)
            return True
    except Exception as e:
        logger.error(f"execute_sql error: {e} | SQL: {sql}")
        return False


