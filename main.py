# main.py
# NODIA SaaS Backend — Entry point FastAPI

import logging
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from api.webhook import router as webhook_router
from api.send_message import router as send_message_router
from config import settings

# ── Logging ──────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
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
    allow_origins=["http://localhost:3000", "https://tu-panel.vercel.app"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routers ───────────────────────────────────────────────
app.include_router(webhook_router, tags=["WhatsApp Webhook"])
app.include_router(send_message_router, prefix="/api", tags=["Panel Agente"])


# ── Health check ──────────────────────────────────────────
@app.get("/health", tags=["Sistema"])
async def health():
    return {"status": "ok", "service": "nodia-saas-backend", "version": "1.0.0"}


# ── Run ───────────────────────────────────────────────────
if __name__ == "__main__":
    logger.info(f"🚀 NODIA Backend iniciando en puerto {settings.PORT}")
    uvicorn.run("main:app", host="0.0.0.0", port=settings.PORT, reload=True)
