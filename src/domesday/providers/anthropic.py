"""Claude as the LLM (Anthropic has no embedding API, so pair it with fastembed/OpenAI/Ollama embeddings)."""
from __future__ import annotations

from domesday.providers.base import LLM, Completion, ProviderError, closed_schema

DEFAULT_MODEL = "claude-opus-5-5"


class AnthropicLLM(LLM):
    name = "anthropic"

    def __init__(self, api_key: str | None = None, model: str | None = None, effort: str = "medium"):
        import anthropic  # optional dependency

        self._anthropic = anthropic
        # No key given: the SDK resolves ANTHROPIC_API_KEY / an `ant auth login` profile itself.
        self._client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()
        self.model = model or DEFAULT_MODEL
        self.effort = effort

    def complete(self, system: str, user: str, *, schema: dict | None = None, task: str = "chat",
                 payload: dict | None = None, max_tokens: int = 4096) -> Completion:
        output_config: dict = {"effort": self.effort}
        if schema is not None:
            output_config["format"] = {"type": "json_schema", "schema": closed_schema(schema)}
        try:
            resp = self._client.beta.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_config=output_config,
                # A safety decline is re-run server-side on Anthropic's recommended fallback model.
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except self._anthropic.APIStatusError as exc:
            raise ProviderError(f"anthropic {exc.status_code}: {exc.message}") from exc
        except self._anthropic.APIConnectionError as exc:
            raise ProviderError(f"anthropic connection error: {exc}") from exc
        if resp.stop_reason == "refusal":
            raise ProviderError("anthropic refused the request")
        text = "".join(block.text for block in resp.content if block.type == "text")
        return Completion(text=text, tokens_in=resp.usage.input_tokens, tokens_out=resp.usage.output_tokens,
                          model=resp.model)
