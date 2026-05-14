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

logger = logging.getLogger(__name__)

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
            "Consulta las citas ocupadas en el calendario del negocio para una fecha específica. "
            "Llama esta función cuando el cliente proponga una fecha/hora para verificar disponibilidad. "
            "Retorna lista de eventos con start/stop."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "date_str": {
                    "type": "string",
                    "description": "Fecha a consultar en formato YYYY-MM-DD (hora Bogotá)"
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
                tools = [TOOL_CHECK_AVAILABILITY, TOOL_CREATE_APPOINTMENT]

                response = await self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    tools=tools,
                    tool_choice="auto",
                    max_tokens=600,
                    temperature=0.4,
                )

                response_message = response.choices[0].message

                # ── Ciclo de tool calling ──────────────────────────────────
                if response_message.tool_calls:
                    from domain.odoo_service import OdooService
                    odoo = OdooService(
                        url=odoo_config["url"],
                        db=odoo_config["db"],
                        user=odoo_config["user"],
                        api_key=odoo_config["api_key"],
                    )

                    messages.append(response_message)
                    booking_data = None  # resultado de create_appointment si aplica

                    for tool_call in response_message.tool_calls:
                        fn_name = tool_call.function.name
                        fn_args = json.loads(tool_call.function.arguments)

                        if fn_name == "check_availability":
                            result = odoo.check_availability(fn_args.get("date_str", ""))
                            tool_result = json.dumps(result, ensure_ascii=False)
                            logger.info(f"Odoo check_availability: {fn_args.get('date_str')} → {len(result)} eventos")

                        elif fn_name == "create_appointment":
                            # ✅ Crear la cita en Odoo directamente desde ai_service
                            event_id = odoo.create_appointment(
                                name=fn_args.get("cliente_nombre", sender_name or sender_wa_id or "Cliente"),
                                phone=sender_wa_id or "",
                                date_str=fn_args.get("fecha", ""),
                                time_str=fn_args.get("hora", "00:00"),
                                service_name=fn_args.get("servicio", ""),
                                price=fn_args.get("precio", ""),
                                negocio_servicios=negocio_servicios,
                                description="Agendado via WhatsApp Bot NODIA",
                                professional_name=fn_args.get("profesional_nombre", ""),
                            )
                            if event_id:
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

                    # Segunda llamada: GPT genera el mensaje final para el cliente
                    second = await self.client.chat.completions.create(
                        model=self.model,
                        messages=messages,
                        max_tokens=600,
                        temperature=0.4,
                    )
                    return second.choices[0].message.content.strip(), booking_data

                return response_message.content.strip(), None

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
