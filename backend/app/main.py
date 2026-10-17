"""FastAPI application entrypoint.

    uvicorn app.main:app --port 8010          (from backend/)
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import app  # noqa: F401 - puts the repository root on sys.path for mcp_server
from app.api import routes, ws
from app.core.config import get_settings

async def _stream_engine_telemetry() -> None:
    """Push a FalkorDB engine snapshot to every client whenever queries ran, at most 5x per second."""
    from app.api.ws import bus
    from app.core.telemetry import telemetry

    seen = -1
    while True:
        await asyncio.sleep(0.2)
        if telemetry.version != seen:
            seen = telemetry.version
            await bus.publish("engine.stats", telemetry.snapshot())


@asynccontextmanager
async def lifespan(_: FastAPI):
    from app.services.bootstrap import ensure_ready

    await asyncio.to_thread(ensure_ready)
    task = asyncio.create_task(_stream_engine_telemetry())
    yield
    task.cancel()


api = FastAPI(title="DarkGlass control plane", version="1.0.0", lifespan=lifespan)
api.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)
api.include_router(routes.router)
api.include_router(ws.router)

app = api
