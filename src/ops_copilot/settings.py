"""Configuration. Secrets from .env, everything else from YAML.

The split matters: .env holds what differs per deployment (keys,
hosts). The YAML files hold what differs per *decision* (thresholds,
model tiers, schema meaning) and are versioned in git so a change
shows up in review.
"""

from __future__ import annotations

import functools
import hashlib
from pathlib import Path
from typing import Any, Literal, cast

import yaml
from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT / "config"
PROMPT_DIR = CONFIG_DIR / "prompts"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # LLM
    groq_api_key: str = ""
    llm_provider: str = "groq"
    llm_base_url: str = "https://api.groq.com/openai/v1"
    llm_model_cheap: str = "openai/gpt-oss-20b"
    llm_model_strong: str = "openai/gpt-oss-120b"
    # Evaluation only. A different model family from the two above, so
    # the system is never graded by the model that wrote the answer, and
    # judging draws on its own daily token allowance.
    llm_model_judge: str = "qwen/qwen3.8-27b"
    llm_timeout_seconds: int = 60
    llm_max_retries: int = 2

    # Embeddings / reranking (local)
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    embedding_dim: int = 384
    reranker_model: str = "BAAI/bge-reranker-base"

    # Database
    database_url: str = ""
    database_url_readonly: str = ""

    # MCP
    mcp_transport: Literal["stdio", "http"] = "stdio"
    mcp_server_host: str = "localhost"
    mcp_server_port: int = 8765
    # Load embedding + reranker models when the tool server starts, so
    # the first question does not pay for it. Off in tests.
    mcp_warm_models: bool = True

    # Observability
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    # .env.example names this LANGFUSE_BASE_URL; LANGFUSE_HOST also works.
    langfuse_host: str = Field(default="https://cloud.langfuse.com",
                               validation_alias=AliasChoices("langfuse_base_url", "langfuse_host"))
    tracing_enabled: bool = True

    # App
    app_env: str = "development"
    log_level: str = "INFO"
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    # Browser origins allowed to call the API, comma-separated. Always an
    # explicit list, never "*": the chat stream carries operational data.
    cors_origins: str = "http://localhost:3000,http://localhost:5173"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def tracing_configured(self) -> bool:
        return bool(
            self.tracing_enabled
            and self.langfuse_public_key
            and self.langfuse_secret_key
        )


@functools.lru_cache
def get_settings() -> Settings:
    return Settings()


@functools.lru_cache
def get_config() -> dict[str, Any]:
    """Runtime thresholds from config/app_config.yaml."""
    # Explicit UTF-8: the Windows default codec would mangle (or fail on)
    # any non-ASCII character in the file.
    with open(CONFIG_DIR / "app_config.yaml", encoding="utf-8") as f:
        return cast(dict[str, Any], yaml.safe_load(f))


@functools.lru_cache
def get_schema_config() -> dict[str, Any]:
    """Schema grounding from config/schema_config.yaml."""
    with open(CONFIG_DIR / "schema_config.yaml", encoding="utf-8") as f:
        return cast(dict[str, Any], yaml.safe_load(f))


@functools.lru_cache
def load_prompt(name: str) -> tuple[str, str]:
    """Return (prompt_body, version_hash).

    The hash goes into every trace. Without it you cannot tell
    whether a past failure is still reproducible after a prompt
    change — which makes the whole feedback loop unfalsifiable.
    """
    path = PROMPT_DIR / f"{name}.md"
    raw = path.read_text(encoding="utf-8")
    body = raw.split("---", 2)[-1].strip() if raw.startswith("---") else raw
    digest = hashlib.sha256(raw.encode()).hexdigest()[:12]
    return body, digest


def model_for(node: str) -> str:
    """Resolve a node name to a concrete model id via its tier."""
    s = get_settings()
    tier = get_config()["llm"]["tiers"].get(node, "cheap")
    return {"strong": s.llm_model_strong, "judge": s.llm_model_judge}.get(tier, s.llm_model_cheap)
