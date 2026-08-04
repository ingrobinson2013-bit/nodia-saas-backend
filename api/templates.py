# api/templates.py
# Gestión de Plantillas WhatsApp via Meta Graph API
# Permite crear y listar templates directamente desde el panel NODIA

import logging
import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from infrastructure.repositories.tenant_repo import TenantRepository


router = APIRouter()
logger = logging.getLogger(__name__)
tenant_repo = TenantRepository()

GRAPH_URL = "https://graph.facebook.com/v19.0"


async def _resolve_waba_id(phone_number_id: str, access_token: str) -> str | None:
    """
    Extrae el WABA ID automaticamente desde Meta usando el phone_number_id.
    GET /v19.0/{phone_number_id}?fields=whatsapp_business_account
    """
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{GRAPH_URL}/{phone_number_id}",
                params={
                    "fields": "whatsapp_business_account",
                    "access_token": access_token,
                }
            )
        data = resp.json()
        waba = data.get("whatsapp_business_account", {})
        waba_id = waba.get("id")
        if waba_id:
            logger.info(f"WABA ID resuelto automaticamente: {waba_id}")
        return waba_id
    except Exception as e:
        logger.warning(f"No se pudo resolver WABA ID: {e}")
        return None


@router.get("/templates/resolve-waba/{tenant_id}")
async def resolve_waba(tenant_id: str):
    """
    Extrae y guarda automaticamente el WABA ID del tenant desde Meta.
    Llamar una vez para autoconfigurar el tenant sin entrada manual.
    """
    tenant = tenant_repo.get_by_id(tenant_id)
    if not tenant or not tenant.get("activo"):
        raise HTTPException(status_code=404, detail="Tenant no encontrado")

    phone_id = tenant.get("wa_phone_id")
    token    = tenant.get("wa_access_token")

    if not phone_id or not token:
        raise HTTPException(status_code=400, detail="wa_phone_id o wa_access_token no configurados")

    waba_id = await _resolve_waba_id(phone_id, token)
    if not waba_id:
        raise HTTPException(status_code=400, detail="No se pudo obtener WABA ID de Meta")

    # Guardar en Supabase usando el repositorio
    tenant_repo.update_waba_id(tenant_id, waba_id)
    logger.info(f"[{tenant.get('nombre')}] waba_id={waba_id} guardado automaticamente")

    return {"waba_id": waba_id, "message": "WABA ID resuelto y guardado automaticamente"}


class TemplateComponent(BaseModel):
    type: str           # HEADER | BODY | FOOTER
    text: str
    example: dict = {}  # {"body_text": [["var1", "var2"]]}


class CreateTemplateRequest(BaseModel):
    tenant_id: str
    name: str            # ej: "cita_confirmada"
    category: str        # UTILITY | MARKETING | AUTHENTICATION
    language: str = "es"
    body: str            # texto del cuerpo con {{1}} {{2}} etc.
    header: str = ""     # texto del encabezado (opcional)
    footer: str = ""     # texto del pie (opcional)
    body_example: list[str] = []  # valores de ejemplo para las variables


@router.post("/templates/create")
async def create_template(req: CreateTemplateRequest):
    """
    Crea una plantilla de WhatsApp via Meta Graph API.
    Requiere que el tenant tenga waba_id configurado.
    """
    tenant = tenant_repo.get_by_id(req.tenant_id)
    if not tenant or not tenant.get("activo"):
        raise HTTPException(status_code=404, detail="Tenant no encontrado")

    waba_id = tenant.get("waba_id")
    access_token = tenant.get("wa_access_token")

    if not waba_id:
        raise HTTPException(
            status_code=400,
            detail="El tenant no tiene waba_id configurado. Agréga el WABA ID en la configuración."
        )
    if not access_token:
        raise HTTPException(status_code=400, detail="Access token de WhatsApp no configurado")

    # Construir componentes del template
    components = []

    if req.header:
        components.append({
            "type": "HEADER",
            "format": "TEXT",
            "text": req.header,
        })

    # Cuerpo con ejemplos de variables
    body_component: dict = {"type": "BODY", "text": req.body}
    if req.body_example:
        body_component["example"] = {"body_text": [req.body_example]}
    components.append(body_component)

    if req.footer:
        components.append({"type": "FOOTER", "text": req.footer})

    payload = {
        "name":       req.name.lower().replace(" ", "_"),
        "category":   req.category.upper(),
        "language":   req.language,
        "components": components,
    }

    logger.info(f"[{tenant['nombre']}] Creando template '{req.name}' en WABA {waba_id}")

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                f"{GRAPH_URL}/{waba_id}/message_templates",
                json=payload,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                }
            )

        result = response.json()

        if response.status_code not in (200, 201):
            error_msg = result.get("error", {}).get("message", "Error desconocido de Meta")
            logger.error(f"Meta API error: {result}")
            raise HTTPException(status_code=400, detail=f"Meta rechazó el template: {error_msg}")

        logger.info(f"[{tenant['nombre']}] Template '{req.name}' enviado a revisión: {result}")
        return {
            "status": "pending",
            "message": "Plantilla enviada a Meta para revisión (24-48h)",
            "template_id": result.get("id"),
            "name": req.name,
        }

    except httpx.HTTPError as e:
        logger.error(f"Error HTTP al crear template: {e}")
        raise HTTPException(status_code=500, detail=f"Error conectando con Meta: {str(e)}")


@router.get("/templates/list/{tenant_id}")
async def list_templates(tenant_id: str):
    """
    Lista todas las plantillas del WABA del tenant con su estado.
    Estados: APPROVED | PENDING | REJECTED | PAUSED
    """
    tenant = tenant_repo.get_by_id(tenant_id)
    if not tenant or not tenant.get("activo"):
        raise HTTPException(status_code=404, detail="Tenant no encontrado")

    waba_id = tenant.get("waba_id")
    access_token = tenant.get("wa_access_token")

    if not waba_id:
        return {"templates": [], "message": "WABA ID no configurado"}

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(
                f"{GRAPH_URL}/{waba_id}/message_templates",
                params={
                    "fields": "id,name,status,language,category,components",
                    "limit": 50,
                },
                headers={"Authorization": f"Bearer {access_token}"}
            )

        result = response.json()

        if response.status_code != 200:
            raise HTTPException(status_code=400, detail=result.get("error", {}).get("message", "Error Meta"))

        templates = result.get("data", [])
        logger.info(f"[{tenant['nombre']}] {len(templates)} templates listadas")
        return {"templates": templates, "total": len(templates)}

    except httpx.HTTPError as e:
        raise HTTPException(status_code=500, detail=f"Error conectando con Meta: {str(e)}")
