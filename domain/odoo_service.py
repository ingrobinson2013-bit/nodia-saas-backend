# domain/odoo_service.py
# Servicio de conexión a Odoo via JSON-RPC (igual que el workflow n8n original)
# IMPORTANTE: Odoo almacena fechas en UTC. Bogotá = UTC-5, siempre sumar 5h.

import httpx
import logging
from datetime import datetime, timedelta, date

logger = logging.getLogger(__name__)

BOGOTA_UTC_OFFSET_HOURS = 5  # Bogotá es UTC-5, sumamos 5h para convertir a UTC


def _bogota_to_utc(date_str: str, time_str: str) -> str:
    """
    Convierte hora local de Bogotá (UTC-5) a UTC para enviar a Odoo.
    date_str: 'YYYY-MM-DD', time_str: 'HH:MM'
    Retorna: 'YYYY-MM-DD HH:MM:SS' en UTC
    """
    h, m = map(int, time_str.split(":"))
    y, mo, d = map(int, date_str.split("-"))
    bogota_dt = datetime(y, mo, d, h, m)
    utc_dt = bogota_dt + timedelta(hours=BOGOTA_UTC_OFFSET_HOURS)
    return utc_dt.strftime("%Y-%m-%d %H:%M:%S")


def _format_attendance_hour(hour_float: float) -> str:
    """Convierte un float decimal de hora (ej: 8.5) a string legible (ej: 8:30am)."""
    h = int(hour_float)
    m = int(round((hour_float - h) * 60))
    period = "am" if h < 12 else "pm"
    h_12 = h if 0 < h <= 12 else h - 12 if h > 12 else 12
    return f"{h_12}:{str(m).zfill(2)}{period}"


def _duration_from_service(service_name: str, negocio_servicios: str = "") -> int:
    """
    Extrae la duración en minutos del nombre del servicio.
    Busca en el texto de servicios del negocio '(30 min)', '(45 min)', etc.
    Si no encuentra, usa tabla de respaldo.
    """
    if negocio_servicios:
        service_norm = (service_name or "").lower()[:6]
        for line in negocio_servicios.split("\n"):
            if service_norm in line.lower():
                import re
                match = re.search(r"\((\d+)\s*min\)", line, re.IGNORECASE)
                if match:
                    return int(match.group(1))

    # Tabla de respaldo
    fallback = {
        "corte": 30, "barba": 45, "afeitado": 20,
        "capilar": 60, "tratamiento": 60, "masaje": 60,
        "manicure": 45, "pedicure": 50,
    }
    sn_lower = (service_name or "").lower()
    for key, dur in fallback.items():
        if key in sn_lower:
            return dur
    return 30  # default


class OdooService:
    """
    Cliente JSON-RPC para Odoo 17.
    Replica exactamente la lógica de los nodos 'Auth Odoo' y 'Crear Cita Odoo'
    del workflow n8n [BOT] SaaS Barberías - MultiTenant V2.
    """

    def __init__(self, url: str, db: str, user: str, api_key: str):
        # Sanitizar URL: eliminar \n, \r, espacios y caracteres no imprimibles
        # que se cuelan al copiar/pegar en Supabase y rompen httpx
        self.url = "".join(c for c in (url or "").strip() if c.isprintable()).rstrip("/")
        self.db  = (db or "").strip()
        self.user = (user or "").strip()
        self.api_key = (api_key or "").strip()
        self.uid = None
        self.professional_field_name = "professional_id"  # por defecto
        if not self.url:
            logger.error("❌ OdooService: odoo_url vacía o inválida — skip auth")
            return
        if self.db and self.user and self.api_key:
            try:
                self._authenticate()
            except Exception as e:
                logger.warning(f"Odoo: no se pudo autenticar usuario — solo endpoints públicos estarán disponibles: {e}")

    def _jsonrpc(self, service: str, method: str, args: list) -> dict:
        """Ejecuta una llamada JSON-RPC al endpoint /jsonrpc de Odoo."""
        payload = {
            "jsonrpc": "2.0",
            "method": "call",
            "id": 1,
            "params": {
                "service": service,
                "method": method,
                "args": args,
            },
        }
        try:
            response = httpx.post(
                f"{self.url}/jsonrpc",
                json=payload,
                timeout=15.0,
                headers={"Content-Type": "application/json"},
            )
            response.raise_for_status()
            data = response.json()
            if "error" in data:
                raise Exception(f"Odoo JSON-RPC error: {data['error']}")
            return data.get("result")
        except httpx.HTTPError as e:
            raise Exception(f"HTTP error calling Odoo: {e}") from e

    def _authenticate(self):
        """
        Autentica con Odoo y obtiene el uid.
        Equivalente al nodo 'Auth Odoo' del workflow n8n.
        """
        try:
            result = self._jsonrpc(
                service="common",
                method="login",
                args=[self.db, self.user, self.api_key],
            )
            if not result or not isinstance(result, int) or result <= 0:
                raise Exception(f"Auth fallida — resultado: {result}")
            self.uid = result
            logger.info(f"✅ Odoo JSON-RPC autenticado. uid={self.uid} db={self.db}")

            # Detectar dinámicamente si el campo de profesional es 'professional_id' o 'spa_professional_id'
            # IMPORTANTE: fields_get NO lanza excepción cuando un campo no existe — devuelve {} (dict vacío).
            # Por eso reseteamos a None primero y consultamos ambos campos en una sola llamada.
            self.professional_field_name = None
            try:
                fields = self._execute(
                    "calendar.event", "fields_get",
                    [["professional_id", "spa_professional_id"]],
                    {"attributes": ["type"]}
                )
                if "professional_id" in fields:
                    self.professional_field_name = "professional_id"
                elif "spa_professional_id" in fields:
                    self.professional_field_name = "spa_professional_id"
                else:
                    logger.warning("calendar.event: no se encontró ni 'professional_id' ni 'spa_professional_id' — se omitirá el profesional al crear citas")
            except Exception as e_field:
                logger.warning(f"No se pudo detectar el campo de profesional en calendar.event: {e_field}")
            logger.info(f"Odoo: Campo de profesional detectado y configurado como: '{self.professional_field_name}'")

        except Exception as e:
            logger.error(f"❌ Error autenticando en Odoo: {e}")
            self.uid = None

    def _execute(self, model: str, method: str, args: list, kwargs: dict = None) -> any:
        """Wrapper para execute_kw."""
        if not self.uid:
            raise Exception("OdooService no tiene uid — autenticación fallida")
        full_args = [self.db, self.uid, self.api_key, model, method, args]
        if kwargs:
            full_args.append(kwargs)
        return self._jsonrpc(service="object", method="execute_kw", args=full_args)

    # ──────────────────────────────────────────────────────────────────
    # CONTACTOS
    # ──────────────────────────────────────────────────────────────────

    def search_partner(self, phone: str, name: str = None) -> int | None:
        """Busca un contacto por teléfono. Si no existe, lo crea."""
        if not self.uid:
            return None
        try:
            # Búsqueda por los últimos 10 dígitos del teléfono
            ids = self._execute(
                "res.partner", "search",
                [[["phone", "ilike", phone[-10:]]]]
            )
            if ids:
                return ids[0]
            # Crear contacto nuevo
            new_id = self._execute(
                "res.partner", "create",
                [{"name": name or f"Cliente WhatsApp {phone}", "phone": phone}]
            )
            logger.info(f"Odoo: nuevo partner creado id={new_id} — {name}")
            return new_id
        except Exception as e:
            logger.error(f"Error buscando/creando partner: {e}")
            return None

    # ──────────────────────────────────────────────────────────────────
    # CITAS
    # ──────────────────────────────────────────────────────────────────

    def create_appointment(
        self,
        name: str,
        phone: str,
        date_str: str,
        time_str: str,
        service_name: str = "",
        price: str = "",
        negocio_servicios: str = "",
        description: str = "",
        professional_name: str = "",
    ) -> int | None:
        """
        Crea una cita en Odoo convirtiendo hora Bogotá → UTC.
        
        Replica exactamente el nodo 'Crear Cita Odoo' del workflow n8n:
        - date_str: 'YYYY-MM-DD' (hora local Bogotá)
        - time_str: 'HH:MM'     (hora local Bogotá)
        
        Retorna el event_id (entero) o None si falla.
        """
        if not self.uid:
            logger.error("Odoo: uid no disponible — fallo de autenticación")
            return None
        try:
            # 0. Validar fecha y hora futura en Bogotá (America/Bogota UTC-5)
            try:
                from zoneinfo import ZoneInfo
                bogota_tz = ZoneInfo("America/Bogota")
            except ImportError:
                from datetime import timezone, timedelta
                bogota_tz = timezone(timedelta(hours=-5))

            ahora_bogota = datetime.now(bogota_tz)
            y, mo, d = map(int, date_str.split("-"))
            h, m = map(int, time_str.split(":"))
            fecha_hora_cita_bogota = datetime(y, mo, d, h, m, tzinfo=bogota_tz)

            if fecha_hora_cita_bogota <= ahora_bogota:
                logger.warning(f"Odoo: Intento de agendar en fecha/hora pasada ({date_str} {time_str}). Rechazado.")
                return "PAST_DATE_TIME"

            # 1. Buscar ID del servicio en product.template mediante coincidencia difusa primero
            service_id = self.find_service_id(service_name)

            # 1b. Calcular duración real del servicio (priorizando spa_duration de Odoo)
            dur_min = 30
            if service_id:
                try:
                    prod = self._execute(
                        "product.template", "read",
                        [[service_id], ["spa_duration"]]
                    )
                    if prod and isinstance(prod, list) and len(prod) >= 1:
                        spa_dur = prod[0].get("spa_duration")
                        if spa_dur:
                            dur_min = int(float(spa_dur) * 60)
                            logger.info(f"Odoo: Duración del servicio '{service_name}' extraída de Odoo: {dur_min} min")
                except Exception as ex_dur:
                    logger.warning(f"Error obteniendo duración de Odoo para servicio {service_id}: {ex_dur}")

            if not service_id or dur_min == 30:
                dur_min = _duration_from_service(service_name, negocio_servicios)

            dur_hours = dur_min / 60.0

            # 2. Calcular hora de fin en Bogotá
            total_min = h * 60 + m + dur_min
            stop_h = str(total_min // 60).zfill(2)
            stop_m = str(total_min % 60).zfill(2)
            stop_time_str = f"{stop_h}:{stop_m}"

            # 3. Convertir Bogotá → UTC (sumar 5 horas)
            start_utc = _bogota_to_utc(date_str, time_str)
            stop_utc  = _bogota_to_utc(date_str, stop_time_str)

            # 4. Buscar o crear partner
            partner_id = self.search_partner(phone, name)

            # 5. Nombre del evento igual que en n8n: "Servicio - Nombre"
            event_name = f"{service_name} - {name}" if service_name else f"Cita WhatsApp: {name}"
            precio_desc = f" | Valor: {price}" if price else ""
            
            # 5b. Agregar profesional a la descripción si aplica
            profesional_desc = f" | Profesional: {professional_name}" if professional_name else ""
            desc_full = (
                f"Reserva WhatsApp | Tel: {phone} | Origen: AgenteIA VALE{precio_desc}{profesional_desc}\nBeautysync - Agendamiento"
            )
            if description:
                desc_full += f"\n{description}"

            # 6b. Buscar ID del profesional en hr.employee
            professional_id = None
            if professional_name and professional_name.lower() != "cualquiera":
                pro_list = self.get_professionals()
                for p in pro_list:
                    p_name = p.get("name", "").lower()
                    req_name = professional_name.lower()
                    if p_name == req_name or req_name in p_name or p_name in req_name:
                        professional_id = p.get("id")
                        break

            # 6c. Validar que el profesional realmente ofrezca este servicio
            if professional_id and service_id:
                if not self.check_professional_specialty(professional_id, service_id):
                    logger.warning(f"Odoo create_appointment: specialty check failed. {professional_name} does not offer {service_name}")
                    return "SPECIALTY_INCOMPATIBLE"

            # 7. Crear evento en calendar.event con IDs relacionales y campos spa
            event_data = {
                "name": event_name,
                "start": start_utc,
                "stop": stop_utc,
                "duration": dur_hours,
                "description": desc_full,
                "spa_customer_phone": phone or "",
                "spa_status": "confirmed",
                "spa_source": "wa",
            }
            if partner_id:
                event_data["partner_ids"] = [(4, partner_id)]
            if professional_id and self.professional_field_name:
                event_data[self.professional_field_name] = professional_id
            if service_id:
                svc_field = getattr(self, "service_field_name", None) or "spa_service_id"
                event_data[svc_field] = service_id

            try:
                event_id = self._execute("calendar.event", "create", [event_data])
            except Exception as e:
                logger.error(f"❌ Error creando cita en Odoo: {e}")
                return None

            logger.info(
                f"✅ Odoo: cita creada event_id={event_id} — '{event_name}' "
                f"Bogotá={date_str} {time_str} / UTC={start_utc} | Profesional={professional_name} (id={professional_id})"
            )
            return event_id
        except Exception as e:
            logger.error(f"❌ Error general en create_appointment: {e}")
            return None

    # ──────────────────────────────────────────────────────────────────
    # INTEGRACIÓN ENDPOINTS /api/spa/ (Cancelación, Mis Citas, Slots)
    # ──────────────────────────────────────────────────────────────────

    def get_client_appointments(self, phone: str = "") -> dict:
        """
        Consulta las citas del cliente en Odoo directamente mediante ORM search_read de calendar.event.
        """
        if not self.url or not phone:
            return {"success": True, "citas": [], "total": 0}
        
        clean_phone = (phone or "").replace("+", "").strip()
        return self._fallback_get_client_appointments(clean_phone)

    def find_service_id(self, service_name: str) -> int | None:
        """Busca el ID relacional de un servicio en product.template mediante coincidencia difusa inteligente."""
        if not self.uid or not service_name:
            return None
        try:
            import re
            clean = re.sub(r'[^a-zA-Z0-9\s]', '', service_name.lower()).strip()
            # Omitir conectores comunes para evitar coincidencias de ruido (ej: "y", "de")
            stop_words = {"y", "de", "con", "para", "del", "el", "la", "los", "las", "un", "una", "unos", "unas"}
            words = [w for w in clean.split() if len(w) >= 3 and w not in stop_words]
            
            prods = self._execute(
                "product.template", "search_read",
                [],
                {"fields": ["id", "name"]}
            )
            
            # 1. Coincidencia exacta o contenida (máxima prioridad)
            for p in prods or []:
                p_norm = re.sub(r'[^a-zA-Z0-9\s]', '', p.get("name", "").lower()).strip()
                p_norm_clean = " ".join(p_norm.split())
                clean_norm = " ".join(clean.split())
                if clean_norm == p_norm_clean or clean_norm in p_norm_clean or p_norm_clean in clean_norm:
                    return p["id"]
            
            # 2. Búsqueda por puntuación de coincidencia de palabras clave
            best_prod_id = None
            best_score = 0
            
            for p in prods or []:
                p_norm = re.sub(r'[^a-zA-Z0-9\s]', '', p.get("name", "").lower()).strip()
                p_words = set(p_norm.split())
                
                score = 0
                for w in words:
                    if w in p_words:
                        score += 2  # coincidencia de palabra completa vale más
                    elif any(w in pw or pw in w for pw in p_words):
                        score += 1
                
                if score > best_score:
                    best_score = score
                    best_prod_id = p["id"]
            
            if best_score > 0:
                return best_prod_id
                
            return None
        except Exception as e:
            logger.warning(f"Odoo find_service_id error: {e}")
            return None

    def find_partner_ids(self, phone: str) -> list[int]:
        """Busca todos los IDs de contactos existentes por teléfono o celular."""
        if not self.uid or not phone:
            return []
        try:
            clean_10 = phone[-10:] if len(phone) >= 10 else phone
            ids = self._execute(
                "res.partner", "search",
                [[["active", "=", True], "|", "|", ["phone", "ilike", clean_10], ["mobile", "ilike", clean_10], ["phone", "ilike", phone]]]
            )
            return ids or []
        except Exception as e:
            logger.warning(f"Odoo find_partner_ids error: {e}")
            return []

    def find_partner_id(self, phone: str) -> int | None:
        """Busca el primer ID de contacto existente por teléfono."""
        pids = self.find_partner_ids(phone)
        return pids[0] if pids else None

    def _fallback_get_client_appointments(self, phone: str) -> dict:
        """Fallback: busca citas del cliente en calendar.event mediante partner_id, teléfono y descripción."""
        if not phone:
            return {"success": True, "citas": [], "total": 0}
        try:
            clean_10 = phone[-10:] if len(phone) >= 10 else phone
            events_dict = {}

            # 1. Búsqueda por partner_ids
            partner_ids = self.find_partner_ids(phone)
            prof_field = self.professional_field_name or "spa_professional_id"
            base_fields = ["id", "name", "start", "stop", "description", prof_field]
            for pid in partner_ids:
                if isinstance(pid, int):
                    try:
                        evs1 = self._execute(
                            "calendar.event", "search_read",
                            [[["partner_ids", "in", [pid]], ["active", "=", True]]],
                            {"fields": base_fields, "order": "start desc"}
                        )
                        for ev in evs1 or []:
                            events_dict[ev["id"]] = ev
                    except Exception as e1:
                        logger.warning(f"Fallback query partner {pid} error: {e1}")

            # 2. Búsqueda por spa_customer_phone
            try:
                evs2 = self._execute(
                    "calendar.event", "search_read",
                    [[["spa_customer_phone", "ilike", clean_10], ["active", "=", True]]],
                    {"fields": base_fields, "order": "start desc"}
                )
                for ev in evs2 or []:
                    events_dict[ev["id"]] = ev
            except Exception as e2:
                logger.warning(f"Fallback query 2 error: {e2}")

            # 3. Búsqueda por descripción (ej: "Tel: 573018511200")
            try:
                evs3 = self._execute(
                    "calendar.event", "search_read",
                    [[["description", "ilike", clean_10], ["active", "=", True]]],
                    {"fields": base_fields, "order": "start desc"}
                )
                for ev in evs3 or []:
                    events_dict[ev["id"]] = ev
            except Exception as e3:
                logger.warning(f"Fallback query 3 error: {e3}")

            from datetime import datetime, timedelta, timezone
            bogota_tz = timezone(timedelta(hours=-5))
            ahora_bogota = datetime.now(bogota_tz)
            
            citas = []
            for ev in events_dict.values():
                start_str = ev.get("start", "")
                if start_str:
                    try:
                        dt_utc = datetime.strptime(start_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                        dt_bogota = dt_utc.astimezone(bogota_tz)
                        if dt_bogota < ahora_bogota - timedelta(hours=2):
                            continue
                        fecha_formatted = dt_bogota.strftime("%d/%m/%Y")
                        hora_formatted = dt_bogota.strftime("%H:%M")
                    except Exception:
                        fecha_formatted = start_str[:10]
                        hora_formatted = start_str[11:16]
                else:
                    fecha_formatted = ""
                    hora_formatted = ""
                
                prof_field = self.professional_field_name or "spa_professional_id"
                raw_prof = ev.get(prof_field)
                if isinstance(raw_prof, (list, tuple)) and len(raw_prof) == 2:
                    profesional_name = raw_prof[1]  # [id, "Nombre Profesional"]
                    profesional_id = raw_prof[0]
                elif isinstance(raw_prof, dict):
                    profesional_name = raw_prof.get("name", "Asignado")
                    profesional_id = raw_prof.get("id")
                else:
                    profesional_name = "Asignado"
                    profesional_id = None
                
                raw_name = ev.get("name", "")
                servicio_name = raw_name.split("-")[0].strip() if "-" in raw_name else raw_name
                
                citas.append({
                    "id": ev["id"],
                    "servicio": servicio_name,
                    "profesional": profesional_name,
                    "profesional_id": profesional_id,
                    "fecha": fecha_formatted,
                    "hora": hora_formatted,
                    "start_utc": start_str,  # conservamos UTC para comparaciones internas
                })
            
            logger.info(f"Odoo Fallback get_client_appointments para {phone}: {len(citas)} citas encontradas")
            return {"success": True, "citas": citas, "total": len(citas)}
        except Exception as e:
            logger.error(f"Fallback get_client_appointments error: {e}")
            return {"success": False, "citas": [], "total": 0}

    def cancel_appointment_spa(self, cita_id: int = 0, phone: str = "") -> dict:
        """
        Cancela una cita del cliente en Odoo llamando al endpoint POST /api/spa/cancelar,
        y luego garantiza la eliminación física de la cita (unlink) en Odoo para liberar espacio.
        Si cita_id es 0 o invalido, busca automáticamente las citas activas del cliente por su teléfono.
        """
        if not self.url:
            return {"success": False, "message": "Servicio Odoo no configurado"}

        clean_phone = (phone or "").replace("+", "").strip()
        
        # Casteo seguro de cita_id (soporta enteros, strings como "75" o "cita_75")
        cita_id_int = 0
        try:
            if isinstance(cita_id, str):
                digits = "".join(c for c in str(cita_id) if c.isdigit())
                cita_id_int = int(digits) if digits else 0
            elif cita_id:
                cita_id_int = int(cita_id)
        except Exception:
            cita_id_int = 0

        # Si no hay cita_id específico pero hay teléfono, buscar y cancelar todas las citas activas del cliente
        if not cita_id_int and clean_phone:
            citas_res = self.get_client_appointments(clean_phone)
            c_list = citas_res.get("citas", []) if isinstance(citas_res, dict) else []
            if c_list:
                cancelled_any = False
                for c in c_list:
                    cid = c.get("id")
                    if cid:
                        if self.cancel_appointment(int(cid)):
                            cancelled_any = True
                if cancelled_any:
                    return {"success": True, "message": "Tus citas pendientes han sido canceladas correctamente."}
            return {"success": False, "message": "No se encontraron citas activas asociadas a tu número para cancelar."}

        if not cita_id_int:
            return {"success": False, "message": "ID de cita inválido"}

        # 1. Llamar al endpoint /api/spa/cancelar para disparar flujos internos en Odoo primero
        url = f"{self.url}/api/spa/cancelar"
        payload = {
            "jsonrpc": "2.0",
            "method": "call",
            "id": 1,
            "params": {
                "cita_id": cita_id_int,
                "phone": clean_phone
            }
        }
        
        try:
            response = httpx.post(
                url,
                json=payload,
                timeout=12.0,
                headers={"Content-Type": "application/json"}
            )
            response.raise_for_status()
            data = response.json()
            if "error" not in data:
                result = data.get("result", {})
                if isinstance(result, dict) and result.get("success"):
                    logger.info(f"Odoo /api/spa/cancelar exitoso para cita {cita_id_int}")
        except Exception as e:
            logger.warning(f"Error llamando a /api/spa/cancelar: {e}")
            
        # 2. SIEMPRE garantizar la eliminación física (unlink) mediante el ORM
        unlink_ok = self.cancel_appointment(cita_id_int)
        
        return {
            "success": unlink_ok, 
            "message": "Tu cita ha sido cancelada correctamente." if unlink_ok else "No se pudo cancelar la cita."
        }

    def get_available_slots(self, date_str: str, professional_id: int = None, service_id: int = None) -> list:
        """
        Consulta slots disponibles llamando al endpoint POST /api/spa/slots (usando JSON-RPC 2.0).
        """
        if not self.url:
            return []
        url = f"{self.url}/api/spa/slots"
        params = {"date": date_str}
        if professional_id:
            params["professional_id"] = int(professional_id)
        if service_id:
            params["service_id"] = int(service_id)
        
        payload = {
            "jsonrpc": "2.0",
            "method": "call",
            "params": params
        }
        
        try:
            response = httpx.post(
                url,
                json=payload,
                timeout=10.0,
                headers={"Content-Type": "application/json"}
            )
            response.raise_for_status()
            data = response.json()
            if isinstance(data, dict):
                # Odoo JSON-RPC retorna {"result": {"success": True, "slots": [...]}} o similar
                res = data.get("result")
                if isinstance(res, dict):
                    return res.get("slots", [])
                elif isinstance(res, list):
                    return res
                return data.get("slots", [])
            return []
        except Exception as e:
            logger.warning(f"No se pudo consultar /api/spa/slots ({url}) via JSON-RPC: {e}")
            return []

    def cancel_appointment(self, event_id: int) -> bool:
        """Elimina (unlink) por completo una cita en Odoo para liberar el espacio.
        Si el registro ya no existe (MissingError), se considera éxito — ya fue eliminado."""
        if not self.uid or not event_id:
            return False
        try:
            self._execute("calendar.event", "unlink", [[event_id]])
            logger.info(f"Odoo: cita {event_id} eliminada por completo (unlink)")
            return True
        except Exception as e:
            err_str = str(e)
            # Si el registro ya no existe (lo eliminó el controlador /api/spa/cancelar antes),
            # es un éxito: el objetivo era eliminarlo y ya está eliminado.
            if "MissingError" in err_str or "Record does not exist" in err_str:
                logger.info(f"Odoo: cita {event_id} ya no existe en Odoo (MissingError) — se considera cancelada exitosamente.")
                return True
            logger.error(f"Error eliminando cita {event_id}: {e}")
            return False

    def reschedule_appointment(
        self, event_id: int, date_str: str, time_str: str, dur_min: int = 30
    ) -> bool:
        """
        Reagenda una cita. Convierte Bogotá → UTC.
        date_str: 'YYYY-MM-DD', time_str: 'HH:MM' (hora Bogotá)
        """
        if not self.uid or not event_id:
            return False
        try:
            # Validar que la nueva fecha y hora sea futura en Bogotá
            try:
                from zoneinfo import ZoneInfo
                bogota_tz = ZoneInfo("America/Bogota")
            except ImportError:
                from datetime import timezone, timedelta
                bogota_tz = timezone(timedelta(hours=-5))

            ahora_bogota = datetime.now(bogota_tz)
            y, mo, d = map(int, date_str.split("-"))
            h, m = map(int, time_str.split(":"))
            fecha_hora_cita_bogota = datetime(y, mo, d, h, m, tzinfo=bogota_tz)

            if fecha_hora_cita_bogota <= ahora_bogota:
                logger.warning(f"Odoo: Intento de reagendar en fecha/hora pasada ({date_str} {time_str}). Rechazado.")
                return False

            # Intentar obtener la duración real del evento en Odoo para preservar su duración original
            try:
                ev_data = self._execute("calendar.event", "read", [[event_id], ["duration"]])
                if ev_data and isinstance(ev_data, list) and len(ev_data) >= 1:
                    dur_val = ev_data[0].get("duration")
                    if dur_val:
                        dur_min = int(float(dur_val) * 60)
                        logger.info(f"Odoo reschedule_appointment: obtenida duración de cita {event_id} desde Odoo: {dur_min} min")
            except Exception as ev_ex:
                logger.warning(f"Odoo reschedule_appointment: no se pudo leer la duración actual de la cita {event_id}: {ev_ex}")

            total_min = h * 60 + m + dur_min
            stop_h = str(total_min // 60).zfill(2)
            stop_m = str(total_min % 60).zfill(2)

            start_utc = _bogota_to_utc(date_str, time_str)
            stop_utc  = _bogota_to_utc(date_str, f"{stop_h}:{stop_m}")

            self._execute("calendar.event", "write", [[event_id], {
                "start": start_utc,
                "stop": stop_utc,
                "duration": dur_min / 60.0,
            }])
            logger.info(f"Odoo: cita {event_id} reagendada → {date_str} {time_str} Bogotá / UTC={start_utc}")
            return True
        except Exception as e:
            logger.error(f"Error reagendando cita {event_id}: {e}")
            return False

    def find_professional_id(self, name: str) -> int | None:
        """Busca el ID de un profesional por coincidencia parcial de su nombre."""
        if not self.uid or not name:
            return None
        try:
            profs = self.get_professionals()
            name_clean = name.lower().strip()
            # 1. Coincidencia exacta o parcial
            for p in profs:
                p_name = p.get("name", "").lower()
                if name_clean in p_name or p_name in name_clean:
                    return p.get("id")
            return None
        except Exception as e:
            logger.warning(f"Error en find_professional_id: {e}")
            return None

    def check_professional_specialty(self, professional_id: int, service_id: int) -> bool:
        """
        Verifica si un profesional tiene asignada una especialidad (servicio) en Odoo.
        """
        if not self.uid or not professional_id or not service_id:
            return True  # por defecto si no hay info, permitimos para no bloquear
        try:
            # Consultar hr.employee para ver si el service_id (product.template) está en spa_specialties
            emp = self._execute(
                "hr.employee", "read",
                [[professional_id]],
                {"fields": ["spa_specialties"]}
            )
            if emp and isinstance(emp, list):
                specialties = emp[0].get("spa_specialties", [])
                if specialties and isinstance(specialties, list):
                    return service_id in specialties
            return False
        except Exception as e:
            logger.warning(f"Error en check_professional_specialty para prof={professional_id} serv={service_id}: {e}")
            return True  # en caso de error/campo no existente, no bloquear

    def check_availability(self, date_str: str, professional_id: int = None) -> list:
        """
        Devuelve los eventos del negocio para un día (en UTC), añadiendo campos
        en hora local de Bogotá (inicio_bogota, fin_bogota) para el Agente IA.
        date_str en formato YYYY-MM-DD (hora Bogotá).
        Filtra por profesional_id si se especifica.
        """
        if not self.uid:
            return []
        try:
            # El día en Bogotá empieza a las 05:00 UTC y termina a las 04:59 UTC del día siguiente
            start_utc = _bogota_to_utc(date_str, "00:00")
            end_utc   = _bogota_to_utc(date_str, "23:59")
            fields_to_read = ["name", "start", "stop", "duration"]
            if self.professional_field_name:
                fields_to_read.append(self.professional_field_name)

            domain = [["start", ">=", start_utc], ["start", "<=", end_utc], ["active", "=", True]]
            if professional_id and self.professional_field_name:
                domain.append([self.professional_field_name, "=", professional_id])

            events = self._execute(
                "calendar.event", "search_read",
                [domain],
                {"fields": fields_to_read},
            )

            # Normalizar para que el resto del sistema que asume 'professional_id' siga funcionando
            if events and self.professional_field_name and self.professional_field_name != "professional_id":
                for ev in events:
                    if self.professional_field_name in ev:
                        ev["professional_id"] = ev[self.professional_field_name]

            # Agregar campos convertidos a Bogotá para simplificar la lectura de la IA
            from datetime import datetime, timezone, timedelta
            bogota_tz = timezone(timedelta(hours=-5))

            for ev in events or []:
                # 1. Obtener profesional_id y profesional_nombre
                prof_val = ev.get(self.professional_field_name or "spa_professional_id")
                prof_id = None
                prof_name = "Cualquiera"
                if isinstance(prof_val, (list, tuple)) and len(prof_val) == 2:
                    prof_id = prof_val[0]
                    prof_name = prof_val[1]
                elif isinstance(prof_val, dict):
                    prof_id = prof_val.get("id")
                    prof_name = prof_val.get("name", "Cualquiera")

                ev["profesional_id_int"] = prof_id
                ev["profesional_nombre"] = prof_name

                # 2. Convertir start y stop de UTC a Bogotá
                start_str = ev.get("start", "")
                stop_str = ev.get("stop", "")
                if start_str:
                    try:
                        dt_utc = datetime.strptime(start_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                        ev["inicio_bogota"] = dt_utc.astimezone(bogota_tz).strftime("%Y-%m-%d %H:%M")
                    except Exception:
                        ev["inicio_bogota"] = start_str[:16]
                if stop_str:
                    try:
                        dt_utc = datetime.strptime(stop_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                        ev["fin_bogota"] = dt_utc.astimezone(bogota_tz).strftime("%Y-%m-%d %H:%M")
                    except Exception:
                        ev["fin_bogota"] = stop_str[:16]

            return events or []
        except Exception as e:
            logger.error(f"Error consultando disponibilidad Odoo: {e}")
            return []

    def get_professionals(self) -> list:
        """Devuelve los profesionales activos en Odoo con sus especialidades y horarios dinámicos (con caché simple)."""
        if not self.uid:
            return []
        
        global _PROFESSIONALS_CACHE, _PROFESSIONALS_CACHE_TIME
        if '_PROFESSIONALS_CACHE' not in globals():
            _PROFESSIONALS_CACHE = {}
            _PROFESSIONALS_CACHE_TIME = {}
            
        now = datetime.now()
        cache_key = f"{self.url}_{self.db}"
        
        if cache_key in _PROFESSIONALS_CACHE and cache_key in _PROFESSIONALS_CACHE_TIME:
            if (now - _PROFESSIONALS_CACHE_TIME[cache_key]).total_seconds() < 1800:
                return _PROFESSIONALS_CACHE[cache_key]

        try:
            # 1. Obtener empleados activos, sus especialidades y su calendario de recursos
            try:
                employees = self._execute(
                    "hr.employee", "search_read",
                    [[["active", "=", True], ["is_spa_professional", "=", True]]],
                    {"fields": ["id", "name", "spa_specialties", "resource_calendar_id"]}
                )
            except Exception as e_spa:
                logger.warning(f"Odoo get_professionals: no se pudo filtrar por is_spa_professional, usando fallback: {e_spa}")
                employees = self._execute(
                    "hr.employee", "search_read",
                    [[["active", "=", True]]],
                    {"fields": ["id", "name", "spa_specialties", "resource_calendar_id"]}
                )
            
            # 2. Obtener nombres de productos/servicios para mapear especialidades
            prods = self._execute(
                "product.template", "search_read",
                [],
                {"fields": ["id", "name"]}
            )
            prod_map = {p["id"]: p["name"] for p in prods or [] if p.get("id") and p.get("name")}
            
            # 3. Obtener los horarios de atención (attendances) de los calendarios asociados
            calendar_ids = list(set(
                emp["resource_calendar_id"][0]
                for emp in employees or []
                if emp.get("resource_calendar_id") and isinstance(emp["resource_calendar_id"], list)
            ))
            
            att_map = {}
            if calendar_ids:
                try:
                    attendances = self._execute(
                        "resource.calendar.attendance", "search_read",
                        [[["calendar_id", "in", calendar_ids]]],
                        {"fields": ["calendar_id", "dayofweek", "hour_from", "hour_to"]}
                    )
                    for att in attendances or []:
                        cal_id = att["calendar_id"][0]
                        if cal_id not in att_map:
                            att_map[cal_id] = []
                        att_map[cal_id].append(att)
                except Exception as att_ex:
                    logger.warning(f"No se pudieron cargar attendances de calendarios {calendar_ids}: {att_ex}")
            
            day_names = {
                "0": "Lun", "1": "Mar", "2": "Mié", "3": "Jue", "4": "Vie", "5": "Sáb", "6": "Dom"
            }
            
            # Nombres de sistema que nunca deben aparecer como profesionales al cliente
            SKIP_NAMES = {"administrator", "admin", "colaboradora", "colaborador"}
            
            result = []
            for emp in employees or []:
                emp_name = (emp.get("name") or "").strip()
                # Excluir cuentas de sistema por nombre
                if emp_name.lower() in SKIP_NAMES:
                    continue
                specialty_ids = emp.get("spa_specialties", []) or []
                specialty_names = [prod_map[sid] for sid in specialty_ids if sid in prod_map]
                # Excluir empleados sin ninguna especialidad asignada (no son profesionales activos)
                if not specialty_names:
                    continue
                
                # Construir horario del profesional
                cal_val = emp.get("resource_calendar_id")
                schedule_parts = []
                if cal_val and isinstance(cal_val, list):
                    cal_id = cal_val[0]
                    emp_atts = att_map.get(cal_id, [])
                    # Ordenar por día de la semana y hora de inicio
                    emp_atts.sort(key=lambda x: (int(x.get("dayofweek", 0)), float(x.get("hour_from", 0))))
                    
                    # Agrupar por día para franjas múltiples
                    by_day = {}
                    for att in emp_atts:
                        day = att.get("dayofweek")
                        if day not in by_day:
                            by_day[day] = []
                        by_day[day].append(att)
                        
                    for day_num in sorted(by_day.keys(), key=int):
                        day_lbl = day_names.get(day_num, day_num)
                        ranges = []
                        for att in by_day[day_num]:
                            t_from = _format_attendance_hour(att.get("hour_from"))
                            t_to = _format_attendance_hour(att.get("hour_to"))
                            ranges.append(f"{t_from}-{t_to}")
                        schedule_parts.append(f"{day_lbl} {', '.join(ranges)}")
                
                schedule_text = "; ".join(schedule_parts) if schedule_parts else "Lun-Sáb 8:00am-7:00pm"
                
                result.append({
                    "id": emp.get("id"),
                    "name": emp_name,
                    "specialties": specialty_names,
                    "schedule": schedule_text
                })
                
            _PROFESSIONALS_CACHE[cache_key] = result
            _PROFESSIONALS_CACHE_TIME[cache_key] = now
            return result
        except Exception as e:
            logger.error(f"Error consultando profesionales con especialidades en Odoo: {e}")
            return []

    def get_recent_events(self, since_minutes: int = 2) -> list:
        """
        Devuelve calendar.event creados en los ultimos N minutos.
        Usado por el notification_job para detectar citas manuales en Odoo.
        """
        if not self.uid:
            return []
        try:
            now_utc = datetime.utcnow()
            since_utc = (now_utc - timedelta(minutes=since_minutes)).strftime("%Y-%m-%d %H:%M:%S")

            logger.info(f"OdooService.get_recent_events: buscando desde {since_utc} UTC (ventana={since_minutes}min)")

            events = self._execute(
                "calendar.event", "search_read",
                [[["create_date", ">=", since_utc], ["active", "=", True]]],
                {"fields": ["id", "name", "start", "partner_ids", "description"]},
            )
            logger.info(f"OdooService.get_recent_events: Odoo devolvió {len(events) if events else 0} eventos")
            if not events:
                return []

            result = []
            for ev in events:
                partner_ids = ev.get("partner_ids", [])
                ev["partner_name"] = "Cliente"
                ev["phone"] = ""
                if partner_ids:
                    partners = self._execute(
                        "res.partner", "read",
                        [partner_ids],
                        {"fields": ["name", "phone", "mobile"]},
                    )
                    if partners:
                        p = partners[0]
                        ev["partner_name"] = p.get("name", "Cliente")
                        ev["phone"] = (p.get("mobile") or p.get("phone") or "").replace(" ", "").replace("+", "")

                # Convertir start UTC → Bogota
                try:
                    start_dt = datetime.strptime(ev["start"], "%Y-%m-%d %H:%M:%S")
                    start_bta = start_dt - timedelta(hours=BOGOTA_UTC_OFFSET_HOURS)
                    ev["start_bogota"] = start_bta.strftime("%d/%m/%Y a las %I:%M %p")
                except Exception:
                    ev["start_bogota"] = ev.get("start", "")

                result.append(ev)
            return result
        except Exception as e:
            logger.error(f"Error consultando eventos recientes Odoo: {e}")
            return []
