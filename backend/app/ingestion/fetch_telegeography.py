"""Download the public TeleGeography Submarine Cable Map dataset into data/.

Outputs (repository-relative):
    data/cables.geojson            cable route polylines (MultiLineString per cable)
    data/landing_stations.geojson  landing station points
    data/cable_details.json        per-cable metadata (landing points, length, RFS year)

Data source: https://www.submarinecablemap.com (TeleGeography), licensed
CC BY-NC-SA 3.0. Attribution is required in any derived work.

Usage:
    python -m app.ingestion.fetch_telegeography          (from backend/)
"""

from __future__ import annotations

import json
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

BASE = "https://www.submarinecablemap.com/api/v3"
DATA_DIR = Path(__file__).resolve().parents[3] / "data"
UA = {"User-Agent": "darkglass-ingest/1.0 (research; hackathon)"}


def _get(url: str) -> dict:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> int:
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    print("[fetch] cable-geo.json")
    cables = _get(f"{BASE}/cable/cable-geo.json")
    (DATA_DIR / "cables.geojson").write_text(json.dumps(cables), encoding="utf-8")

    print("[fetch] landing-point-geo.json")
    stations = _get(f"{BASE}/landing-point/landing-point-geo.json")
    (DATA_DIR / "landing_stations.geojson").write_text(json.dumps(stations), encoding="utf-8")

    ids = sorted({f["properties"]["id"] for f in cables["features"]})
    print(f"[fetch] {len(ids)} cable detail records")

    details: dict[str, dict] = {}
    failures: list[str] = []
    with ThreadPoolExecutor(max_workers=12) as pool:
        futures = {pool.submit(_get, f"{BASE}/cable/{cid}.json"): cid for cid in ids}
        for i, fut in enumerate(as_completed(futures), 1):
            cid = futures[fut]
            try:
                details[cid] = fut.result()
            except Exception as exc:  # network hiccup on a single record
                failures.append(f"{cid}: {exc}")
            if i % 100 == 0:
                print(f"[fetch]   {i}/{len(ids)}")

    (DATA_DIR / "cable_details.json").write_text(
        json.dumps(details, sort_keys=True), encoding="utf-8"
    )
    print(f"[fetch] done: {len(details)} details, {len(failures)} failures")
    for line in failures[:20]:
        print(f"[fetch]   failed {line}")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
