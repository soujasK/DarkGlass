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


async def _reset_idle_incidents(minutes: float) -> None:
    """Public demo: return to the start screen when an incident has been left alone for `minutes`."""
    import time

    from app.api.ws import bus
    from app.services.chaos_engine import chaos

    while True:
        await asyncio.sleep(30)
        inc = chaos.snapshot().get("incident")
        if not inc or routes._agent_lock.locked():
            continue
        last = inc.get("resolved_at") or inc.get("agent_started_at") or inc["started_at"]
        if time.time() - last > minutes * 60:
            await bus.publish("reset", await asyncio.to_thread(chaos.reset))


@asynccontextmanager
async def lifespan(_: FastAPI):
    from app.services.bootstrap import ensure_ready

    await asyncio.to_thread(ensure_ready)
    tasks = [asyncio.create_task(_stream_engine_telemetry())]
    if get_settings().idle_reset_minutes > 0:
        tasks.append(asyncio.create_task(_reset_idle_incidents(get_settings().idle_reset_minutes)))
    yield
    for task in tasks:
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
