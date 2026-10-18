"""Public demo: a Gemini run that fails or stops early is finished by the Playbook (live FalkorDB).

Gemini itself is replaced by stand-ins here, so these tests need no model and spend nothing.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.database import ping, run
from app.main import app

pytestmark = pytest.mark.skipif(not ping(), reason="FalkorDB is not running")


@pytest.fixture()
def demo(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "llm_enabled", True)
    monkeypatch.setattr(s, "vertex_project_id", "test-project")
    monkeypatch.setattr(s, "llm_fail_fast", True)
    monkeypatch.setattr(s, "auto_proof", False)
    with TestClient(app) as c:
        c.post("/api/reset")
        yield c
        c.post("/api/reset")


def _gemini(behaviour):
    async def fake_run_llm(cable_id, emit):
        if behaviour == "quota":
            raise RuntimeError("429 RESOURCE_EXHAUSTED: quota exceeded")
        await emit("agent.complete", {"restored": False, "mode": "llm", "reason": "the model did not complete a reallocation"})
        return {"restored": False, "mode": "llm", "reason": "the model did not complete a reallocation", "steps": []}
    return fake_run_llm


def _tunnels_on(cable: str) -> int:
    return run("MATCH (t:LogicalTunnel)-[:CARRIED_BY]->(:CableSystem {id: $c}) RETURN count(t) AS n",
               {"c": cable}, read_only=True, track=False).rows[0]["n"]


@pytest.mark.parametrize("behaviour", ["quota", "stopped-early"])
def test_playbook_finishes_when_gemini_cannot(demo, monkeypatch, behaviour):
    import mcp_server.agent_runner as runner

    monkeypatch.setattr(runner, "run_llm", _gemini(behaviour))
    assert demo.post("/api/chaos/anchor-drag", json={"cable_id": "dunant"}).status_code == 200
    assert demo.post("/api/agent/run", json={"mode": "llm"}).status_code == 200
    agent = demo.get("/api/state").json()["incident"]["agent"]
    assert agent["restored"] and agent["mode"] == "playbook"
    assert agent["restored_gpus"] == 65536 and agent["detour_latency_ms"] == pytest.approx(30.4075)
    assert _tunnels_on("dunant") == 0


def test_without_fail_fast_a_gemini_failure_is_reported(demo, monkeypatch):
    """Locally (no fail-fast) a failed Gemini run is shown as failed, never quietly replaced."""
    import mcp_server.agent_runner as runner

    monkeypatch.setattr(get_settings(), "llm_fail_fast", False)
    monkeypatch.setattr(runner, "run_llm", _gemini("quota"))
    demo.post("/api/chaos/anchor-drag", json={"cable_id": "dunant"})
    with pytest.raises(RuntimeError):
        demo.post("/api/agent/run", json={"mode": "llm"})
    assert _tunnels_on("dunant") > 0
