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
    ) -> str:
        """
        Genera una respuesta de IA dado el mensaje del usuario y el historial.

        Args:
            user_message: Texto del mensaje entrante.
            history: Lista de mensajes previos [{"role": "user/assistant", "content": "..."}].
            system_prompt: Prompt personalizado del tenant.

        Returns:
            Respuesta de texto del modelo.
        """
        messages = [
            {"role": "system", "content": system_prompt or SYSTEM_PROMPT_DEFAULT}
        ]

        # Incluir historial (máx últimos 10 turnos para controlar tokens)
        messages.extend(history[-10:])
        messages.append({"role": "user", "content": user_message})

        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                max_tokens=500,
                temperature=0.7,
            )
            return response.choices[0].message.content.strip()

        except Exception as e:
            logger.error(f"OpenAI error: {e}")
            return "En este momento tengo un problema técnico. Un asesor te contactará pronto. 🙏"
