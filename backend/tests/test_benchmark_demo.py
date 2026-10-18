"""Public demo: a visitor's benchmark re-run is shown for a while, then the stored reference run returns."""

from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.database import ping
from app.main import app

pytestmark = pytest.mark.skipif(not ping(), reason="FalkorDB is not running")


def test_rerun_is_temporary_and_never_overwrites_the_reference(monkeypatch):
    import app.api.routes as routes
    import app.services.benchmark as bench

    reference = json.loads((get_settings().benchmark_dir / "latest.json").read_text(encoding="utf-8"))
    calls = []

    def fake_run(quick=False, sql_timeout_s=30.0, save=True):
        calls.append(save)
        return {**reference, "generated_at": "2099-01-01T00:00:00Z", "headline": {}}

    monkeypatch.setattr(get_settings(), "benchmark_keep_minutes", 10)
    monkeypatch.setattr(bench, "run_benchmark", fake_run)
    monkeypatch.setitem(routes._bench_fresh, "report", None)
    with TestClient(app) as c:
        assert c.post("/api/benchmark/run", json={"quick": True}).status_code == 200
        for _ in range(50):
            body = c.get("/api/benchmark").json()
            if body["report"]["generated_at"] == "2099-01-01T00:00:00Z":
                break
            time.sleep(0.1)
        assert body["report"]["generated_at"] == "2099-01-01T00:00:00Z" and body["reverts_at"] > time.time()
        assert calls == [False]  # the demo never writes the reference file
        monkeypatch.setitem(routes._bench_fresh, "until", time.time() - 1)  # ten minutes later
        assert c.get("/api/benchmark").json()["report"]["generated_at"] == reference["generated_at"]
    on_disk = json.loads((get_settings().benchmark_dir / "latest.json").read_text(encoding="utf-8"))
    assert on_disk["generated_at"] == reference["generated_at"]
