# domain/memory_engine.py
# Motor de Memoria Híbrida y Consolidación de Preferencias — Clean Architecture

import logging
import json
from typing import Optional, Dict, Any
from infrastructure.repositories.client_memory_repo import ClientMemoryRepository

logger = logging.getLogger(__name__)


class MemoryEngine:
    """
    Motor de Memoria Híbrida (Cognitive Episodic Memory).
    Permite a la IA reconocer al cliente, recordar su barbero/estilista favorito,
    sus servicios frecuentes y hábitos de agendamiento para ofrecer una experiencia hiper-personalizada.
    """

    def __init__(self):
        self.repo = ClientMemoryRepository()

    def get_client_context(self, tenant_id: str, wa_from: str) -> str:
        """
        Extrae el perfil de memoria del cliente para inyectar en el System Prompt.
        Retorna un bloque de texto formateado listo para el LLM.
        """
        if not tenant_id or not wa_from:
            return ""
        try:
            mem = self.repo.get_by_tenant_and_phone(tenant_id, wa_from)
            if not mem or mem.get("total_citas_agendadas", 0) == 0:
                return ""

            nombre = mem.get("nombre_cliente") or ""
            prof = mem.get("profesional_favorito") or ""
            servicios = mem.get("servicios_frecuentes") or []
            if isinstance(servicios, str):
                try: servicios = json.loads(servicios)
                except Exception: servicios = []
            
            dias = mem.get("dias_preferidos") or []
            if isinstance(dias, str):
                try: dias = json.loads(dias)
                except Exception: dias = []

            horario = mem.get("horario_habitual") or ""
            total_citas = mem.get("total_citas_agendadas") or 1
            notas = mem.get("notas_estilo") or ""

            context_lines = [
                "\n[🧠 MEMORIA Y PREFERENCIAS DEL CLIENTE (APRENDIZAJE CONTINUO)]",
                f"- Tipo de Cliente: Cliente Recurrente VIP ({total_citas} citas previas)"
            ]
            if nombre:
                context_lines.append(f"- Nombre Reconocido: {nombre}")
            if prof:
                context_lines.append(f"- Profesional de Confianza / Favorito: {prof}")
            if servicios:
                context_lines.append(f"- Servicios Habituales: {', '.join(servicios)}")
            if dias:
                context_lines.append(f"- Días Preferidos: {', '.join(dias)}")
            if horario:
                context_lines.append(f"- Horario Habitual: {horario}")
            if notas:
                context_lines.append(f"- Notas Especiales de Estilo/Gusto: {notas}")

            context_lines.append(
                "*PAUTA DE ATENCIÓN*: Saluda cordialmente como cliente de la casa y, si pide cita abierta, "
                f"propón proactivamente atenderse con su profesional habitual ({prof if prof else 'de confianza'})."
            )
            context_lines.append("[FIN MEMORIA CLIENTE]\n")

            return "\n".join(context_lines)
        except Exception as e:
            logger.error(f"Error generando contexto de memoria de cliente: {e}")
            return ""

    def learn_from_appointment(
        self,
        tenant_id: str,
        wa_from: str,
        nombre: str = "",
        profesional: str = "",
        profesional_id: Optional[int] = None,
        servicio: str = "",
        fecha: str = "",
        hora: str = ""
    ) -> bool:
        """
        Registra el aprendizaje inmediato cuando una cita se confirma en Odoo.
        """
        if not tenant_id or not wa_from:
            return False
        return self.repo.record_appointment(
            tenant_id=tenant_id,
            wa_from=wa_from,
            nombre=nombre,
            profesional=profesional,
            profesional_id=profesional_id,
            servicio=servicio,
            fecha=fecha,
            hora=hora
        )


memory_engine = MemoryEngine()
