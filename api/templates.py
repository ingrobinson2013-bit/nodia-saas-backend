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
