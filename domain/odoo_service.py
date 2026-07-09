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
        self._authenticate()

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
            try:
                fields = self._execute("calendar.event", "fields_get", [["professional_id"]], {"attributes": ["type"]})
                if "professional_id" in fields:
                    self.professional_field_name = "professional_id"
            except Exception:
                try:
                    fields = self._execute("calendar.event", "fields_get", [["spa_professional_id"]], {"attributes": ["type"]})
                    if "spa_professional_id" in fields:
                        self.professional_field_name = "spa_professional_id"
                except Exception as e_field:
                    logger.warning(f"No se pudo detectar el campo de profesional en calendar.event (falló professional_id y spa_professional_id): {e_field}")
                    self.professional_field_name = None
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
            # 1. Calcular duración real del servicio
            dur_min = _duration_from_service(service_name, negocio_servicios)
            dur_hours = dur_min / 60.0

            # 2. Calcular hora de fin en Bogotá
            h, m = map(int, time_str.split(":"))
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
                f"Reserva WhatsApp | Tel: {phone} | Origen: AgenteIA VALE{precio_desc}{profesional_desc}"
            )
            if description:
                desc_full += f"\n{description}"

            # 6. Buscar ID del profesional
            professional_id = None
            if professional_name and professional_name.lower() != "cualquiera":
                pro_list = self.get_professionals()
                for p in pro_list:
                    if p.get("name", "").lower() == professional_name.lower():
                        professional_id = p.get("id")
                        break

            event_data = {
                "name": event_name,
                "start": start_utc,
                "stop": stop_utc,
                "duration": dur_hours,
                "description": desc_full,
                "booking_state": "confirmed",
            }
            if partner_id:
                event_data["partner_ids"] = [(4, partner_id)]
            if professional_id and self.professional_field_name:
                event_data[self.professional_field_name] = professional_id

            try:
                event_id = self._execute("calendar.event", "create", [event_data])
            except Exception as e:
                if "booking_state" in str(e) and "booking_state" in event_data:
                    logger.warning("Odoo: 'booking_state' no es un campo válido en calendar.event. Reintentando creación sin él...")
                    del event_data["booking_state"]
                    try:
                        event_id = self._execute("calendar.event", "create", [event_data])
                    except Exception as retry_err:
                        logger.error(f"❌ Error al reintentar creación en Odoo sin booking_state: {retry_err}")
                        return None
                else:
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

    def cancel_appointment(self, event_id: int) -> bool:
        """Archiva (cancela) una cita en Odoo estableciendo active=False."""
        if not self.uid or not event_id:
            return False
        try:
            self._execute("calendar.event", "write", [[event_id], {"active": False}])
            logger.info(f"Odoo: cita {event_id} cancelada (active=False)")
            return True
        except Exception as e:
            logger.error(f"Error cancelando cita {event_id}: {e}")
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
            h, m = map(int, time_str.split(":"))
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

    def check_availability(self, date_str: str) -> list:
        """
        Devuelve los eventos del negocio para un día (en UTC).
        date_str en formato YYYY-MM-DD (hora Bogotá).
        Convierte el rango a UTC antes de consultar.
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

            events = self._execute(
                "calendar.event", "search_read",
                [[["start", ">=", start_utc], ["start", "<=", end_utc], ["active", "=", True]]],
                {"fields": fields_to_read},
            )

            # Normalizar para que el resto del sistema que asume 'professional_id' siga funcionando
            if events and self.professional_field_name and self.professional_field_name != "professional_id":
                for ev in events:
                    if self.professional_field_name in ev:
                        ev["professional_id"] = ev[self.professional_field_name]

            return events or []
        except Exception as e:
            logger.error(f"Error consultando disponibilidad Odoo: {e}")
            return []

    def get_client_appointments(self, partner_id: int) -> list:
        """Devuelve las citas futuras de un cliente específico."""
        if not self.uid or not partner_id:
            return []
        try:
            today_utc = _bogota_to_utc(date.today().strftime("%Y-%m-%d"), "00:00")
            events = self._execute(
                "calendar.event", "search_read",
                [[["partner_ids", "in", [partner_id]], ["start", ">=", today_utc], ["active", "=", True]]],
                {"fields": ["name", "start", "stop", "duration"]},
            )
            return events or []
        except Exception as e:
            logger.error(f"Error consultando citas cliente {partner_id}: {e}")
            return []

    def get_professionals(self) -> list:
        """Devuelve los profesionales activos en Odoo (con caché simple)."""
        if not self.uid:
            return []
        
        global _PROFESSIONALS_CACHE, _PROFESSIONALS_CACHE_TIME
        if '_PROFESSIONALS_CACHE' not in globals():
            _PROFESSIONALS_CACHE = {}
            _PROFESSIONALS_CACHE_TIME = {}
            
        now = datetime.now()
        cache_key = f"{self.url}_{self.db}"
        
        if cache_key in _PROFESSIONALS_CACHE and cache_key in _PROFESSIONALS_CACHE_TIME:
            if (now - _PROFESSIONALS_CACHE_TIME[cache_key]).total_seconds() < 3600:
                return _PROFESSIONALS_CACHE[cache_key]

        try:
            # Buscar en hr.employee o en res.users segun lo que use el modulo.
            # En Odoo normalmente los profesionales de calendar son empleados o usuarios.
            # Asumiremos hr.employee ya que el field inspeccionado es hr.employee.
            employees = self._execute(
                "hr.employee", "search_read",
                [[["active", "=", True]]],
                {"fields": ["id", "name"]}
            )
            result = employees or []
            _PROFESSIONALS_CACHE[cache_key] = result
            _PROFESSIONALS_CACHE_TIME[cache_key] = now
            return result
        except Exception as e:
            logger.error(f"Error consultando profesionales en Odoo: {e}")
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
