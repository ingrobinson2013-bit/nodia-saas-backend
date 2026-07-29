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


TOOL_GET_MY_APPOINTMENTS = {
    "type": "function",
    "function": {
        "name": "get_my_appointments",
        "description": (
            "Consulta las citas activas o pendientes del cliente en Odoo utilizando su número telefónico. "
            "DEBES llamar esta función cuando el cliente pida cancelar, eliminar, anular, reprogramar o ver sus citas "
            "(ej: 'quiero cancelar', 'cancelar cita', 'mis citas', 'eliminar cita', 'no puedo ir', 'reprogramar')."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "required": []
        }
    }
}

TOOL_CANCEL_APPOINTMENT = {
    "type": "function",
    "function": {
        "name": "cancel_appointment",
        "description": (
            "Cancela una cita específica del cliente en Odoo mediante el ID numérico de la cita (cita_id). "
            "Llama esta función cuando el cliente pida cancelar una cita específica o seleccione la cita a cancelar."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "cita_id": {
                    "type": "integer",
                    "description": "ID numérico de la cita en Odoo (ej: 1805)"
                }
            },
            "required": ["cita_id"]
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
        tenant_id: str = None,
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
                tools = [
                    TOOL_CHECK_AVAILABILITY,
                    TOOL_CREATE_APPOINTMENT,
                    TOOL_GET_MY_APPOINTMENTS,
                    TOOL_CANCEL_APPOINTMENT,
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
                        fn_args = json.loads(tool_call.function.arguments or "{}")

                        if fn_name == "check_availability":
                            result = odoo.check_availability(fn_args.get("date_str", ""))
                            # También consultar slots libres del endpoint spa si están disponibles
                            slots = odoo.get_available_slots(fn_args.get("date_str", ""))
                            tool_result = json.dumps({"eventos_ocupados": result, "slots_disponibles": slots}, ensure_ascii=False)
                            logger.info(f"Odoo check_availability: {fn_args.get('date_str')} → {len(result)} eventos")

                        elif fn_name == "get_my_appointments":
                            client_phone = sender_wa_id or fn_args.get("phone") or ""
                            res = odoo.get_client_appointments(client_phone)
                            if not isinstance(res, dict):
                                res = {"success": True, "citas": res if isinstance(res, list) else [], "total": len(res) if isinstance(res, list) else 0}
                            tool_result = json.dumps(res, ensure_ascii=False)
                            total_cnt = res.get("total", len(res.get("citas", [])))
                            logger.info(f"Odoo get_my_appointments para {client_phone} → {total_cnt} citas")

                        elif fn_name == "cancel_appointment":
                            cita_id = fn_args.get("cita_id")
                            res = odoo.cancel_appointment_spa(cita_id, sender_wa_id or "")
                            if not isinstance(res, dict):
                                res = {"success": True, "message": str(res)}
                            
                            # ✅ Sincronizar cancelación en Supabase citas_log
                            if res.get("success") and tenant_id:
                                try:
                                    from infrastructure.repositories.tenant_repo import get_supabase_client
                                    sb = get_supabase_client()
                                    if cita_id:
                                        sb.table("citas_log").update({"estado": "cancelada"}).eq("tenant_id", tenant_id).eq("odoo_event_id", int(cita_id)).execute()
                                    if sender_wa_id:
                                        sb.table("citas_log").update({"estado": "cancelada"}).eq("tenant_id", tenant_id).eq("wa_from", sender_wa_id).execute()
                                    logger.info(f"Supabase citas_log sincronizado: estado='cancelada' para {sender_wa_id} cita_id={cita_id}")
                                except Exception as se:
                                    logger.warning(f"No se pudo actualizar citas_log en Supabase al cancelar: {se}")

                            tool_result = json.dumps(res, ensure_ascii=False)
                            logger.info(f"Odoo cancel_appointment cita_id={cita_id} para {sender_wa_id} → {res.get('success')}")

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
                                description="Beautysync - Agendamiento",
                                professional_name=fn_args.get("profesional_nombre", ""),
                            )
                            if event_id == "PAST_DATE_TIME":
                                tool_result = json.dumps({
                                    "success": False,
                                    "error_code": "PAST_DATE_TIME",
                                    "message": "Lo siento, no es posible agendar en una fecha u hora que ya pasó. Por favor elige una fecha y hora futura."
                                }, ensure_ascii=False)
                            elif event_id:
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
                    final_text = second.choices[0].message.content.strip()

                    # Safety check: Si el texto afirma cancelación pero no se llamó cancel_appointment
                    if "cancelada" in final_text.lower() and not any(tc.function.name == "cancel_appointment" for tc in response_message.tool_calls):
                        logger.warning(f"⚠️ GPT afirmó cancelación en texto sin haber llamado la tool cancel_appointment. Ejecutando cancelación de seguridad para {sender_wa_id}...")
                        try:
                            citas_res = odoo.get_client_appointments(sender_wa_id or "")
                            c_list = citas_res.get("citas", []) if isinstance(citas_res, dict) else []
                            for c in c_list:
                                cid = c.get("id")
                                if cid:
                                    odoo.cancel_appointment_spa(cid, sender_wa_id or "")
                                    if tenant_id:
                                        try:
                                            from infrastructure.repositories.tenant_repo import get_supabase_client
                                            sb = get_supabase_client()
                                            sb.table("citas_log").update({"estado": "cancelada"}).eq("tenant_id", tenant_id).eq("odoo_event_id", int(cid)).execute()
                                            sb.table("citas_log").update({"estado": "cancelada"}).eq("tenant_id", tenant_id).eq("wa_from", sender_wa_id).execute()
                                        except Exception:
                                            pass
                        except Exception as fe:
                            logger.error(f"Error en safety cancel: {fe}")

                    return final_text, booking_data

                final_text = response_message.content.strip() if response_message.content else ""
                if "cancelada" in final_text.lower() and sender_wa_id:
                    logger.warning(f"⚠️ GPT afirmó cancelación sin tool calls. Ejecutando cancelación de seguridad para {sender_wa_id}...")
                    try:
                        citas_res = odoo.get_client_appointments(sender_wa_id or "")
                        c_list = citas_res.get("citas", []) if isinstance(citas_res, dict) else []
                        for c in c_list:
                            cid = c.get("id")
                            if cid:
                                odoo.cancel_appointment_spa(cid, sender_wa_id or "")
                                if tenant_id:
                                    try:
                                        from infrastructure.repositories.tenant_repo import get_supabase_client
                                        sb = get_supabase_client()
                                        sb.table("citas_log").update({"estado": "cancelada"}).eq("tenant_id", tenant_id).eq("odoo_event_id", int(cid)).execute()
                                        sb.table("citas_log").update({"estado": "cancelada"}).eq("tenant_id", tenant_id).eq("wa_from", sender_wa_id).execute()
                                    except Exception:
                                        pass
                    except Exception as fe:
                        logger.error(f"Error en safety cancel: {fe}")

                return final_text, None

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

    async def extract_lead_details(self, history: list) -> dict:
        """
        Analiza el historial de conversación y extrae detalles estructurados del prospecto usando GPT.
        """
        if not history:
            return {}

        # Formatear el historial en texto plano
        history_text = ""
        for msg in history:
            role_label = "Cliente" if msg.get("role") == "user" else "Asesora (VALE)"
            history_text += f"{role_label}: {msg.get('content')}\n"

        prompt = (
            "Eres un extractor de datos analítico y preciso. Tu tarea es analizar el historial de conversación "
            "de un bot de ventas de WhatsApp y extraer la siguiente información del prospecto en formato JSON. "
            "Sé preciso. Si no encuentras la información de algún campo, devuélvelo como null (o un string vacío).\n\n"
            "Campos a extraer:\n"
            "1. nombre: Nombre de la persona / prospecto.\n"
            "2. negocio: Nombre del negocio, estética, peluquería o barbería.\n"
            "3. tipo_negocio: Tipo de negocio (Barbería, Peluquería, Salón de Belleza, Spa, Estética, etc.).\n"
            "4. inversion_insumos: Inversión aproximada mensual en insumos/productos en COP.\n"
            "5. plan_interes: Plan seleccionado o de interés (ej: 'Básico', 'Pro', 'Demo').\n"
            "6. email: Correo electrónico proporcionado.\n"
            "7. telefono: Número de contacto si lo menciona diferente al de origen.\n"
            "8. nit_rut: NIT o RUT si lo menciona.\n"
            "9. resumen_interes: Un resumen de una frase sobre qué le interesa al cliente.\n\n"
            "Responde ÚNICAMENTE con el objeto JSON válido. El formato del JSON debe ser exactamente:\n"
            "{\n"
            "  \"nombre\": \"...\",\n"
            "  \"negocio\": \"...\",\n"
            "  \"tipo_negocio\": \"...\",\n"
            "  \"inversion_insumos\": \"...\",\n"
            "  \"plan_interes\": \"...\",\n"
            "  \"email\": \"...\",\n"
            "  \"telefono\": \"...\",\n"
            "  \"nit_rut\": \"...\",\n"
            "  \"resumen_interes\": \"...\"\n"
            "}"
        )

        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": f"Historial de conversación:\n{history_text}"}
                ],
                max_tokens=300,
                temperature=0.0,
                response_format={"type": "json_object"}
            )
            raw_json = response.choices[0].message.content.strip()
            return json.loads(raw_json)
        except Exception as e:
            logger.error(f"Error al extraer detalles del lead con OpenAI: {e}")
            return {}

