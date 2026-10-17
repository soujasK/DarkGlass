"""WebSocket broadcaster: fans control-plane events out to every connected frontend."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect


class Broadcaster:
    def __init__(self) -> None:
        self._clients: set[asyncio.Queue[str]] = set()

    def subscribe(self) -> asyncio.Queue[str]:
        q: asyncio.Queue[str] = asyncio.Queue(maxsize=256)
        self._clients.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[str]) -> None:
        self._clients.discard(q)

    async def publish(self, event: str, payload: dict[str, Any]) -> None:
        msg = json.dumps({"type": event, "ts": time.time(), "payload": payload}, default=str)
        for q in list(self._clients):
            if q.full():
                q.get_nowait()  # drop the oldest frame for a slow client rather than block the agent
            q.put_nowait(msg)


bus = Broadcaster()
router = APIRouter()


@router.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    q = bus.subscribe()
    try:
        await ws.send_text(json.dumps({"type": "hello", "ts": time.time(), "payload": {}}))
        while True:
            await ws.send_text(await q.get())
    except WebSocketDisconnect:
        pass
    finally:
        bus.unsubscribe(q)
