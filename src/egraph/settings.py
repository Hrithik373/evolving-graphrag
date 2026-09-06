"""Configuration. Nothing in this system is hard-coded; every knob lands here."""

from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

StoreBackend = Literal["memory", "arcadedb", "postgres"]
QueueBackend = Literal["inline", "arq"]
LLMBackend = Literal["mock", "gateway", "anthropic"]
EmbedBackend = Literal["hash", "sentence-transformers", "gateway"]
ClusterBackend = Literal["auto", "leiden", "label-propagation"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="EGRAPH_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- identity / reproducibility -------------------------------------------------
    env: str = "dev"
    seed: int = 1337

    # --- store -----------------------------------------------------------------------
    store_backend: StoreBackend = "memory"
    arcadedb_url: str = "http://localhost:2480"
    arcadedb_database: str = "egraph"
    arcadedb_user: str = "root"
    arcadedb_password: str = "playwithdata"
    # Postgres is the deployable backend: free and managed on Neon, Supabase, Render and
    # Railway, where ArcadeDB needs a platform that runs Docker with a persistent volume.
    # Providers hand out a DATABASE_URL, so that is accepted as an alias.
    postgres_dsn: str = "postgresql://egraph:egraph@localhost:5432/egraph"
    postgres_pool_max: int = 8
    # Where the memory store persists between process restarts (empty = pure in-process).
    memory_store_path: str = "./data/graph_store.json"

    # --- queue -----------------------------------------------------------------------
    queue_backend: QueueBackend = "inline"
    redis_url: str = "redis://localhost:6379"
    redis_queue_name: str = "egraph:jobs"

    # --- llm gateway ------------------------------------------------------------------
    llm_backend: LLMBackend = "mock"
    gateway_url: str = "http://localhost:8080"
    gateway_api_key: str = ""
    llm_model: str = "claude-opus-5"
    llm_max_tokens: int = 1024
    llm_temperature: float = 0.0
    llm_timeout_s: float = 60.0
    # USD per 1M tokens, used by CostMeter. Override per model in .env.
    price_in_per_mtok: float = 5.0  # claude-opus-5 input
    price_out_per_mtok: float = 25.0  # claude-opus-5 output
    # Extraction is high-volume and schema-constrained; low effort is the right default.
    llm_effort_extract: str = "low"
    llm_effort_summarize: str = "medium"

    # --- embeddings --------------------------------------------------------------------
    embed_backend: EmbedBackend = "hash"
    embed_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    embed_dim: int = 256
    embed_price_per_mtok: float = 0.02

    # --- ingest ------------------------------------------------------------------------
    chunk_size: int = 400  # characters; paragraph-aligned, so a chunk is a coherent fact set
    # Overlap defaults to 0 on purpose. Overlapping chunks copy the tail of their
    # predecessor, so editing one paragraph changes the *text* - and therefore the
    # content-addressed id - of the chunk after it too. That silently destroys the chunk
    # reuse that makes an update cheap. Raise it only if recall across chunk boundaries
    # matters more than update cost for your corpus.
    chunk_overlap: int = 0

    # --- entity resolution --------------------------------------------------------------
    tau_sim: float = 0.86  # cosine threshold for merge
    tau_name: float = 0.90  # normalised-name similarity threshold
    resolver_candidates: int = 20

    # --- churn / maintenance --------------------------------------------------------------
    dirty_batch_size: int = 8
    compaction_interval_s: int = 900
    compaction_enabled: bool = True
    max_community_size: int = 8
    summary_max_entities: int = 20

    # --- retrieval ---------------------------------------------------------------------
    top_k_entities: int = 8
    top_k_communities: int = 3
    max_context_chars: int = 6000

    # --- clustering --------------------------------------------------------------------
    cluster_backend: ClusterBackend = "auto"
    leiden_resolution: float = 1.0

    # --- api ---------------------------------------------------------------------------
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    cors_origins: list[str] = Field(default_factory=lambda: ["*"])
    metrics_enabled: bool = True

    # --- deployment -------------------------------------------------------------------------
    # Load the bundled mini-corpus on boot if the index is empty. A public demo URL that
    # opens on an empty graph shows nothing; this is off by default so it can never touch
    # an index that already has content.
    seed_on_start: bool = False

    # --- eval ----------------------------------------------------------------------------
    eval_output_dir: str = "./results"

    @property
    def config_hash(self) -> str:
        """Stamped onto every eval run so figures are traceable to a configuration."""
        payload = json.dumps(self.model_dump(mode="json"), sort_keys=True, default=str)
        return hashlib.sha256(payload.encode()).hexdigest()[:16]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    """Tests flip env vars; this makes the change visible."""
    get_settings.cache_clear()
