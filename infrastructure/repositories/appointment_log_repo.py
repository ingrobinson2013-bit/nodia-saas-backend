# infrastructure/repositories/appointment_log_repo.py
# Acceso a datos de citas_log — PostgreSQL Nativo & Clean Architecture

from typing import List, Optional, Dict, Any
import logging
from infrastructure.database import fetch_one, fetch_all, execute_sql, get_supabase

logger = logging.getLogger(__name__)


class AppointmentLogRepository:
    """
    Repositorio para la tabla public.citas_log.
    Schema:
        id, tenant_id, wa_from, cliente_nombre, servicio, profesional,
        fecha_cita, hora_cita, fecha_hora_inicio, fecha_hora_fin,
        odoo_event_id, origen, estado, created_at
    """

    def get_active_appointments(self, tenant_id: str, wa_from: str) -> List[dict]:
        sql = """
        SELECT id FROM citas_log 
        WHERE tenant_id = %s 
          AND wa_from = %s 
          AND estado IN ('confirmada', 'creada', 'reagendada');
        """
        rows = fetch_all(sql, (tenant_id, wa_from))
        if rows:
            return rows
        
        db = get_supabase()
        if db:
            try:
                res = db.table("citas_log").select("id").eq("tenant_id", tenant_id).eq("wa_from", wa_from).in_("estado", ["confirmada", "creada", "reagendada"]).execute()
                return res.data or []
            except Exception as e:
                logger.error(f"Error en get_active_appointments fallback: {e}")
        return []

    def update_status_by_ids(self, ids: List[Any], estado: str) -> bool:
        if not ids:
            return False
        sql = "UPDATE citas_log SET estado = %s WHERE id = ANY(%s);"
        ok = execute_sql(sql, (estado, ids))
        if not ok:
            db = get_supabase()
            if db:
                try:
                    db.table("citas_log").update({"estado": estado}).in_("id", ids).execute()
                    return True
                except Exception as e:
                    logger.error(f"Error en update_status_by_ids fallback: {e}")
        return ok

    def insert_log(self, log_entry: dict) -> Optional[dict]:
        if not log_entry:
            return None
        cols = list(log_entry.keys())
        values = list(log_entry.values())
        placeholders = [f"%s" for _ in cols]
        sql = f"INSERT INTO citas_log ({', '.join(cols)}) VALUES ({', '.join(placeholders)}) RETURNING *;"
        res = fetch_one(sql, tuple(values))
        if res:
            return res
        
        db = get_supabase()
        if db:
            try:
                r = db.table("citas_log").insert(log_entry).execute()
                return r.data[0] if r.data else None
            except Exception as e:
                logger.error(f"Error en insert_log fallback: {e}")
        return None

    def update_status_by_id(self, cita_id: Any, estado: str) -> bool:
        sql = "UPDATE citas_log SET estado = %s WHERE id = %s;"
        ok = execute_sql(sql, (estado, cita_id))
        if not ok:
            db = get_supabase()
            if db:
                try:
                    db.table("citas_log").update({"estado": estado}).eq("id", cita_id).execute()
                    return True
                except Exception as e:
                    logger.error(f"Error en update_status_by_id fallback: {e}")
        return ok

    def cancel_appointment_by_odoo_id(self, tenant_id: str, odoo_event_id: int) -> bool:
        sql = "UPDATE citas_log SET estado = 'cancelada' WHERE tenant_id = %s AND odoo_event_id = %s;"
        ok = execute_sql(sql, (tenant_id, str(odoo_event_id)))
        if not ok:
            db = get_supabase()
            if db:
                try:
                    db.table("citas_log").update({"estado": "cancelada"}).eq("tenant_id", tenant_id).eq("odoo_event_id", odoo_event_id).execute()
                    return True
                except Exception as e:
                    logger.error(f"Error en cancel_appointment_by_odoo_id fallback: {e}")
        return ok

    def cancel_all_appointments_by_phone(self, tenant_id: str, wa_from: str) -> bool:
        sql = "UPDATE citas_log SET estado = 'cancelada' WHERE tenant_id = %s AND wa_from = %s;"
        ok = execute_sql(sql, (tenant_id, wa_from))
        if not ok:
            db = get_supabase()
            if db:
                try:
                    db.table("citas_log").update({"estado": "cancelada"}).eq("tenant_id", tenant_id).eq("wa_from", wa_from).execute()
                    return True
                except Exception as e:
                    logger.error(f"Error en cancel_all_appointments_by_phone fallback: {e}")
        return ok

    def get_future_appointments_by_client(self, tenant_id: str, wa_from: str, since_date: str, limit: int = 5) -> List[dict]:
        sql = """
        SELECT fecha_cita, hora_cita, servicio FROM citas_log 
        WHERE tenant_id = %s 
          AND wa_from = %s 
          AND estado != 'cancelada' 
          AND fecha_cita >= %s 
        ORDER BY fecha_cita ASC 
        LIMIT %s;
        """
        rows = fetch_all(sql, (tenant_id, wa_from, since_date, limit))
        if rows:
            return rows
        
        db = get_supabase()
        if db:
            try:
                res = db.table("citas_log").select("fecha_cita, hora_cita, servicio").eq("tenant_id", tenant_id).eq("wa_from", wa_from).neq("estado", "cancelada").gte("fecha_cita", since_date).order("fecha_cita", desc=False).limit(limit).execute()
                return res.data or []
            except Exception as e:
                logger.error(f"Error en get_future_appointments_by_client fallback: {e}")
        return []

    def get_future_appointments_by_tenant(self, tenant_id: str, since_date: str, limit: int = 30) -> List[dict]:
        sql = """
        SELECT fecha_cita, hora_cita FROM citas_log 
        WHERE tenant_id = %s 
          AND estado != 'cancelada' 
          AND fecha_cita >= %s 
        ORDER BY fecha_cita ASC, hora_cita ASC 
        LIMIT %s;
        """
        rows = fetch_all(sql, (tenant_id, since_date, limit))
        if rows:
            return rows
        
        db = get_supabase()
        if db:
            try:
                res = db.table("citas_log").select("fecha_cita, hora_cita").eq("tenant_id", tenant_id).neq("estado", "cancelada").gte("fecha_cita", since_date).order("fecha_cita", desc=False).order("hora_cita", desc=False).limit(limit).execute()
                return res.data or []
            except Exception as e:
                logger.error(f"Error en get_future_appointments_by_tenant fallback: {e}")
        return []

    def get_active_appointments_for_cancel(
        self,
        tenant_id: str,
        wa_from: str,
        date_str: Optional[str] = None,
        time_str: Optional[str] = None
    ) -> List[dict]:
        where_clauses = ["tenant_id = %s", "wa_from = %s", "estado NOT IN ('cancelada', 'reagendada')"]
        params = [tenant_id, wa_from]
        if date_str:
            where_clauses.append("fecha_cita = %s")
            params.append(date_str)
        if time_str:
            hora_norm = time_str[:5] + ":00" if len(time_str) == 5 else time_str
            where_clauses.append("hora_cita = %s")
            params.append(hora_norm)
            
        sql = f"SELECT id, odoo_event_id, fecha_cita, hora_cita FROM citas_log WHERE {' AND '.join(where_clauses)} ORDER BY fecha_cita ASC;"
        rows = fetch_all(sql, tuple(params))
        if rows:
            return rows
        
        db = get_supabase()
        if db:
            try:
                q = db.table("citas_log").select("id, odoo_event_id, fecha_cita, hora_cita").eq("tenant_id", tenant_id).eq("wa_from", wa_from).neq("estado", "cancelada").neq("estado", "reagendada")
                if date_str:
                    q = q.eq("fecha_cita", date_str)
                if time_str:
                    hora_norm = time_str[:5] + ":00" if len(time_str) == 5 else time_str
                    q = q.eq("hora_cita", hora_norm)
                res = q.order("fecha_cita", desc=False).execute()
                return res.data or []
            except Exception as e:
                logger.error(f"Error en get_active_appointments_for_cancel fallback: {e}")
        return []

    def get_active_future_appointments(self, tenant_id: str, wa_from: str, since_date: str) -> List[dict]:
        sql = """
        SELECT id, odoo_event_id, fecha_cita FROM citas_log 
        WHERE tenant_id = %s AND wa_from = %s AND estado = 'confirmada' AND fecha_cita >= %s;
        """
        rows = fetch_all(sql, (tenant_id, wa_from, since_date))
        if rows:
            return rows
        
        db = get_supabase()
        if db:
            try:
                res = db.table("citas_log").select("id, odoo_event_id, fecha_cita").eq("tenant_id", tenant_id).eq("wa_from", wa_from).eq("estado", "confirmada").gte("fecha_cita", since_date).execute()
                return res.data or []
            except Exception as e:
                logger.error(f"Error en get_active_future_appointments fallback: {e}")
        return []

    def get_appointment_by_date(self, tenant_id: str, wa_from: str, date_str: str) -> List[dict]:
        sql = "SELECT id, odoo_event_id FROM citas_log WHERE tenant_id = %s AND wa_from = %s AND fecha_cita = %s;"
        rows = fetch_all(sql, (tenant_id, wa_from, date_str))
        if rows:
            return rows
        
        db = get_supabase()
        if db:
            try:
                res = db.table("citas_log").select("id, odoo_event_id").eq("tenant_id", tenant_id).eq("wa_from", wa_from).eq("fecha_cita", date_str).execute()
                return res.data or []
            except Exception as e:
                logger.error(f"Error en get_appointment_by_date fallback: {e}")
        return []

    def reschedule_appointment_log(self, log_id: Any, new_date: str, new_time: str) -> bool:
        hora_norm = f"{new_time}:00" if len(new_time) == 5 else new_time
        sql = "UPDATE citas_log SET fecha_cita = %s, hora_cita = %s WHERE id = %s;"
        ok = execute_sql(sql, (new_date, hora_norm, log_id))
        if not ok:
            db = get_supabase()
            if db:
                try:
                    db.table("citas_log").update({"fecha_cita": new_date, "hora_cita": hora_norm}).eq("id", log_id).execute()
                    return True
                except Exception as e:
                    logger.error(f"Error en reschedule_appointment_log fallback: {e}")
        return ok

    def reschedule_by_odoo_id(self, tenant_id: str, odoo_event_id: int, new_date: str, new_time: str) -> bool:
        hora_norm = f"{new_time}:00" if len(new_time) == 5 else new_time
        sql = "UPDATE citas_log SET fecha_cita = %s, hora_cita = %s, estado = 'confirmada' WHERE tenant_id = %s AND odoo_event_id = %s;"
        ok = execute_sql(sql, (new_date, hora_norm, tenant_id, str(odoo_event_id)))
        if not ok:
            db = get_supabase()
            if db:
                try:
                    db.table("citas_log").update({"fecha_cita": new_date, "hora_cita": hora_norm, "estado": "confirmada"}).eq("tenant_id", tenant_id).eq("odoo_event_id", odoo_event_id).execute()
                    return True
                except Exception as e:
                    logger.error(f"Error en reschedule_by_odoo_id fallback: {e}")
        return ok

    def update_status_by_odoo_id(self, tenant_id: str, odoo_event_id: int, estado: str) -> bool:
        sql = "UPDATE citas_log SET estado = %s WHERE tenant_id = %s AND odoo_event_id = %s;"
        ok = execute_sql(sql, (estado, tenant_id, str(odoo_event_id)))
        if not ok:
            db = get_supabase()
            if db:
                try:
                    db.table("citas_log").update({"estado": estado}).eq("tenant_id", tenant_id).eq("odoo_event_id", odoo_event_id).execute()
                    return True
                except Exception as e:
                    logger.error(f"Error en update_status_by_odoo_id fallback: {e}")
        return ok

    def reschedule_by_phone(self, tenant_id: str, wa_from: str, new_date: str, new_time: str) -> bool:
        hora_norm = f"{new_time}:00" if len(new_time) == 5 else new_time
        sql = "UPDATE citas_log SET fecha_cita = %s, hora_cita = %s, estado = 'confirmada' WHERE tenant_id = %s AND wa_from = %s;"
        ok = execute_sql(sql, (new_date, hora_norm, tenant_id, wa_from))
        if not ok:
            db = get_supabase()
            if db:
                try:
                    db.table("citas_log").update({"fecha_cita": new_date, "hora_cita": hora_norm, "estado": "confirmada"}).eq("tenant_id", tenant_id).eq("wa_from", wa_from).execute()
                    return True
                except Exception as e:
                    logger.error(f"Error en reschedule_by_phone fallback: {e}")
        return ok







