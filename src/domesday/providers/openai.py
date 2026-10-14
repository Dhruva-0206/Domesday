"""OpenAI chat completions and embeddings."""
from __future__ import annotations

from domesday.providers.base import LLM, Completion, Embedder, ProviderError

DEFAULT_LLM_MODEL = "gpt-4o-mini"
DEFAULT_EMBED_MODEL = "text-embedding-3-small"
_KNOWN_DIMS = {"text-embedding-3-small": 1536, "text-embedding-3-large": 3072, "text-embedding-ada-002": 1536}


def _client(api_key: str | None):
    import openai  # optional dependency

    return openai, (openai.OpenAI(api_key=api_key) if api_key else openai.OpenAI())


class OpenAILLM(LLM):
    name = "openai"

    def __init__(self, api_key: str | None = None, model: str | None = None):
        self._openai, self._client = _client(api_key)
        self.model = model or DEFAULT_LLM_MODEL

    def complete(self, system: str, user: str, *, schema: dict | None = None, task: str = "chat",
                 payload: dict | None = None, max_tokens: int = 4096) -> Completion:
        kwargs: dict = {}
        if schema is not None:
            kwargs["response_format"] = {"type": "json_schema",
                                         "json_schema": {"name": task, "schema": schema, "strict": False}}
        try:
            resp = self._client.chat.completions.create(
                model=self.model, max_completion_tokens=max_tokens,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}], **kwargs)
        except self._openai.OpenAIError as exc:
            raise ProviderError(f"openai: {exc}") from exc
        usage = resp.usage
        return Completion(text=resp.choices[0].message.content or "",
                          tokens_in=usage.prompt_tokens if usage else 0,
                          tokens_out=usage.completion_tokens if usage else 0, model=resp.model)


class OpenAIEmbedder(Embedder):
    name = "openai"

    def __init__(self, api_key: str | None = None, model: str | None = None):
        self._openai, self._client = _client(api_key)
        self.model = model or DEFAULT_EMBED_MODEL
        self._dim = _KNOWN_DIMS.get(self.model)

    @property
    def dim(self) -> int:
        if self._dim is None:
            self._dim = len(self.embed_one("dimension probe"))
        return self._dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        try:
            resp = self._client.embeddings.create(model=self.model, input=texts)
        except self._openai.OpenAIError as exc:
            raise ProviderError(f"openai embeddings: {exc}") from exc
        return [d.embedding for d in sorted(resp.data, key=lambda d: d.index)]
