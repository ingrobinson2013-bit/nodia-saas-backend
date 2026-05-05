# domain/ai_service.py
# Integración con OpenAI — responde con contexto de conversación
# 
# ARQUITECTURA:
# - ai_service SOLO genera texto. No crea citas directamente.
# - La creación de citas la maneja message_handler parseando el JSON {"action":"BOOK",...}
# - El tool calling se usa SOLO para check_availability (consultar disponibilidad)

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
    ) -> str:
        """
        Genera una respuesta de IA dado el mensaje del usuario y el historial.
        - Si odoo_config está presente, habilita la tool check_availability.
        - La confirmación de citas (BOOK) se emite como JSON en el texto y
          message_handler lo intercepta para guardar en Odoo y citas_log.
        """
        messages = [{"role": "system", "content": system_prompt or SYSTEM_PROMPT_DEFAULT}]

        # Mapear 'agent' → 'assistant' (OpenAI no acepta 'agent')
        ROLE_MAP = {"agent": "assistant", "user": "user", "assistant": "assistant"}
        for msg in history[-10:]:
            role = ROLE_MAP.get(msg.get("role", "user"), "user")
            content = msg.get("content", "")
            if content:
                messages.append({"role": role, "content": content})

        messages.append({"role": "user", "content": user_message})

        try:
            # ── Tool: check_availability (solo si tiene Odoo) ──────────
            if odoo_config and odoo_config.get("odoo_url"):
                tools = [
                    {
                        "type": "function",
                        "function": {
                            "name": "check_availability",
                            "description": "Consulta las horas ocupadas en Odoo para un día específico. Úsala cuando el cliente pregunte disponibilidad.",
                            "parameters": {
                                "type": "object",
                                "properties": {
                                    "date_str": {
                                        "type": "string",
                                        "description": "Fecha a consultar en formato YYYY-MM-DD"
                                    }
                                },
                                "required": ["date_str"]
                            }
                        }
                    }
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

                # Si el modelo decide consultar disponibilidad
                if response_message.tool_calls:
                    from domain.odoo_service import OdooService
                    odoo = OdooService(
                        url=odoo_config["odoo_url"],
                        db=odoo_config["odoo_db"],
                        user=odoo_config["odoo_user"],
                        api_key=odoo_config["odoo_api_key"],
                    )

                    messages.append(response_message)

                    for tool_call in response_message.tool_calls:
                        fn_name = tool_call.function.name
                        fn_args = json.loads(tool_call.function.arguments)

                        if fn_name == "check_availability":
                            result = odoo.check_availability(fn_args.get("date_str", ""))
                            tool_result = json.dumps(result, ensure_ascii=False)
                            logger.info(f"Odoo check_availability: {fn_args.get('date_str')} → {len(result)} eventos")
                        else:
                            tool_result = json.dumps({"error": "Función no reconocida"})

                        messages.append({
                            "tool_call_id": tool_call.id,
                            "role": "tool",
                            "name": fn_name,
                            "content": tool_result,
                        })

                    # Segunda llamada con el resultado de disponibilidad
                    second = await self.client.chat.completions.create(
                        model=self.model,
                        messages=messages,
                        max_tokens=600,
                        temperature=0.4,
                    )
                    return second.choices[0].message.content.strip()

                return response_message.content.strip()

            else:
                # ── Sin Odoo: llamada directa sin tools ───────────────
                response = await self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    max_tokens=600,
                    temperature=0.4,
                )
                return response.choices[0].message.content.strip()

        except Exception as e:
            logger.error(f"OpenAI error: {e}")
            return "En este momento tengo un problema técnico. Un asesor te contactará pronto. 🙏"
