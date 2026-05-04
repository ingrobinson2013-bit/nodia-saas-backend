# domain/ai_service.py
# Integración con OpenAI — responde con contexto de conversación

from openai import AsyncOpenAI
from config import settings
import logging

logger = logging.getLogger(__name__)

SYSTEM_PROMPT_DEFAULT = (
    "Eres un asistente de atención al cliente amable, profesional y conciso. "
    "Responde siempre en español colombiano. "
    "Si no puedes ayudar con algo, indica amablemente que un asesor humano le atenderá pronto."
)


class AIService:
    """
    Servicio de IA usando OpenAI.
    Usa la API Key global (en el futuro: BYOK por tenant).
    """

    def __init__(self, api_key: str = None, model: str = None):
        self.client = AsyncOpenAI(api_key=api_key or settings.OPENAI_API_KEY)
        self.model = model or settings.OPENAI_MODEL

    async def get_response(
        self,
        user_message: str,
        history: list[dict],
        system_prompt: str = None,
        odoo_config: dict = None,
    ) -> str:
        """
        Genera una respuesta de IA dado el mensaje del usuario y el historial.
        """
        messages = [{"role": "system", "content": system_prompt or SYSTEM_PROMPT_DEFAULT}]
        
        # Filtrar solo 'role' y 'content' para OpenAI
        clean_history = []
        for msg in history[-10:]:
            clean_msg = {"role": msg["role"], "content": msg["content"]}
            clean_history.append(clean_msg)
            
        messages.extend(clean_history)
        messages.append({"role": "user", "content": user_message})

        tools = [
            {
                "type": "function",
                "function": {
                    "name": "check_availability",
                    "description": "Consulta las horas ocupadas en Odoo para un día específico.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "date_str": {"type": "string", "description": "Fecha a consultar en formato YYYY-MM-DD"}
                        },
                        "required": ["date_str"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "create_appointment",
                    "description": "Agenda una cita en el calendario de Odoo.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "description": "Nombre del cliente"},
                            "phone": {"type": "string", "description": "Teléfono del cliente"},
                            "start_datetime": {"type": "string", "description": "Fecha y hora de inicio en UTC formato 'YYYY-MM-DD HH:MM:SS'"},
                            "duration_hours": {"type": "number", "description": "Duración de la cita en horas. Por defecto 1.0"}
                        },
                        "required": ["name", "phone", "start_datetime"]
                    }
                }
            }
        ]

        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=tools,
                tool_choice="auto",
                max_tokens=500,
                temperature=0.7,
            )
            
            response_message = response.choices[0].message
            
            # Si el modelo decide usar una herramienta (Odoo)
            if response_message.tool_calls:
                # Importar aquí para evitar circular dependency
                from domain.odoo_service import OdooService
                
                odoo = None
                if odoo_config and odoo_config.get("odoo_url"):
                    odoo = OdooService(
                        odoo_config["odoo_url"],
                        odoo_config["odoo_db"],
                        odoo_config["odoo_user"],
                        odoo_config["odoo_api_key"]
                    )
                
                messages.append(response_message)  # Agregar la llamada del asistente al historial
                
                for tool_call in response_message.tool_calls:
                    function_name = tool_call.function.name
                    import json
                    function_args = json.loads(tool_call.function.arguments)
                    
                    if not odoo:
                        function_response = '{"error": "Integración con Odoo no configurada o inactiva."}'
                    elif function_name == "check_availability":
                        res = odoo.check_availability(function_args.get("date_str"))
                        function_response = json.dumps(res)
                    elif function_name == "create_appointment":
                        res = odoo.create_appointment(
                            name=function_args.get("name"),
                            phone=function_args.get("phone"),
                            start_datetime=function_args.get("start_datetime"),
                            duration_hours=function_args.get("duration_hours", 1.0)
                        )
                        function_response = json.dumps({"success": res})
                    else:
                        function_response = '{"error": "Función desconocida"}'
                        
                    messages.append({
                        "tool_call_id": tool_call.id,
                        "role": "tool",
                        "name": function_name,
                        "content": function_response,
                    })
                
                # Segunda llamada a OpenAI con el resultado de Odoo
                second_response = await self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    max_tokens=500,
                )
                return second_response.choices[0].message.content.strip()
            
            return response_message.content.strip()

        except Exception as e:
            logger.error(f"OpenAI error: {e}")
            return "En este momento tengo un problema técnico. Un asesor te contactará pronto. 🙏"

