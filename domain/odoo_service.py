# domain/odoo_service.py
# Servicio de conexión a Odoo via XML-RPC
import xmlrpc.client
import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

class OdooService:
    def __init__(self, url: str, db: str, user: str, api_key: str):
        self.url = url.rstrip('/')
        self.db = db
        self.user = user
        self.api_key = api_key
        
        try:
            self.common = xmlrpc.client.ServerProxy(f"{self.url}/xmlrpc/2/common")
            self.uid = self.common.authenticate(self.db, self.user, self.api_key, {})
            if not self.uid:
                raise Exception("Fallo en autenticación con Odoo. Verifica las credenciales.")
            
            self.models = xmlrpc.client.ServerProxy(f"{self.url}/xmlrpc/2/object")
            logger.info(f"Conexión a Odoo establecida correctamente para BD: {self.db}")
        except Exception as e:
            logger.error(f"Error conectando a Odoo: {str(e)}")
            self.uid = None

    def search_partner(self, phone: str, name: str = None) -> int:
        """Busca un cliente por teléfono. Si no existe, lo crea."""
        if not self.uid: return None
        
        # Odoo guarda los teléfonos en distintos formatos. Hacemos búsqueda parcial si es posible.
        partner_ids = self.models.execute_kw(self.db, self.uid, self.api_key, 'res.partner', 'search', [[['phone', 'ilike', phone[-10:]]]])
        
        if partner_ids:
            return partner_ids[0]
        
        # Si no existe, crear
        new_partner = {
            'name': name or f"Cliente WhatsApp {phone}",
            'phone': phone,
        }
        partner_id = self.models.execute_kw(self.db, self.uid, self.api_key, 'res.partner', 'create', [new_partner])
        return partner_id

    def check_availability(self, date_str: str) -> list:
        """
        Devuelve las horas ocupadas en un día específico.
        date_str en formato YYYY-MM-DD
        """
        if not self.uid: return []
        
        try:
            start_date = f"{date_str} 00:00:00"
            end_date = f"{date_str} 23:59:59"
            
            events = self.models.execute_kw(
                self.db, self.uid, self.api_key, 
                'calendar.event', 'search_read', 
                [[['start', '>=', start_date], ['start', '<=', end_date]]],
                {'fields': ['name', 'start', 'stop', 'duration']}
            )
            
            return events
        except Exception as e:
            logger.error(f"Error consultando disponibilidad en Odoo: {e}")
            return []

    def create_appointment(self, name: str, phone: str, start_datetime: str, duration_hours: float = 1.0, description: str = "") -> bool:
        """
        Crea una cita (calendar.event) en Odoo.
        start_datetime debe estar en UTC "YYYY-MM-DD HH:MM:SS"
        """
        if not self.uid: return False
        
        try:
            partner_id = self.search_partner(phone, name)
            
            # Calcular fin de la cita
            start_dt = datetime.strptime(start_datetime, "%Y-%m-%d %H:%M:%S")
            stop_dt = start_dt + timedelta(hours=duration_hours)
            stop_datetime = stop_dt.strftime("%Y-%m-%d %H:%M:%S")
            
            event_data = {
                'name': f"Cita: {name} - {description}",
                'start': start_datetime,
                'stop': stop_datetime,
                'duration': duration_hours,
                'partner_ids': [(4, partner_id)] if partner_id else [],
                'description': f"Agendado via WhatsApp Bot.\nTeléfono: {phone}\n{description}"
            }
            
            event_id = self.models.execute_kw(self.db, self.uid, self.api_key, 'calendar.event', 'create', [event_data])
            return bool(event_id)
        except Exception as e:
            logger.error(f"Error creando cita en Odoo: {e}")
            return False
