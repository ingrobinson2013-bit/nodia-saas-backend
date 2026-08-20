# domain/audio_service.py
# Servicio de transcripción de notas de voz usando OpenAI Whisper API

import io
import logging
from openai import AsyncOpenAI
from config import settings

logger = logging.getLogger(__name__)

# Prompt de contexto de dominio especializado para centros de belleza y barberías
BEAUTY_SPA_PROMPT = (
    "Salón de belleza, peluquería, barbería, corte caballero, corte dama, "
    "balayage, mechas, keratina, alisado, colorimetría, retoque de raíz, "
    "manicura semipermanente, manicura tradicional, pedicure, cepillado, "
    "citas, agendar cita, reprogramar cita, cancelar cita, horario, precio, "
    "Jose Roa, Paola Roa, Carolina Céspedes, Valentina Sanchez, Camilo, Bogotá, Rionegro."
)


class AudioTranscriptionService:
    """
    Servicio para transcribir notas de voz (audio/ogg, audio/mp3, audio/m4a, audio/wav)
    enviadas por clientes a través de WhatsApp Business API.
    """

    def __init__(self, api_key: str = None):
        self.client = AsyncOpenAI(api_key=api_key or settings.OPENAI_API_KEY)

    async def transcribe(
        self,
        audio_bytes: bytes,
        mime_type: str = "audio/ogg",
        prompt: str = None,
    ) -> str:
        """
        Transcribe los bytes de audio a texto en español utilizando Whisper-1.
        """
        if not audio_bytes or len(audio_bytes) < 100:
            logger.warning("Audio recibido vacío o demasiado pequeño para transcribir")
            return ""

        # Determinar la extensión del archivo según el mime_type
        extension = "ogg"
        if "mp3" in mime_type or "mpeg" in mime_type:
            extension = "mp3"
        elif "m4a" in mime_type or "mp4" in mime_type:
            extension = "m4a"
        elif "wav" in mime_type:
            extension = "wav"

        filename = f"voice_note.{extension}"

        # Crear archivo en memoria para la API de OpenAI
        audio_file = io.BytesIO(audio_bytes)
        audio_file.name = filename

        system_prompt = prompt or BEAUTY_SPA_PROMPT

        try:
            logger.info(f"🎙️ Transcribiendo audio con Whisper ({len(audio_bytes)} bytes, ext={extension})...")
            transcription = await self.client.audio.transcriptions.create(
                model="whisper-1",
                file=audio_file,
                language="es",
                prompt=system_prompt,
                temperature=0.0,
            )

            text = (transcription.text or "").strip()
            logger.info(f"✅ Transcripción Whisper exitosa: '{text}'")
            return text

        except Exception as e:
            logger.error(f"❌ Error transcribiendo audio con Whisper: {e}", exc_info=True)
            return ""
