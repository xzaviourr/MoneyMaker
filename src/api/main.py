"""
FastAPI application — Mission Control backend.

Endpoints:
  GET  /health
  GET  /portfolio
  GET  /pods
  GET  /pods/{pod_id}
  POST /pods/{pod_id}/command
  GET  /decisions?symbol=&limit=
  POST /commands (human override)
  GET  /feedback/summary
  WS   /ws/live
"""
from __future__ import annotations

from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .middleware.auth import ApiKeyMiddleware
from .middleware.request_id import RequestIdMiddleware
from .routes import portfolio, pods, decisions, commands, feedback, system, logs, news, ideas
from .websocket.live_feed import router as ws_router

log = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("api.startup")
    yield
    log.info("api.shutdown")


app = FastAPI(
    title="MoneyMaker Mission Control",
    description="Indian algorithmic trading platform API",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(RequestIdMiddleware)
app.add_middleware(ApiKeyMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(portfolio.router, prefix="/portfolio",  tags=["portfolio"])
app.include_router(pods.router,      prefix="/pods",       tags=["pods"])
app.include_router(decisions.router, prefix="/decisions",  tags=["decisions"])
app.include_router(commands.router,  prefix="/commands",   tags=["commands"])
app.include_router(feedback.router,  prefix="/feedback",   tags=["feedback"])
app.include_router(system.router,    prefix="/system",     tags=["system"])
app.include_router(logs.router,      prefix="/logs",       tags=["logs"])
app.include_router(news.router,      prefix="/news",       tags=["news"])
app.include_router(ideas.router,     prefix="/ideas",      tags=["ideas"])
app.include_router(ws_router,                              tags=["websocket"])


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "moneymaker-api"}


@app.get("/ready")
async def ready() -> dict:
    """Readiness probe — confirms the bus and broker are up before accepting traffic."""
    from .websocket.live_feed import manager
    from ..shared.message_bus import MessageBus
    bus = MessageBus.get()
    checks = {
        "bus": bus._running,
        "ws_clients": manager.count,
    }
    try:
        from ..brokers.broker_gateway import BrokerGateway
        checks["broker"] = BrokerGateway.get().is_connected
    except Exception:
        checks["broker"] = False
    all_ok = checks["bus"] and checks["broker"]
    return {"status": "ready" if all_ok else "not_ready", **checks}
