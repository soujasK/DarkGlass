"""Runtime settings. Every value can be overridden with a DARKGLASS_* environment variable."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DARKGLASS_", env_file=".env", extra="ignore")

    # FalkorDB (Redis protocol). Default port 6390 matches docker-compose.yml.
    falkordb_host: str = "localhost"
    falkordb_port: int = 6390
    falkordb_password: str | None = None
    falkordb_pool_size: int = 16
    graph_name: str = "darkglass_core"

    # Data locations
    data_dir: Path = REPO_ROOT / "data"
    frontend_data_dir: Path = REPO_ROOT / "frontend" / "public" / "data"

    # Physics: light in silica, n = 1.4682 -> tau = n / c
    refractive_index: float = 1.4682
    speed_of_light_km_s: float = 299_792.458

    # Layer 7 workload constants (MLSys-modeled)
    stall_loss_usd_per_hour: float = 18_420.00
    max_jitter_ms: float = 12.0
    # Modelled counterfactual for the impact readout: BGP/IGP reconvergence plus manual traffic
    # engineering after a subsea cut. An assumption, labelled as such in the UI.
    legacy_reconvergence_s: float = 180.0
    auto_proof: bool = True  # after each fix, measure that cut live in FalkorDB, SQL and NetworkX

    # Routing
    max_hops: int = 12
    candidate_paths: int = 3

    # Agent (Claude mode). provider "anthropic" uses ANTHROPIC_API_KEY / `ant auth login`;
    # "vertex" uses Google Application Default Credentials (`gcloud auth application-default login`).
    anthropic_model: str = "claude-opus-5-5"
    # "gemini" runs Google Gemini on Vertex AI (same ADC credentials and project as "vertex").
    llm_provider: str = "gemini"
    gemini_model: str = "gemini-4-argon"
    vertex_project_id: str | None = None  # set DARKGLASS_VERTEX_PROJECT_ID in backend/.env
    vertex_region: str = "global"
    cors_origins: list[str] = ["http://localhost:3000", "http://127.0.0.1:3000"]

    @property
    def tau_ms_per_km(self) -> float:
        """Propagation delay in milliseconds per kilometre (~0.004897)."""
        return self.refractive_index / self.speed_of_light_km_s * 1000.0

    @property
    def stall_loss_usd_per_second(self) -> float:
        return self.stall_loss_usd_per_hour / 3600.0

    @property
    def graph_dir(self) -> Path:
        return self.data_dir / "graph"

    @property
    def benchmark_dir(self) -> Path:
        return self.data_dir / "benchmarks"


@lru_cache
def get_settings() -> Settings:
    return Settings()
