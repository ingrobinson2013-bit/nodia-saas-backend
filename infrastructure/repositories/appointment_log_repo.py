# infrastructure/repositories/appointment_log_repo.py
# Acceso a datos de citas_log — alineado con Clean Architecture y multi-tenant

from typing import List, Optional
import logging
from infrastructure.database import get_supabase

logger = logging.getLogger(__name__)


class AppointmentLogRepository:
    """
    Repositorio para la tabla public.citas_log.
    Schema:
        id, tenant_id, wa_from, odoo_event_id, estado, ...
    """

    def get_active_appointments(self, tenant_id: str, wa_from: str) -> List[dict]:
        db = get_supabase()
        try:
            result = (
                db.table("citas_log")
                .select("id")
                .eq("tenant_id", tenant_id)
                .eq("wa_from", wa_from)
                .in_("estado", ["confirmada", "creada", "reagendada"])
                .execute()
            )
            return result.data or []
        except Exception as e:
            logger.error(f"Error en AppointmentLogRepository.get_active_appointments: {e}")
            return []

    def update_status_by_ids(self, ids: List[str], estado: str) -> bool:
        if not ids:
            return False
        db = get_supabase()
        try:
            db.table("citas_log").update({"estado": estado}).in_("id", ids).execute()
            return True
        except Exception as e:
            logger.error(f"Error en AppointmentLogRepository.update_status_by_ids: {e}")
            return False

    def insert_log(self, log_entry: dict) -> Optional[dict]:
        db = get_supabase()
        try:
            result = db.table("citas_log").insert(log_entry).execute()
            return result.data[0] if result.data else None
        except Exception as e:
            logger.error(f"Error en AppointmentLogRepository.insert_log: {e}")
            return None

    def update_status_by_id(self, cita_id: str, estado: str) -> bool:
        db = get_supabase()
        try:
            db.table("citas_log").update({"estado": estado}).eq("id", cita_id).execute()
            return True
        except Exception as e:
            logger.error(f"Error en AppointmentLogRepository.update_status_by_id: {e}")
            return False

    def cancel_appointment_by_odoo_id(self, tenant_id: str, odoo_event_id: int) -> bool:
        db = get_supabase()
        try:
            db.table("citas_log").update({"estado": "cancelada"}).eq("tenant_id", tenant_id).eq("odoo_event_id", odoo_event_id).execute()
            return True
        except Exception as e:
            logger.error(f"Error en AppointmentLogRepository.cancel_appointment_by_odoo_id: {e}")
            return False

    def cancel_all_appointments_by_phone(self, tenant_id: str, wa_from: str) -> bool:
        db = get_supabase()
        try:
            db.table("citas_log").update({"estado": "cancelada"}).eq("tenant_id", tenant_id).eq("wa_from", wa_from).execute()
            return True
        except Exception as e:
            logger.error(f"Error en AppointmentLogRepository.cancel_all_appointments_by_phone: {e}")
            return False

    def get_future_appointments_by_client(self, tenant_id: str, wa_from: str, since_date: str, limit: int = 5) -> List[dict]:
        db = get_supabase()
        try:
            res = (
                db.table("citas_log")
                .select("fecha_cita, hora_cita, servicio")
                .eq("tenant_id", tenant_id)
                .eq("wa_from", wa_from)
                .neq("estado", "cancelada")
                .gte("fecha_cita", since_date)
                .order("fecha_cita", desc=False)
                .limit(limit)
                .execute()
            )
            return res.data or []
        except Exception as e:
            logger.error(f"Error en AppointmentLogRepository.get_future_appointments_by_client: {e}")
            return []

    def get_future_appointments_by_tenant(self, tenant_id: str, since_date: str, limit: int = 30) -> List[dict]:
        db = get_supabase()
        try:
            res = (
                db.table("citas_log")
                .select("fecha_cita, hora_cita")
                .eq("tenant_id", tenant_id)
                .neq("estado", "cancelada")
                .gte("fecha_cita", since_date)
                .order("fecha_cita", desc=False)
                .order("hora_cita", desc=False)
                .limit(limit)
                .execute()
            )
            return res.data or []
        except Exception as e:
            logger.error(f"Error en AppointmentLogRepository.get_future_appointments_by_tenant: {e}")
            return []

    def get_active_appointments_for_cancel(
        self,
        tenant_id: str,
        wa_from: str,
        date_str: Optional[str] = None,
        time_str: Optional[str] = None
    ) -> List[dict]:
        db = get_supabase()
        try:
            q = (
                db.table("citas_log")
                .select("id, odoo_event_id, fecha_cita, hora_cita")
                .eq("tenant_id", tenant_id)
                .eq("wa_from", wa_from)
                .neq("estado", "cancelada")
                .neq("estado", "reagendada")
            )
            if date_str:
                q = q.eq("fecha_cita", date_str)
            if time_str:
                hora_norm = time_str[:5] + ":00" if len(time_str) == 5 else time_str
                q = q.eq("hora_cita", hora_norm)
            
            res = q.order("fecha_cita", desc=False).execute()
            return res.data or []
        except Exception as e:
            logger.error(f"Error en AppointmentLogRepository.get_active_appointments_for_cancel: {e}")
            return []

    def get_active_future_appointments(self, tenant_id: str, wa_from: str, since_date: str) -> List[dict]:
        db = get_supabase()
        try:
            result = (
                db.table("citas_log")
                .select("id, odoo_event_id, fecha_cita")
                .eq("tenant_id", tenant_id)
                .eq("wa_from", wa_from)
                .eq("estado", "confirmada")
                .gte("fecha_cita", since_date)
                .execute()
            )
            return result.data or []
        except Exception as e:
            logger.error(f"Error en AppointmentLogRepository.get_active_future_appointments: {e}")
            return []

    def get_appointment_by_date(self, tenant_id: str, wa_from: str, date_str: str) -> List[dict]:
        db = get_supabase()
        try:
            result = (
                db.table("citas_log")
                .select("id, odoo_event_id")
                .eq("tenant_id", tenant_id)
                .eq("wa_from", wa_from)
                .eq("fecha_cita", date_str)
                .execute()
            )
            return result.data or []
        except Exception as e:
            logger.error(f"Error en AppointmentLogRepository.get_appointment_by_date: {e}")
            return []

    def reschedule_appointment_log(self, log_id: str, new_date: str, new_time: str) -> bool:
        db = get_supabase()
        try:
            db.table("citas_log").update({
                "fecha_cita": new_date,
                "hora_cita":  f"{new_time}:00",
            }).eq("id", log_id).execute()
            return True
        except Exception as e:
            logger.error(f"Error en AppointmentLogRepository.reschedule_appointment_log: {e}")
            return False

    def reschedule_by_odoo_id(self, tenant_id: str, odoo_event_id: int, new_date: str, new_time: str) -> bool:
        db = get_supabase()
        try:
            db.table("citas_log").update({
                "fecha_cita": new_date,
                "hora_cita":  f"{new_time}:00",
                "estado":     "confirmada"
            }).eq("tenant_id", tenant_id).eq("odoo_event_id", odoo_event_id).execute()
            return True
        except Exception as e:
            logger.error(f"Error en AppointmentLogRepository.reschedule_by_odoo_id: {e}")
            return False

    def update_status_by_odoo_id(self, tenant_id: str, odoo_event_id: int, estado: str) -> bool:
        db = get_supabase()
        try:
            db.table("citas_log").update({"estado": estado}).eq("tenant_id", tenant_id).eq("odoo_event_id", odoo_event_id).execute()
            return True
        except Exception as e:
            logger.error(f"Error en AppointmentLogRepository.update_status_by_odoo_id: {e}")
            return False

    def reschedule_by_phone(self, tenant_id: str, wa_from: str, new_date: str, new_time: str) -> bool:
        db = get_supabase()
        try:
            db.table("citas_log").update({
                "fecha_cita": new_date,
                "hora_cita":  f"{new_time}:00",
                "estado":     "confirmada"
            }).eq("tenant_id", tenant_id).eq("wa_from", wa_from).execute()
            return True
        except Exception as e:
            logger.error(f"Error en AppointmentLogRepository.reschedule_by_phone: {e}")
            return False






