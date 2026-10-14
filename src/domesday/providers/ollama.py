"""Ollama over its local HTTP API (no key, runs on the user's machine)."""
from __future__ import annotations

import httpx

from domesday.providers.base import LLM, Completion, Embedder, ProviderError

DEFAULT_LLM_MODEL = "llama3.2"
DEFAULT_EMBED_MODEL = "nomic-embed-text"


def _post(base_url: str, path: str, body: dict, timeout: float = 300.0) -> dict:
    try:
        resp = httpx.post(f"{base_url.rstrip('/')}{path}", json=body, timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPError as exc:
        raise ProviderError(f"ollama {path}: {exc}") from exc


class OllamaLLM(LLM):
    name = "ollama"

    def __init__(self, base_url: str = "http://localhost:11434", model: str | None = None):
        self.base_url = base_url
        self.model = model or DEFAULT_LLM_MODEL

    def complete(self, system: str, user: str, *, schema: dict | None = None, task: str = "chat",
                 payload: dict | None = None, max_tokens: int = 4096) -> Completion:
        body: dict = {"model": self.model, "stream": False, "options": {"num_predict": max_tokens},
                      "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        if schema is not None:
            body["format"] = schema
        data = _post(self.base_url, "/api/chat", body)
        return Completion(text=data.get("message", {}).get("content", ""),
                          tokens_in=data.get("prompt_eval_count", 0), tokens_out=data.get("eval_count", 0),
                          model=self.model)


class OllamaEmbedder(Embedder):
    name = "ollama"

    def __init__(self, base_url: str = "http://localhost:11434", model: str | None = None):
        self.base_url = base_url
        self.model = model or DEFAULT_EMBED_MODEL
        self._dim: int | None = None

    @property
    def dim(self) -> int:
        if self._dim is None:
            self._dim = len(self.embed_one("dimension probe"))
        return self._dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        return _post(self.base_url, "/api/embed", {"model": self.model, "input": texts})["embeddings"]
