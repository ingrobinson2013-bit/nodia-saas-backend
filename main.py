# main.py
# NODIA SaaS Backend — Entry point FastAPI

import logging
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from api.webhook import router as webhook_router
from api.send_message import router as send_message_router
from api.meta_connect import router as meta_connect_router
from config import settings

# ── Logging ──────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
# DEBUG solo para orquestador para ver el response completo de GPT
logging.getLogger("domain.message_handler").setLevel(logging.DEBUG)
logger = logging.getLogger(__name__)

# ── App ───────────────────────────────────────────────────
app = FastAPI(
    title="NODIA WhatsApp AI SaaS",
    description="Backend multi-tenant para agentes IA en WhatsApp",
    version="1.0.0",
    docs_url="/docs" if settings.ENVIRONMENT == "development" else None,
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

# ── Routers ───────────────────────────────────────────────
app.include_router(webhook_router, tags=["WhatsApp Webhook"])
app.include_router(send_message_router, prefix="/api", tags=["Panel Agente"])
app.include_router(meta_connect_router, prefix="/api", tags=["Meta Onboarding"])
app.include_router(odoo_webhook_router, prefix="/api", tags=["Odoo Webhooks"])


# ── Health check ──────────────────────────────────────────
@app.get("/health", tags=["Sistema"])
async def health():
    return {"status": "ok", "service": "nodia-saas-backend", "version": "1.0.0"}


# ── Run ───────────────────────────────────────────────────
if __name__ == "__main__":
    logger.info(f"🚀 NODIA Backend iniciando en puerto {settings.PORT}")
    uvicorn.run("main:app", host="0.0.0.0", port=settings.PORT, reload=True)
