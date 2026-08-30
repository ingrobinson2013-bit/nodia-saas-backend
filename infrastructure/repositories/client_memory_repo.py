# infrastructure/repositories/client_memory_repo.py
# Repositorio de Memoria Híbrida y Preferencias de Clientes — 100% PostgreSQL Nativo

from typing import Optional, Dict, Any, List
import json
import logging
from infrastructure.database import fetch_one, fetch_all, execute_sql

logger = logging.getLogger(__name__)


class ClientMemoryRepository:
    """
    Repositorio para la tabla public.client_memory.
    Almacena las preferencias a largo plazo aprendidas de cada cliente:
        - Profesional favorito
        - Servicios frecuentes
        - Horarios y días habituales
        - Historial de citas completadas
        - Notas y estilo
    """

    def get_by_tenant_and_phone(self, tenant_id: str, wa_from: str) -> Optional[dict]:
        """Obtiene la memoria y perfil consolidado del cliente."""
        sql = "SELECT * FROM client_memory WHERE tenant_id = %s AND wa_from = %s LIMIT 1;"
        return fetch_one(sql, (tenant_id, wa_from))

    def record_appointment(
        self,
        tenant_id: str,
        wa_from: str,
        nombre: str = "",
        profesional: str = "",
        profesional_id: Optional[int] = None,
        servicio: str = "",
        fecha: str = "",
        hora: str = "",
    ) -> bool:
        """
        Consolida y aprende de una cita recién agendada:
        - Incrementa total de citas
        - Actualiza profesional favorito si es recurrente
        - Agrega servicio a la lista de frecuentes
        - Registra horario y día preferido
        """
        try:
            mem = self.get_by_tenant_and_phone(tenant_id, wa_from)
            
            # Calcular día de la semana si hay fecha
            dia_semana = ""
            if fecha:
                try:
                    from datetime import datetime
                    dt = datetime.strptime(fecha, "%Y-%m-%d")
                    dias_map = {0: "Lunes", 1: "Martes", 2: "Miércoles", 3: "Jueves", 4: "Viernes", 5: "Sábado", 6: "Domingo"}
                    dia_semana = dias_map.get(dt.weekday(), "")
                except Exception:
                    pass

            if not mem:
                # Primera cita: crear registro de memoria inicial
                servicios_list = [servicio] if servicio else []
                dias_list = [dia_semana] if dia_semana else []
                
                sql = """
                INSERT INTO client_memory (
                    tenant_id, wa_from, nombre_cliente, profesional_favorito, 
                    profesional_favorito_id, servicios_frecuentes, dias_preferidos,
                    horario_habitual, total_citas_agendadas, ultima_cita_fecha, updated_at
                ) VALUES (
                    %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, 1, %s, NOW()
                ) ON CONFLICT (tenant_id, wa_from) DO UPDATE SET
                    nombre_cliente = COALESCE(EXCLUDED.nombre_cliente, client_memory.nombre_cliente),
                    profesional_favorito = COALESCE(EXCLUDED.profesional_favorito, client_memory.profesional_favorito),
                    total_citas_agendadas = client_memory.total_citas_agendadas + 1,
                    ultima_cita_fecha = EXCLUDED.ultima_cita_fecha,
                    updated_at = NOW();
                """
                return execute_sql(
                    sql,
                    (
                        tenant_id, wa_from, nombre, profesional, profesional_id,
                        json.dumps(servicios_list), json.dumps(dias_list),
                        hora, fecha
                    )
                )

            # Cliente existente: enriquecer historial
            current_services = mem.get("servicios_frecuentes") or []
            if isinstance(current_services, str):
                try: current_services = json.loads(current_services)
                except Exception: current_services = []
            
            if servicio and servicio not in current_services:
                current_services.append(servicio)
                if len(current_services) > 5:
                    current_services.pop(0)

            current_days = mem.get("dias_preferidos") or []
            if isinstance(current_days, str):
                try: current_days = json.loads(current_days)
                except Exception: current_days = []
            
            if dia_semana and dia_semana not in current_days:
                current_days.append(dia_semana)
                if len(current_days) > 3:
                    current_days.pop(0)

            sql_update = """
            UPDATE client_memory SET
                nombre_cliente = COALESCE(NULLIF(%s, ''), nombre_cliente),
                profesional_favorito = COALESCE(NULLIF(%s, ''), profesional_favorito),
                profesional_favorito_id = COALESCE(%s, profesional_favorito_id),
                servicios_frecuentes = %s::jsonb,
                dias_preferidos = %s::jsonb,
                horario_habitual = COALESCE(NULLIF(%s, ''), horario_habitual),
                total_citas_agendadas = total_citas_agendadas + 1,
                ultima_cita_fecha = %s,
                updated_at = NOW()
            WHERE tenant_id = %s AND wa_from = %s;
            """
            return execute_sql(
                sql_update,
                (
                    nombre, profesional, profesional_id,
                    json.dumps(current_services), json.dumps(dias_days if 'dias_days' in locals() else current_days),
                    hora, fecha,
                    tenant_id, wa_from
                )
            )
        except Exception as e:
            logger.error(f"Error registrando memoria de cliente: {e}")
            return False

    def update_style_notes(self, tenant_id: str, wa_from: str, notas: str) -> bool:
        """Actualiza notas especiales de corte/estilo del cliente."""
        sql = """
        INSERT INTO client_memory (tenant_id, wa_from, notas_estilo, updated_at)
        VALUES (%s, %s, %s, NOW())
        ON CONFLICT (tenant_id, wa_from) DO UPDATE SET
            notas_estilo = EXCLUDED.notas_estilo,
            updated_at = NOW();
        """
        return execute_sql(sql, (tenant_id, wa_from, notas))
