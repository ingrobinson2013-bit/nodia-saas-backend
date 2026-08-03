# main.py
# NODIA SaaS Backend — Entry point FastAPI

import asyncio
import logging
import uvicorn
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from api.webhook import router as webhook_router
from api.send_message import router as send_message_router
from api.meta_connect import router as meta_connect_router
from config import settings
from domain.notification_job import run_notification_job

# ── Logging ──────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
# DEBUG solo para orquestador para ver el response completo de GPT
logging.getLogger("domain.message_handler").setLevel(logging.DEBUG)
logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Inicia el job de notificaciones Odoo→WhatsApp en background."""
    task = asyncio.create_task(run_notification_job())
    logger.info("NotificationJob: background task iniciado")
    yield
    task.cancel()
    logger.info("NotificationJob: background task detenido")


# ── App ───────────────────────────────────────────────────
app = FastAPI(
    title="NODIA WhatsApp AI SaaS",
    description="Backend multi-tenant para agentes IA en WhatsApp",
    version="1.4.0",
    docs_url="/docs" if settings.ENVIRONMENT == "development" else None,
    lifespan=lifespan,
)

# ── CORS (permitir panel Next.js) ─────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # Permitir todos para evitar errores si cambia el puerto local
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from api.odoo_webhook import router as odoo_webhook_router
from api.templates import router as templates_router
from api.campaigns import router as campaigns_router

# ── Routers ───────────────────────────────────────────────
app.include_router(webhook_router, tags=["WhatsApp Webhook"])
app.include_router(send_message_router, prefix="/api", tags=["Panel Agente"])
app.include_router(meta_connect_router, prefix="/api", tags=["Meta Onboarding"])
app.include_router(odoo_webhook_router, prefix="/api", tags=["Odoo Webhooks"])
app.include_router(templates_router, prefix="/api", tags=["WhatsApp Templates"])
app.include_router(campaigns_router, prefix="/api", tags=["WhatsApp Campaigns"])


# ── Health check ──────────────────────────────────────────
@app.get("/health", tags=["Sistema"])
async def health():
    return {"status": "ok", "service": "nodia-saas-backend", "version": "1.4.0"}

@app.get("/debug/openai", tags=["Sistema"])
async def debug_openai():
    from domain.ai_service import AIService
    ai = AIService()
    try:
        await ai.client.models.list()
        return {"status": "ok", "openai_key_valid": True, "model": ai.model}
    except Exception as e:
        return {"status": "error", "openai_key_valid": False, "error": str(e)}


# ── Run ───────────────────────────────────────────────────
if __name__ == "__main__":
    logger.info(f"🚀 NODIA Backend iniciando en puerto {settings.PORT}")
    uvicorn.run("main:app", host="0.0.0.0", port=settings.PORT, reload=True)
