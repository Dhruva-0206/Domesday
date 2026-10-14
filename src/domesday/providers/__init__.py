"""Provider factory: pick the LLM and Embedder named in Settings."""
from __future__ import annotations

from domesday.config import Settings
from domesday.providers.base import LLM, Completion, Embedder, ProviderError
from domesday.providers.fake import FakeEmbedder, FakeLLM

__all__ = ["LLM", "Embedder", "Completion", "ProviderError", "make_llm", "make_embedder"]


def make_llm(settings: Settings) -> LLM:
    kind, model = settings.llm_provider, settings.llm_model
    if kind == "fake":
        return FakeLLM()
    if kind == "anthropic":
        from domesday.providers.anthropic import AnthropicLLM
        return AnthropicLLM(api_key=settings.anthropic_api_key, model=model)
    if kind == "openai":
        from domesday.providers.openai import OpenAILLM
        return OpenAILLM(api_key=settings.openai_api_key, model=model)
    if kind == "ollama":
        from domesday.providers.ollama import OllamaLLM
        return OllamaLLM(base_url=settings.ollama_base_url, model=model)
    raise ProviderError(f"unknown LLM provider {kind!r} (fake | anthropic | openai | ollama)")


def make_embedder(settings: Settings) -> Embedder:
    kind, model = settings.embed_provider, settings.embed_model
    if kind == "fake":
        return FakeEmbedder()
    if kind == "fastembed":
        from domesday.providers.fastembed import FastEmbedder
        return FastEmbedder(model=model)
    if kind == "openai":
        from domesday.providers.openai import OpenAIEmbedder
        return OpenAIEmbedder(api_key=settings.openai_api_key, model=model)
    if kind == "ollama":
        from domesday.providers.ollama import OllamaEmbedder
        return OllamaEmbedder(base_url=settings.ollama_base_url, model=model)
    if kind == "anthropic":
        raise ProviderError("Anthropic has no embedding API; use fastembed, openai, ollama or fake for embeddings")
    raise ProviderError(f"unknown embedding provider {kind!r} (fake | fastembed | openai | ollama)")
