# domain/ai_service.py
# Integración con OpenAI con Tool Calling para disponibilidad y booking
#
# ARQUITECTURA:
# - check_availability: GPT consulta horas ocupadas en Odoo via tool
# - create_appointment: GPT llama esta tool cuando el usuario confirma
#   El ai_service ejecuta la creación en Odoo y retorna el event_id
#   message_handler recibe el resultado y persiste en citas_log

from openai import AsyncOpenAI
from config import settings
import logging
import json
from datetime import datetime
import re
from infrastructure.repositories.appointment_log_repo import AppointmentLogRepository
from infrastructure.repositories.tenant_config_repo import TenantConfigRepository

logger = logging.getLogger(__name__)
appointment_log_repo = AppointmentLogRepository()
tenant_config_repo = TenantConfigRepository()




def _clean_time(t_str: str) -> str:
    """Normaliza cualquier string de hora (ej: '10:00 AM', '6:00 PM', '10:00:00', '10:30') a formato HH:MM (24h)."""
    if not t_str:
        return "00:00"
    t_clean = str(t_str).upper().strip()
    m = re.search(r"(\d{1,2}):(\d{2})", t_clean)
    if not m:
        return "00:00"
    h = int(m.group(1))
    mins = int(m.group(2))
    if "PM" in t_clean and h < 12:
        h += 12
    elif "AM" in t_clean and h == 12:
        h = 0
    return f"{str(h).zfill(2)}:{str(mins).zfill(2)}"

def _clean_date(d_str: str) -> str:
    """Normaliza un string de fecha a formato YYYY-MM-DD."""
    if not d_str:
        return ""
    d_str = str(d_str).strip()
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", d_str)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    m2 = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", d_str)
    if m2:
        return f"{m2.group(3)}-{str(m2.group(2)).zfill(2)}-{str(m2.group(1)).zfill(2)}"
    return d_str[:10]

def is_within_schedule(date_str: str, time_str: str, horario_str: str, check_past: bool = True) -> tuple[bool, str]:
    """
    Verifica si una fecha y hora (Bogotá) está dentro del horario comercial y en el presente/futuro.
    Retorna (es_valido, mensaje_error)
    """
    date_str = _clean_date(date_str)
    time_str = _clean_time(time_str)
    if check_past:
        try:
            from datetime import datetime, timezone, timedelta
            BOGOTA_TZ = timezone(timedelta(hours=-5))
            now_bogota = datetime.now(BOGOTA_TZ)
            
            # 1. Validar que no sea una fecha u hora en el pasado (Bogotá)
            h_req, m_req = map(int, time_str.split(':'))
            dt_req = datetime.strptime(date_str, "%Y-%m-%d")
            dt_req_full = dt_req.replace(hour=h_req, minute=m_req, tzinfo=BOGOTA_TZ)
            
            if dt_req_full < now_bogota - timedelta(minutes=5):
                return False, "La fecha u hora seleccionada ya pasó. Por favor selecciona una fecha y hora en el presente o futuro."
        except Exception as pe:
            logger.warning(f"Error comparando fecha/hora con el tiempo actual en Bogotá: {pe}")

    if not horario_str:
        return True, ""
    try:
        from datetime import datetime
        import re
        dt_cita = datetime.strptime(date_str, "%Y-%m-%d")
        weekday = dt_cita.weekday() # 0=Lunes, ..., 6=Domingo
        
        days_map = {
            'lun': 0, 'mar': 1, 'mie': 2, 'jue': 3, 'vie': 4, 'sab': 5, 'sáb': 5, 'dom': 6,
            'lunes': 0, 'martes': 1, 'miercoles': 2, 'jueves': 3, 'viernes': 4, 'sabado': 5, 'sábado': 5, 'domingo': 6
        }
        
        h_req, m_req = map(int, time_str.split(':'))
        time_req_val = h_req * 60 + m_req
        
        parts = [p.strip() for p in horario_str.split(',')]
        day_matched = False
        allowed_range = None
        
        def parse_time_str(t_str):
            t_str = t_str.lower().strip()
            match = re.match(r'(\d+)(?::(\d+))?\s*(am|pm)', t_str)
            if not match:
                return None
            h = int(match.group(1))
            m = int(match.group(2)) if match.group(2) else 0
            period = match.group(3)
            
            if period == 'pm' and h != 12:
                h += 12
            elif period == 'am' and h == 12:
                h = 0
            return h * 60 + m

        for part in parts:
            match_days_hours = re.match(r'([a-záéíóú\-]+)\s+(\d+(?::\d+)?\s*(?:am|pm))\s*-\s*(\d+(?::\d+)?\s*(?:am|pm))', part, re.IGNORECASE)
            if not match_days_hours:
                continue
                
            days_part = match_days_hours.group(1).lower()
            start_time_str = match_days_hours.group(2)
            end_time_str = match_days_hours.group(3)
            
            covered_days = []
            if '-' in days_part:
                d_start_str, d_end_str = days_part.split('-')
                d_start = days_map.get(d_start_str.strip())
                d_end = days_map.get(d_end_str.strip())
                if d_start is not None and d_end is not None:
                    if d_start <= d_end:
                        covered_days = list(range(d_start, d_end + 1))
                    else:
                        covered_days = list(range(d_start, 7)) + list(range(0, d_end + 1))
            else:
                d_single = days_map.get(days_part.strip())
                if d_single is not None:
                    covered_days = [d_single]
                    
            if weekday in covered_days:
                day_matched = True
                start_val = parse_time_str(start_time_str)
                end_val = parse_time_str(end_time_str)
                if start_val is not None and end_val is not None:
                    allowed_range = (start_val, end_val)
                    break
                    
        if not day_matched:
            return False, "El negocio está cerrado ese día."
            
        if allowed_range:
            start_val, end_val = allowed_range
            if start_val <= time_req_val <= end_val:
                return True, ""
            else:
                h_start, m_start = divmod(start_val, 60)
                h_end, m_end = divmod(end_val, 60)
                def format_h(h, m):
                    period = 'am' if h < 12 else 'pm'
                    h_12 = h if 0 < h <= 12 else h - 12 if h > 12 else 12
                    return f"{h_12}:{str(m).zfill(2)} {period}"
                return False, f"El horario solicitado está fuera del horario de atención de ese día ({format_h(h_start, m_start)} a {format_h(h_end, m_end)})."
                
        return True, ""
    except Exception as e:
        logger.warning(f"Error parseando horario: {e}")
        return True, ""


SYSTEM_PROMPT_DEFAULT = (
    "Eres un asistente de atención al cliente amable, profesional y conciso. "
    "Responde siempre en español colombiano. "
    "Si no puedes ayudar con algo, indica amablemente que un asesor humano le atenderá pronto."
)

# ── Definición de tools disponibles para GPT ──────────────────────────────────

TOOL_CHECK_AVAILABILITY = {
    "type": "function",
    "function": {
        "name": "check_availability",
        "description": (
            "Consulta las citas ocupadas y slots disponibles en el calendario de Odoo para una fecha específica. "
            "Llama esta función CADA VEZ que el cliente proponga, confirme o consulte una fecha u hora. "
            "CRÍTICO: Incluye service_name y/o service_id (ej: 362 para Combo Manos & Pies, 18 para Balayage) "
            "para que Odoo calcule la duración real del servicio y no provoque solapamientos de citas."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "date_str": {
                    "type": "string",
                    "description": "Fecha a consultar en formato YYYY-MM-DD (hora Bogotá)"
                },
                "professional_name": {
                    "type": "string",
                    "description": "Nombre opcional del profesional solicitado (ej: Jose Roa, Valentina)"
                },
                "service_name": {
                    "type": "string",
                    "description": "Nombre del servicio solicitado (ej: Combo Manos & Pies, Balayage, Keratina)"
                },
                "service_id": {
                    "type": "integer",
                    "description": "ID numérico de Odoo del servicio si se conoce (ej: 362, 198, 18, 20, 17, 19)"
                }
            },
            "required": ["date_str"]
        }
    }
}

TOOL_CREATE_APPOINTMENT = {
    "type": "function",
    "function": {
        "name": "create_appointment",
        "description": (
            "DEBES llamar esta función INMEDIATAMENTE cuando el cliente confirme la cita con "
            "palabras como: sí, si, listo, dale, perfecto, confirmo, eso, claro, ok, va, ok. "
            "Crea la cita en el sistema. Sin llamar esta función, la cita NO existe en el calendario."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "cliente_nombre": {
                    "type": "string",
                    "description": "Nombre completo del cliente"
                },
                "servicio": {
                    "type": "string",
                    "description": "Nombre del servicio a realizar (ej: Corte clásico, Afeitado clásico)"
                },
                "precio": {
                    "type": "string",
                    "description": "Precio del servicio tal como aparece en el menú (ej: $20.000)"
                },
                "fecha": {
                    "type": "string",
                    "description": "Fecha de la cita en formato YYYY-MM-DD (hora Bogotá)"
                },
                "hora": {
                    "type": "string",
                    "description": "Hora de la cita en formato HH:MM de 24h (hora Bogotá, ej: 09:00, 14:30)"
                },
                "profesional_nombre": {
                    "type": "string",
                    "description": "Nombre del profesional seleccionado por el cliente (ej: Carlos Mendez). Si el cliente no eligió o le da igual, envía 'Cualquiera'."
                }
            },
            "required": ["cliente_nombre", "servicio", "precio", "fecha", "hora", "profesional_nombre"]
        }
    }
}


TOOL_GET_MY_APPOINTMENTS = {
    "type": "function",
    "function": {
        "name": "get_my_appointments",
        "description": (
            "Consulta las citas activas o pendientes del cliente en Odoo utilizando su número telefónico. "
            "DEBES llamar esta función cuando el cliente pida cancelar, eliminar, anular, reprogramar o ver sus citas "
            "(ej: 'quiero cancelar', 'cancelar cita', 'mis citas', 'eliminar cita', 'no puedo ir', 'reprogramar')."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "required": []
        }
    }
}

TOOL_CANCEL_APPOINTMENT = {
    "type": "function",
    "function": {
        "name": "cancel_appointment",
        "description": (
            "DEBES ejecutar esta herramienta OBLIGATORIAMENTE en el mismo instante en que el cliente "
            "pida cancelar una cita o indique cuál desea cancelar. "
            "NUNCA respondas en texto diciendo que vas a proceder a cancelar o que la cita fue cancelada sin incluir "
            "la llamada a esta herramienta en la misma respuesta. "
            "Recibe cita_id (ID numérico de la cita en Odoo)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "cita_id": {
                    "type": "integer",
                    "description": "ID numérico de la cita en Odoo (ej: 1805)"
                }
            },
            "required": ["cita_id"]
        }
    }
}

TOOL_RESCHEDULE_APPOINTMENT = {
    "type": "function",
    "function": {
        "name": "reschedule_appointment",
        "description": (
            "Reagenda (reprograma) una cita existente del cliente a una nueva fecha y hora. "
            "Llama esta función cuando el cliente haya confirmado la nueva fecha y hora para reagendar. "
            "Requiere el ID numérico de la cita (cita_id), la nueva fecha (YYYY-MM-DD) y la nueva hora (HH:MM). "
            "CRÍTICO: NUNCA digas que la cita fue reagendada sin haber llamado primero a esta herramienta."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "cita_id": {
                    "type": "integer",
                    "description": "ID numérico de la cita en Odoo a reagendar (ej: 1805)"
                },
                "nueva_fecha": {
                    "type": "string",
                    "description": "Nueva fecha en formato YYYY-MM-DD (hora Bogotá)"
                },
                "nueva_hora": {
                    "type": "string",
                    "description": "Nueva hora en formato HH:MM de 24h (hora Bogotá, ej: 10:00, 14:30)"
                }
            },
            "required": ["cita_id", "nueva_fecha", "nueva_hora"]
        }
    }
}


class AIService:
    def __init__(self, api_key: str = None, model: str = None):
        self.client = AsyncOpenAI(api_key=api_key or settings.OPENAI_API_KEY)
        self.model = model or settings.OPENAI_MODEL

    async def get_response(
        self,
        user_message: str,
        history: list[dict],
        system_prompt: str = None,
        odoo_config: dict = None,
        # Contexto de booking para ejecutar create_appointment
        sender_wa_id: str = None,
        sender_name: str = None,
        negocio_servicios: str = "",
        tenant_id: str = None,
    ) -> tuple[str, dict | None]:
        """
        Genera una respuesta de IA dado el mensaje del usuario y el historial.

        Retorna: (response_text, booking_result)
          - response_text: texto limpio para enviar al cliente
          - booking_result: dict con los datos de la cita si se creó, o None
        """
        messages = [{"role": "system", "content": system_prompt or SYSTEM_PROMPT_DEFAULT}]

        ROLE_MAP = {"agent": "assistant", "user": "user", "assistant": "assistant"}
        for msg in history[-10:]:
            role = ROLE_MAP.get(msg.get("role", "user"), "user")
            content = msg.get("content", "")
            if content:
                messages.append({"role": role, "content": content})

        messages.append({"role": "user", "content": user_message})

        try:
            if odoo_config and odoo_config.get("url"):
                tools = [
                    TOOL_CHECK_AVAILABILITY,
                    TOOL_CREATE_APPOINTMENT,
                    TOOL_GET_MY_APPOINTMENTS,
                    TOOL_CANCEL_APPOINTMENT,
                    TOOL_RESCHEDULE_APPOINTMENT,
                ]

                response = await self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    tools=tools,
                    tool_choice="auto",
                    max_tokens=600,
                    temperature=0.4,
                )

                response_message = response.choices[0].message
                booking_data = None  # resultado de create_appointment si aplica
                has_any_call = False

                # ── Ciclo de tool calling ──────────────────────────────────
                if response_message.tool_calls:
                    from domain.odoo_service import OdooService
                    odoo = OdooService(
                        url=odoo_config["url"],
                        db=odoo_config["db"],
                        user=odoo_config["user"],
                        api_key=odoo_config["api_key"],
                    )

                    # Cargar configuración del horario del negocio desde Supabase usando el repositorio
                    horario_comercial = ""
                    if tenant_id:
                        try:
                            tc_res = tenant_config_repo.get_by_tenant_id(tenant_id)
                            if tc_res:
                                horario_comercial = tc_res.get("horario", "")
                        except Exception as tc_err:
                            logger.warning(f"No se pudo cargar el horario del tenant {tenant_id} en ai_service: {tc_err}")

                    iteration = 0
                    max_iterations = 3

                    while iteration < max_iterations:
                        curr_msg = response.choices[0].message
                        if not curr_msg.tool_calls:
                            break

                        messages.append(curr_msg)

                        for tool_call in curr_msg.tool_calls:
                            fn_name = tool_call.function.name
                            fn_args = json.loads(tool_call.function.arguments or "{}")

                            if fn_name in ["cancel_appointment", "reschedule_appointment"]:
                                has_any_call = True

                            if fn_name == "check_availability":
                                date_str = fn_args.get("date_str", "")
                                prof_name = fn_args.get("professional_name")
                                serv_name = fn_args.get("service_name")
                                serv_id_arg = fn_args.get("service_id")
                                
                                # Auto-inferir profesional si el cliente tiene cita activa y no envió professional_name
                                if not prof_name and sender_wa_id:
                                    try:
                                        c_res = odoo.get_client_appointments(sender_wa_id)
                                        c_list = c_res.get("citas", []) if isinstance(c_res, dict) else []
                                        if c_list and isinstance(c_list[0], dict) and c_list[0].get("profesional"):
                                            prof_name = c_list[0].get("profesional")
                                            logger.info(f"Odoo check_availability: auto-inferido profesional='{prof_name}' desde cita activa de {sender_wa_id}")
                                    except Exception as ex_inf:
                                        logger.warning(f"No se pudo inferir profesional desde cita activa: {ex_inf}")

                                prof_id = odoo.find_professional_id(prof_name) if prof_name else None
                                
                                # Resolver service_id numérico de Odoo
                                serv_id = None
                                if serv_id_arg:
                                    try:
                                        serv_id = int(serv_id_arg)
                                    except (ValueError, TypeError):
                                        serv_id = None
                                if not serv_id and serv_name:
                                    serv_id = odoo.find_service_id(serv_name)
                                
                                dia_abierto = True
                                if horario_comercial:
                                    ok_dia, err_dia = is_within_schedule(date_str, "12:00", horario_comercial, check_past=False)
                                    if not ok_dia and "cerrado" in err_dia.lower():
                                        dia_abierto = False

                                ofrece_servicio = True
                                if prof_id and serv_id:
                                    ofrece_servicio = odoo.check_professional_specialty(prof_id, serv_id)

                                if not dia_abierto:
                                    tool_result = json.dumps({
                                        "success": False,
                                        "abierto": False,
                                        "message": f"Lo siento, el negocio está cerrado ese día. Nuestro horario de atención es: {horario_comercial}"
                                    }, ensure_ascii=False)
                                elif not ofrece_servicio:
                                    tool_result = json.dumps({
                                        "success": False,
                                        "ofrece_servicio": False,
                                        "message": f"Lo siento, el profesional '{prof_name}' no ofrece el servicio '{serv_name}' en su catálogo."
                                    }, ensure_ascii=False)
                                else:
                                    result = odoo.check_availability(date_str, professional_id=prof_id)
                                    
                                    if prof_id:
                                        raw_slots = odoo.get_available_slots(date_str, professional_id=prof_id, service_id=serv_id)
                                        avail_times = [s.get("time") for s in raw_slots if isinstance(s, dict) and s.get("available")]
                                        dur_min_val = odoo.get_service_duration_minutes(serv_id) if serv_id else 60
                                        slots_info = {
                                            "profesional": prof_name,
                                            "professional_id": prof_id,
                                            "service_id": serv_id,
                                            "duracion_minutos": dur_min_val,
                                            "horas_libres": avail_times
                                        }
                                        tool_result = json.dumps(slots_info, ensure_ascii=False)
                                    else:
                                        tool_result = json.dumps(result, ensure_ascii=False)
                                logger.info(f"Odoo check_availability: {date_str} prof={prof_name}({prof_id}) serv={serv_name}({serv_id}) → {len(result) if (ofrece_servicio and dia_abierto) else 0} eventos")

                            elif fn_name == "get_my_appointments":
                                client_phone = fn_args.get("phone", sender_wa_id or "")
                                res = odoo.get_client_appointments(client_phone)
                                if not isinstance(res, dict):
                                    res = {"success": True, "citas": res if isinstance(res, list) else [], "total": len(res) if isinstance(res, list) else 0}
                                tool_result = json.dumps(res, ensure_ascii=False)
                                total_cnt = res.get("total", len(res.get("citas", [])))
                                logger.info(f"Odoo get_my_appointments para {client_phone} → {total_cnt} citas")

                            elif fn_name == "cancel_appointment":
                                cita_id = fn_args.get("cita_id")
                                res = odoo.cancel_appointment_spa(cita_id, sender_wa_id or "")
                                if not isinstance(res, dict):
                                    res = {"success": True, "message": str(res)}
                                
                                # ✅ Sincronizar cancelación en Supabase citas_log usando el repositorio
                                if res.get("success") and tenant_id:
                                    try:
                                        if cita_id:
                                            appointment_log_repo.cancel_appointment_by_odoo_id(tenant_id, int(cita_id))
                                        if sender_wa_id:
                                            appointment_log_repo.cancel_all_appointments_by_phone(tenant_id, sender_wa_id)
                                        logger.info(f"Supabase citas_log sincronizado: estado='cancelada' para {sender_wa_id} cita_id={cita_id}")
                                    except Exception as se:
                                        logger.warning(f"No se pudo actualizar citas_log en Supabase al cancelar: {se}")


                                tool_result = json.dumps(res, ensure_ascii=False)
                                logger.info(f"Odoo cancel_appointment cita_id={cita_id} para {sender_wa_id} → {res.get('success')}")

                            elif fn_name == "reschedule_appointment":
                                cita_id_raw = fn_args.get("cita_id")
                                nueva_fecha = _clean_date(fn_args.get("nueva_fecha", ""))
                                nueva_hora  = _clean_time(fn_args.get("nueva_hora", ""))

                                # Parseo seguro de cita_id (int/str)
                                cita_id = None
                                try:
                                    if isinstance(cita_id_raw, str):
                                        digits = "".join(c for c in cita_id_raw if c.isdigit())
                                        cita_id = int(digits) if digits else None
                                    elif cita_id_raw:
                                        cita_id = int(cita_id_raw)
                                except Exception:
                                    cita_id = None

                                # 0. Validar si la nueva fecha/hora ya pasó en Bogotá
                                try:
                                    from zoneinfo import ZoneInfo
                                    bogota_tz = ZoneInfo("America/Bogota")
                                except ImportError:
                                    from datetime import timezone, timedelta
                                    bogota_tz = timezone(timedelta(hours=-5))

                                try:
                                    ahora_bogota = datetime.now(bogota_tz)
                                    y, mo, d = map(int, nueva_fecha.split("-"))
                                    h, m = map(int, nueva_hora.split(":"))
                                    fecha_hora_resched = datetime(y, mo, d, h, m, tzinfo=bogota_tz)
                                    if fecha_hora_resched <= ahora_bogota:
                                        tool_result = json.dumps({
                                            "success": False,
                                            "error_code": "PAST_DATE_TIME",
                                            "message": f"No es posible reprogramar una cita a una fecha u hora que ya pasó ({nueva_fecha} {nueva_hora}). Por favor elige una fecha y hora futura."
                                        }, ensure_ascii=False)
                                        logger.info(f"Odoo reschedule_appointment: intento de reagendar en fecha/hora pasada → {nueva_fecha} {nueva_hora}")
                                        continue
                                except Exception as ex_p:
                                    logger.warning(f"Error validando fecha pasada en reschedule: {ex_p}")

                                # 0b. Validar si la nueva fecha/hora está dentro del horario comercial
                                if horario_comercial:
                                    ok_sch, err_sch = is_within_schedule(nueva_fecha, nueva_hora, horario_comercial)
                                    if not ok_sch:
                                        tool_result = json.dumps({
                                            "success": False,
                                            "message": f"Lo siento, la nueva hora {nueva_hora} no es posible. {err_sch}"
                                        }, ensure_ascii=False)
                                        logger.info(f"Odoo reschedule_appointment: horario no permitido → {nueva_fecha} {nueva_hora}")
                                        continue

                                # 1. Obtener la cita actual para saber el profesional asignado
                                citas_actuales = odoo.get_client_appointments(sender_wa_id or "")
                                c_list = citas_actuales.get("citas", []) if isinstance(citas_actuales, dict) else []
                                
                                # Coincidencia flexible por string para evitar descalce int vs str
                                cita_actual = None
                                if cita_id:
                                    cita_actual = next((c for c in c_list if str(c.get("id")) == str(cita_id)), None)
                                if not cita_actual and c_list:
                                    cita_actual = c_list[0]
                                if cita_actual:
                                    cita_id = cita_actual.get("id")

                                profesional_id = cita_actual.get("profesional_id") if cita_actual else None
                                profesional_nombre = cita_actual.get("profesional", "el profesional") if cita_actual else "el profesional"

                                # 2. Verificar disponibilidad del PROFESIONAL en la nueva fecha/hora (en UTC)
                                from domain.odoo_service import _bogota_to_utc
                                nueva_start_utc = _bogota_to_utc(nueva_fecha, nueva_hora)  # "YYYY-MM-DD HH:MM:SS"
                                eventos_dia = odoo.check_availability(nueva_fecha)

                                # Filtrar eventos del mismo profesional en el mismo horario UTC
                                def _mismo_prof(ev):
                                    if not profesional_id:
                                        return False  # sin profesional específico, no bloquear arbitrariamente
                                    pf = ev.get(odoo.professional_field_name or "spa_professional_id")
                                    if isinstance(pf, (list, tuple)) and len(pf) >= 1:
                                        return int(pf[0]) == int(profesional_id)
                                    if isinstance(pf, dict):
                                        return int(pf.get("id", 0)) == int(profesional_id)
                                    return False

                                ocupado = any(
                                    ev.get("start") == nueva_start_utc and _mismo_prof(ev)
                                    for ev in eventos_dia or []
                                )

                                if ocupado:
                                    tool_result = json.dumps({
                                        "success": False,
                                        "message": f"Lo siento, el profesional {profesional_nombre} ya tiene una cita reservada a las {nueva_hora} en esa fecha. Por favor elige otro horario."
                                    }, ensure_ascii=False)
                                    logger.info(f"Odoo reschedule_appointment: conflicto de horario → {nueva_fecha} {nueva_hora} con {profesional_nombre}")
                                else:
                                    ok = odoo.reschedule_appointment(
                                        event_id=int(cita_id),
                                        date_str=nueva_fecha,
                                        time_str=nueva_hora,
                                    )
                                    if ok:
                                        # Sincronizar en Supabase citas_log
                                        if tenant_id:
                                            try:
                                                appointment_log_repo.reschedule_by_odoo_id(tenant_id, int(cita_id), nueva_fecha, nueva_hora)
                                                if sender_wa_id:
                                                    appointment_log_repo.reschedule_by_phone(tenant_id, sender_wa_id, nueva_fecha, nueva_hora)
                                                logger.info(f"Supabase citas_log actualizado: reagendada cita {cita_id} → {nueva_fecha} {nueva_hora}")
                                            except Exception as se:
                                                logger.warning(f"No se pudo actualizar citas_log en Supabase al reagendar: {se}")

                                        tool_result = json.dumps({
                                            "success": True,
                                            "message": f"¡Listo! Tu cita ha sido reprogramada con {profesional_nombre} al {nueva_fecha} a las {nueva_hora}."
                                        }, ensure_ascii=False)
                                        logger.info(f"Odoo reschedule_appointment cita_id={cita_id} prof={profesional_nombre} → {nueva_fecha} {nueva_hora} ✅")
                                    else:
                                        tool_result = json.dumps({
                                            "success": False,
                                            "message": "No se pudo reprogramar la cita. Por favor intenta de nuevo o comunícate con nosotros."
                                        }, ensure_ascii=False)

                            elif fn_name == "create_appointment":
                                fecha_req = _clean_date(fn_args.get("fecha", ""))
                                hora_req = _clean_time(fn_args.get("hora", "00:00"))

                                # 0. Validar si la fecha/hora está dentro del horario comercial
                                if horario_comercial:
                                    ok_sch, err_sch = is_within_schedule(fecha_req, hora_req, horario_comercial)
                                    if not ok_sch:
                                        tool_result = json.dumps({
                                            "success": False,
                                            "error_code": "OUTSIDE_SCHEDULE",
                                            "message": f"Lo siento, la hora {hora_req} no es posible. {err_sch}"
                                        }, ensure_ascii=False)
                                        logger.info(f"Odoo create_appointment: horario no permitido → {fecha_req} {hora_req}")
                                        continue

                                # Auto-cancelar citas anteriores activas si el usuario está agendando de nuevo o cambiando
                                if sender_wa_id:
                                    try:
                                        prev_res = odoo.get_client_appointments(sender_wa_id)
                                        prev_list = prev_res.get("citas", []) if isinstance(prev_res, dict) else []
                                        for old_c in prev_list:
                                            old_id = old_c.get("id")
                                            if old_id:
                                                logger.info(f"Odoo: eliminando cita previa {old_id} de {sender_wa_id} al crear nueva cita...")
                                                odoo.cancel_appointment_spa(old_id, sender_wa_id)
                                                if tenant_id:
                                                    try:
                                                        appointment_log_repo.cancel_appointment_by_odoo_id(tenant_id, int(old_id))
                                                    except Exception:
                                                        pass

                                    except Exception as ex_dup:
                                        logger.warning(f"Error cancelando cita previa en create_appointment: {ex_dup}")

                                # ✅ Crear la cita en Odoo directamente desde ai_service
                                event_id = odoo.create_appointment(
                                    name=fn_args.get("cliente_nombre", sender_name or sender_wa_id or "Cliente"),
                                    phone=sender_wa_id or "",
                                    date_str=fecha_req,
                                    time_str=hora_req,
                                    service_name=fn_args.get("servicio", ""),
                                    price=fn_args.get("precio", ""),
                                    negocio_servicios=negocio_servicios,
                                    description="Beautysync - Agendamiento",
                                    professional_name=fn_args.get("profesional_nombre", ""),
                                )
                                if event_id == "PAST_DATE_TIME":
                                    tool_result = json.dumps({
                                        "success": False,
                                        "error_code": "PAST_DATE_TIME",
                                        "message": "Lo siento, no es posible agendar en una fecha u hora que ya pasó. Por favor elige una fecha y hora futura."
                                    }, ensure_ascii=False)
                                elif event_id == "SPECIALTY_INCOMPATIBLE":
                                    tool_result = json.dumps({
                                        "success": False,
                                        "error_code": "SPECIALTY_INCOMPATIBLE",
                                        "message": f"Lo siento, el profesional '{fn_args.get('profesional_nombre')}' no ofrece el servicio '{fn_args.get('servicio')}' en su catálogo."
                                    }, ensure_ascii=False)
                                elif event_id:
                                    logger.info(
                                        f"✅ Odoo: cita creada tool_call event_id={event_id} "
                                        f"— {fn_args.get('servicio')} {fn_args.get('fecha')} {fn_args.get('hora')}"
                                    )
                                    booking_data = {
                                        "odoo_event_id": event_id,
                                        "cliente_nombre": fn_args.get("cliente_nombre", ""),
                                        "servicio": fn_args.get("servicio", ""),
                                        "precio": fn_args.get("precio", ""),
                                        "fecha": fn_args.get("fecha", ""),
                                        "hora": fn_args.get("hora", ""),
                                    }
                                    tool_result = json.dumps({
                                        "success": True,
                                        "event_id": event_id,
                                        "message": f"Cita creada exitosamente con ID {event_id}"
                                    })
                                else:
                                    logger.error("❌ create_appointment retornó None — fallo en Odoo")
                                    tool_result = json.dumps({
                                        "success": False,
                                        "message": "No se pudo crear la cita en Odoo. Reintenta."
                                    })
                            else:
                                tool_result = json.dumps({"error": "Funcion no reconocida"})

                            messages.append({
                                "tool_call_id": tool_call.id,
                                "role": "tool",
                                "name": fn_name,
                                "content": tool_result,
                            })

                        # Segunda llamada dentro del ciclo: GPT genera la respuesta o pide la siguiente herramienta
                        response = await self.client.chat.completions.create(
                            model=self.model,
                            messages=messages,
                            tools=tools,
                            tool_choice="auto",
                            max_tokens=600,
                            temperature=0.4,
                        )
                        iteration += 1

                    final_text = response.choices[0].message.content.strip() if response.choices[0].message.content else ""

                    # Sanitizador de reprogramación: Si el texto afirma que 'Voy a reprogramar para X' sin tool call o con fechas pasadas/inventadas
                    if any(k in final_text.lower() for k in ["reprogramar", "reagendar"]):
                        if not has_any_call:
                            if re.search(r"voy a (?:reprogramar|reagendar) (?:esa cita|tu cita) para", final_text, re.IGNORECASE):
                                logger.warning(f"⚠️ GPT prometió reprogramación en texto sin haber llamado reschedule_appointment. Sanitizando respuesta...")
                                final_text = re.sub(
                                    r"voy a (?:reprogramar|reagendar) (?:esa cita|tu cita) para.*",
                                    "¿A qué nueva fecha y hora futura te gustaría reprogramarla? 😊",
                                    final_text,
                                    flags=re.IGNORECASE
                                )

                    # Safety check ampliado: Si el texto afirma explícitamente haber cancelado la cita pero no se llamó a la tool de cancelación/reprogramación
                    cancellation_patterns = [
                        r"\bcita\s+(?:ha sido\s+)?(?:cancelada|anulada|eliminada|borrada)\b",
                        r"\b(?:hemos|he|procedo a)\s+(?:cancelar|anular|eliminar|borrar)\s+(?:la|tu)\s+cita\b",
                        r"\bcancelada\s+exitosamente\b",
                        r"\banulada\s+exitosamente\b"
                    ]
                    has_cancel_pattern = any(re.search(pat, final_text, re.IGNORECASE) for pat in cancellation_patterns)

                    if has_cancel_pattern and not has_any_call:
                        logger.warning(f"⚠️ GPT afirmó cancelación en texto sin haber llamado cancel_appointment ni reschedule_appointment. Ejecutando cancelación de seguridad para {sender_wa_id}...")
                        try:
                            citas_res = odoo.get_client_appointments(sender_wa_id or "")
                            c_list = citas_res.get("citas", []) if isinstance(citas_res, dict) else []
                            for c in c_list:
                                cid = c.get("id")
                                if cid:
                                    odoo.cancel_appointment_spa(cid, sender_wa_id or "")
                                    if tenant_id:
                                        try:
                                            appointment_log_repo.cancel_appointment_by_odoo_id(tenant_id, int(cid))
                                            appointment_log_repo.cancel_all_appointments_by_phone(tenant_id, sender_wa_id)
                                        except Exception:
                                            pass

                        except Exception as fe:
                            logger.error(f"Error en safety cancel: {fe}")

                    return final_text, booking_data

                final_text = response_message.content.strip() if response_message.content else ""
                
                # Sanitizador de reprogramación sin tool calls
                if any(k in final_text.lower() for k in ["reprogramar", "reagendar"]):
                    if re.search(r"voy a (?:reprogramar|reagendar) (?:esa cita|tu cita) para", final_text, re.IGNORECASE):
                        logger.warning(f"⚠️ GPT prometió reprogramación en texto sin haber llamado reschedule_appointment. Sanitizando respuesta...")
                        final_text = re.sub(
                            r"voy a (?:reprogramar|reagendar) (?:esa cita|tu cita) para.*",
                            "¿A qué nueva fecha y hora futura te gustaría reprogramarla? 😊",
                            final_text,
                            flags=re.IGNORECASE
                        )

                # Safety check para respuesta de texto plano
                cancellation_patterns = [
                    r"\bcita\s+(?:ha sido\s+)?(?:cancelada|anulada|eliminada|borrada)\b",
                    r"\b(?:hemos|he|procedo a)\s+(?:cancelar|anular|eliminar|borrar)\s+(?:la|tu)\s+cita\b",
                    r"\bcancelada\s+exitosamente\b",
                    r"\banulada\s+exitosamente\b"
                ]
                has_cancel_pattern = any(re.search(pat, final_text, re.IGNORECASE) for pat in cancellation_patterns)

                if has_cancel_pattern and sender_wa_id:
                    logger.warning(f"⚠️ GPT afirmó cancelación sin tool calls. Ejecutando cancelación de seguridad para {sender_wa_id}...")
                    try:
                        citas_res = odoo.get_client_appointments(sender_wa_id or "")
                        c_list = citas_res.get("citas", []) if isinstance(citas_res, dict) else []
                        for c in c_list:
                            cid = c.get("id")
                            if cid:
                                odoo.cancel_appointment_spa(cid, sender_wa_id or "")
                                if tenant_id:
                                    try:
                                        appointment_log_repo.cancel_appointment_by_odoo_id(tenant_id, int(cid))
                                        appointment_log_repo.cancel_all_appointments_by_phone(tenant_id, sender_wa_id)
                                    except Exception:
                                        pass

                    except Exception as fe:
                        logger.error(f"Error en safety cancel: {fe}")

                return final_text, None

            else:
                # Sin Odoo: llamada directa sin tools
                response = await self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    max_tokens=600,
                    temperature=0.4,
                )
                return response.choices[0].message.content.strip(), None

        except Exception as e:
            logger.error(f"OpenAI error: {e}")
            return "En este momento tengo un problema técnico. Un asesor te contactará pronto.", None

    async def extract_lead_details(self, history: list) -> dict:
        """
        Analiza el historial de conversación y extrae detalles estructurados del prospecto usando GPT.
        """
        if not history:
            return {}

        # Formatear el historial en texto plano
        history_text = ""
        for msg in history:
            role_label = "Cliente" if msg.get("role") == "user" else "Asesora (VALE)"
            history_text += f"{role_label}: {msg.get('content')}\n"

        prompt = (
            "Eres un extractor de datos analítico y preciso. Tu tarea es analizar el historial de conversación "
            "de un bot de ventas de WhatsApp y extraer la siguiente información del prospecto en formato JSON. "
            "Sé preciso. Si no encuentras la información de algún campo, devuélvelo como null (o un string vacío).\n\n"
            "Campos a extraer:\n"
            "1. nombre: Nombre de la persona / prospecto.\n"
            "2. negocio: Nombre del negocio, estética, peluquería o barbería.\n"
            "3. tipo_negocio: Tipo de negocio (Barbería, Peluquería, Salón de Belleza, Spa, Estética, etc.).\n"
            "4. inversion_insumos: Inversión aproximada mensual en insumos/productos en COP.\n"
            "5. plan_interes: Plan seleccionado o de interés (ej: 'Básico', 'Pro', 'Demo').\n"
            "6. email: Correo electrónico proporcionado.\n"
            "7. telefono: Número de contacto si lo menciona diferente al de origen.\n"
            "8. nit_rut: NIT o RUT si lo menciona.\n"
            "9. resumen_interes: Un resumen de una frase sobre qué le interesa al cliente.\n\n"
            "Responde ÚNICAMENTE con el objeto JSON válido. El formato del JSON debe ser exactamente:\n"
            "{\n"
            "  \"nombre\": \"...\",\n"
            "  \"negocio\": \"...\",\n"
            "  \"tipo_negocio\": \"...\",\n"
            "  \"inversion_insumos\": \"...\",\n"
            "  \"plan_interes\": \"...\",\n"
            "  \"email\": \"...\",\n"
            "  \"telefono\": \"...\",\n"
            "  \"nit_rut\": \"...\",\n"
            "  \"resumen_interes\": \"...\"\n"
            "}"
        )

        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": f"Historial de conversación:\n{history_text}"}
                ],
                max_tokens=300,
                temperature=0.0,
                response_format={"type": "json_object"}
            )
            raw_json = response.choices[0].message.content.strip()
            return json.loads(raw_json)
        except Exception as e:
            logger.error(f"Error al extraer detalles del lead con OpenAI: {e}")
            return {}

