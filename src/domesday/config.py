"""Settings read from environment variables (and a local .env, if present)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv


def _env(name: str, default: str = "") -> str:
    value = os.environ.get(name, "")
    return value if value.strip() else default


def _int(name: str, default: int) -> int:
    return int(_env(name, str(default)))


def _bool(name: str, default: bool) -> bool:
    return _env(name, "1" if default else "0").lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    falkordb_host: str = "localhost"
    falkordb_port: int = 6379
    falkordb_username: str | None = None
    falkordb_password: str | None = None
    falkordb_max_connections: int = 64
    query_timeout_ms: int = 30_000

    llm_provider: str = "fake"
    embed_provider: str = "fake"
    llm_model: str | None = None
    embed_model: str | None = None
    anthropic_api_key: str | None = field(default=None, repr=False)
    openai_api_key: str | None = field(default=None, repr=False)
    ollama_base_url: str = "http://localhost:11434"

    async_writes: bool = False
    mcp_host: str = "127.0.0.1"
    mcp_port: int = 8765


def load_settings(dotenv: bool = True) -> Settings:
    """Build Settings from the environment. A .env file only fills variables that are unset."""
    if dotenv:
        load_dotenv(override=False)
    return Settings(
        falkordb_host=_env("FALKORDB_HOST", "localhost"),
        falkordb_port=_int("FALKORDB_PORT", 6379),
        falkordb_username=_env("FALKORDB_USERNAME") or None,
        falkordb_password=_env("FALKORDB_PASSWORD") or None,
        falkordb_max_connections=_int("FALKORDB_MAX_CONNECTIONS", 64),
        query_timeout_ms=_int("DOMESDAY_QUERY_TIMEOUT_MS", 30_000),
        llm_provider=_env("DOMESDAY_LLM_PROVIDER", "fake").lower(),
        embed_provider=_env("DOMESDAY_EMBED_PROVIDER", "fake").lower(),
        llm_model=_env("DOMESDAY_LLM_MODEL") or None,
        embed_model=_env("DOMESDAY_EMBED_MODEL") or None,
        anthropic_api_key=_env("ANTHROPIC_API_KEY") or None,
        openai_api_key=_env("OPENAI_API_KEY") or None,
        ollama_base_url=_env("OLLAMA_BASE_URL", "http://localhost:11434"),
        async_writes=_bool("DOMESDAY_ASYNC_WRITES", False),
        mcp_host=_env("DOMESDAY_MCP_HOST", "127.0.0.1"),
        mcp_port=_int("DOMESDAY_MCP_PORT", 8765),
    )
