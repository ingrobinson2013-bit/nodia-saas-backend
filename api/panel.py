# api/panel.py
# Endpoints REST para el Panel Web SaaS (Next.js) — 100% PostgreSQL Nativo

import logging
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, HTTPException, Query, Body
from pydantic import BaseModel

from infrastructure.database import fetch_all, fetch_one, execute_sql
from infrastructure.repositories.tenant_repo import TenantRepository
from infrastructure.repositories.tenant_config_repo import TenantConfigRepository
from infrastructure.repositories.chat_session_repo import ChatSessionRepository
from infrastructure.repositories.appointment_log_repo import AppointmentLogRepository
from infrastructure.repositories.client_memory_repo import ClientMemoryRepository

router = APIRouter(prefix="/panel", tags=["Panel SaaS Web"])
logger = logging.getLogger(__name__)

tenant_repo = TenantRepository()
tenant_config_repo = TenantConfigRepository()
chat_session_repo = ChatSessionRepository()
appointment_log_repo = AppointmentLogRepository()
client_memory_repo = ClientMemoryRepository()


# ── Schemas ────────────────────────────────────────────────────────────
class BotModeRequest(BaseModel):
    bot_mode: str  # 'auto' o 'manual'


class UpdateTenantConfigRequest(BaseModel):
    nombre: Optional[str] = None
    ai_prompt: Optional[str] = None
    odoo_url: Optional[str] = None
    odoo_db: Optional[str] = None
    odoo_user: Optional[str] = None
    odoo_api_key: Optional[str] = None
    config: Optional[Dict[str, Any]] = None


# ── 1. Tenants ─────────────────────────────────────────────────────────
@router.get("/tenants")
async def list_tenants():
    """Retorna todos los tenants registrados en PostgreSQL."""
    sql = "SELECT tenant_id, nombre, wa_phone_id, created_at, activo FROM tenants ORDER BY created_at ASC;"
    rows = fetch_all(sql)
    return {"success": True, "tenants": rows or []}


@router.get("/tenant/{tenant_id}")
async def get_tenant(tenant_id: str):
    """Retorna los datos de un tenant y su configuración."""
    tenant = tenant_repo.get_by_id(tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant no encontrado")
    
    config = tenant_config_repo.get_by_tenant_id(tenant_id) or {}
    return {
        "success": True,
        "tenant": tenant,
        "config": config
    }


@router.put("/tenant/{tenant_id}/config")
async def update_tenant_config(tenant_id: str, req: UpdateTenantConfigRequest):
    """Actualiza la configuración y prompt del tenant."""
    tenant = tenant_repo.get_by_id(tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant no encontrado")

    # Actualizar campos del tenant
    updates = []
    params = []
    if req.nombre is not None:
        updates.append("nombre = %s")
        params.append(req.nombre)
    if req.ai_prompt is not None:
        updates.append("ai_prompt = %s")
        params.append(req.ai_prompt)
    if req.odoo_url is not None:
        updates.append("odoo_url = %s")
        params.append(req.odoo_url)
    if req.odoo_db is not None:
        updates.append("odoo_db = %s")
        params.append(req.odoo_db)
    if req.odoo_user is not None:
        updates.append("odoo_user = %s")
        params.append(req.odoo_user)
    if req.odoo_api_key is not None:
        updates.append("odoo_api_key = %s")
        params.append(req.odoo_api_key)

    if updates:
        params.append(tenant_id)
        sql = f"UPDATE tenants SET {', '.join(updates)} WHERE tenant_id = %s;"
        execute_sql(sql, tuple(params))

    # Actualizar config JSON
    if req.config is not None:
        tenant_config_repo.update_config(tenant_id, req.config)

    return {"success": True, "message": "Configuración actualizada correctamente"}


# ── 2. Chat Sessions (Inbox) ───────────────────────────────────────────
@router.get("/sessions")
async def list_sessions(tenant_id: str = Query(...), limit: int = 50):
    """Lista las conversaciones activas del tenant ordenadas por fecha reciente."""
    sql = """
    SELECT id, tenant_id, wa_from, name, bot_mode, estado, updated_at,
           jsonb_array_length(history) as total_messages,
           history->-1 as last_message
    FROM chat_sessions
    WHERE tenant_id = %s
    ORDER BY updated_at DESC
    LIMIT %s;
    """
    rows = fetch_all(sql, (tenant_id, limit))
    return {"success": True, "sessions": rows or []}


@router.get("/sessions/{session_id}")
async def get_session_detail(session_id: str):
    """Retorna el historial completo de una sesión de chat."""
    session = chat_session_repo.get_by_id(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Sesión no encontrada")
    return {"success": True, "session": session}


@router.post("/sessions/{session_id}/bot-mode")
async def toggle_bot_mode(session_id: str, req: BotModeRequest):
    """Cambia el modo del bot (auto / manual) para una conversación específica."""
    ok = chat_session_repo.update_bot_mode(session_id, req.bot_mode)
    if not ok:
        raise HTTPException(status_code=500, detail="Error actualizando modo del bot")
    return {"success": True, "bot_mode": req.bot_mode}


# ── 3. Citas (Appointments Log) ────────────────────────────────────────
@router.get("/appointments")
async def list_appointments(tenant_id: str = Query(...), limit: int = 50):
    """Lista las citas agendadas y su estado en PostgreSQL."""
    sql = """
    SELECT id, tenant_id, wa_from, cliente_nombre, servicio, profesional,
           fecha_cita, hora_cita, odoo_event_id, estado, origen, created_at
    FROM citas_log
    WHERE tenant_id = %s
    ORDER BY fecha_cita DESC, hora_cita DESC
    LIMIT %s;
    """
    rows = fetch_all(sql, (tenant_id, limit))
    return {"success": True, "appointments": rows or []}


# ── 4. Memoria Híbrida de Clientes (Client Memory) ─────────────────────
@router.get("/memory")
async def list_client_memory(tenant_id: str = Query(...), limit: int = 50):
    """Lista las preferencias aprendidas de los clientes."""
    sql = """
    SELECT id, tenant_id, wa_from, nombre_cliente, profesional_favorito,
           servicios_frecuentes, dias_preferidos, horario_habitual,
           total_citas_agendadas, ultima_cita_fecha, notas_estilo, updated_at
    FROM client_memory
    WHERE tenant_id = %s
    ORDER BY total_citas_agendadas DESC, updated_at DESC
    LIMIT %s;
    """
    rows = fetch_all(sql, (tenant_id, limit))
    return {"success": True, "clients": rows or []}


# ── 5. Auditoría de Intenciones Fast-Path (AI Intent Logs) ─────────────
@router.get("/intent-logs")
async def list_intent_logs(tenant_id: Optional[str] = None, limit: int = 50):
    """Lista los logs de auditoría de inferencia y latencia en tiempo real."""
    if tenant_id:
        sql = """
        SELECT id, tenant_id, wa_from, mensaje_cliente, intencion_predicha,
               confianza, motor_usado, latencia_ms, timestamp
        FROM ai_intent_logs
        WHERE tenant_id = %s
        ORDER BY id DESC
        LIMIT %s;
        """
        rows = fetch_all(sql, (tenant_id, limit))
    else:
        sql = """
        SELECT id, tenant_id, wa_from, mensaje_cliente, intencion_predicha,
               confianza, motor_usado, latencia_ms, timestamp
        FROM ai_intent_logs
        ORDER BY id DESC
        LIMIT %s;
        """
        rows = fetch_all(sql, (limit,))
    return {"success": True, "logs": rows or []}


# ── 6. Telemetría y ROI Global / Por Tenant (Exclusivo Admin) ──────────
@router.get("/telemetry")
async def get_telemetry_stats(tenant_id: Optional[str] = None):
    """Retorna métricas agregadas de rendimiento de IA, Fast-Path, ahorro en USD y memoria."""
    filter_sql = "WHERE tenant_id = %s" if tenant_id else ""
    params = (tenant_id,) if tenant_id else ()

    # 1. Conteo y métricas de intención
    sql_totals = f"""
    SELECT
        COUNT(*) as total_logs,
        COUNT(*) FILTER (WHERE motor_usado LIKE 'fastpath%%') as fastpath_count,
        COUNT(*) FILTER (WHERE motor_usado NOT LIKE 'fastpath%%') as llm_count,
        COALESCE(AVG(latencia_ms) FILTER (WHERE motor_usado LIKE 'fastpath%%'), 2.4) as avg_fastpath_ms,
        COALESCE(AVG(latencia_ms) FILTER (WHERE motor_usado NOT LIKE 'fastpath%%'), 820.0) as avg_llm_ms
    FROM ai_intent_logs
    {filter_sql};
    """
    totals = fetch_one(sql_totals, params) or {}
    total_logs = totals.get("total_logs", 0) or 0
    fastpath_count = totals.get("fastpath_count", 0) or 0
    llm_count = totals.get("llm_count", 0) or 0
    avg_fastpath_ms = round(float(totals.get("avg_fastpath_ms", 2.4)), 1)
    avg_llm_ms = round(float(totals.get("avg_llm_ms", 820.0)), 1)
    
    fastpath_ratio = round((fastpath_count / total_logs * 100), 1) if total_logs > 0 else 0.0
    cost_saved_usd = round(fastpath_count * 0.00025, 4)  # Estimación basada en tokens ahorrados

    # 2. Desglose por Intención
    sql_breakdown = f"""
    SELECT intencion_predicha, COUNT(*) as count
    FROM ai_intent_logs
    {filter_sql}
    GROUP BY intencion_predicha
    ORDER BY count DESC;
    """
    breakdown_rows = fetch_all(sql_breakdown, params) or []
    breakdown = {r["intencion_predicha"]: r["count"] for r in breakdown_rows}

    # 3. Clientes con Memoria Híbrida
    sql_memory_count = f"SELECT COUNT(*) as count FROM client_memory {filter_sql};"
    mem_total = fetch_one(sql_memory_count, params)
    total_vip_clients = mem_total.get("count", 0) if mem_total else 0

    # 4. Últimos 25 logs
    sql_recent_logs = f"""
    SELECT id, tenant_id, wa_from, mensaje_cliente, intencion_predicha,
           confianza, motor_usado, latencia_ms, timestamp
    FROM ai_intent_logs
    {filter_sql}
    ORDER BY id DESC
    LIMIT 25;
    """
    recent_logs = fetch_all(sql_recent_logs, params) or []

    # 5. Top clientes en memoria
    sql_top_memory = f"""
    SELECT id, tenant_id, wa_from, nombre_cliente, profesional_favorito,
           servicios_frecuentes, dias_preferidos, horario_habitual,
           total_citas_agendadas, ultima_cita_fecha, updated_at
    FROM client_memory
    {filter_sql}
    ORDER BY total_citas_agendadas DESC, updated_at DESC
    LIMIT 10;
    """
    top_memory = fetch_all(sql_top_memory, params) or []

    return {
        "success": True,
        "metrics": {
            "total_interactions": total_logs,
            "fastpath_count": fastpath_count,
            "llm_count": llm_count,
            "fastpath_ratio": fastpath_ratio,
            "avg_fastpath_ms": avg_fastpath_ms,
            "avg_llm_ms": avg_llm_ms,
            "cost_saved_usd": cost_saved_usd,
            "total_vip_clients": total_vip_clients,
            "breakdown": breakdown
        },
        "recent_logs": recent_logs,
        "top_memory": top_memory
    }
