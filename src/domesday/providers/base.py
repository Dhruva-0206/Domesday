"""Provider interfaces: an LLM that completes prompts and an Embedder that turns text into vectors.

Every provider reports token usage, so store and query costs are visible (the original logged 0
for stores). `task` and `payload` are hints: real models ignore them; the offline fake uses them
to answer deterministically without a model.
"""
from __future__ import annotations

import copy
from abc import ABC, abstractmethod
from dataclasses import dataclass


class ProviderError(RuntimeError):
    """A provider call failed in a way the caller should surface (refusal, bad config, transport)."""


@dataclass(frozen=True)
class Completion:
    text: str
    tokens_in: int = 0
    tokens_out: int = 0
    model: str = ""


class LLM(ABC):
    name: str = "llm"
    model: str = ""

    @abstractmethod
    def complete(self, system: str, user: str, *, schema: dict | None = None, task: str = "chat",
                 payload: dict | None = None, max_tokens: int = 4096) -> Completion:
        """Return the model's reply. With `schema`, the reply must be a JSON document matching it."""


class Embedder(ABC):
    name: str = "embedder"
    model: str = ""

    @property
    @abstractmethod
    def dim(self) -> int: ...

    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]: ...

    def embed_one(self, text: str) -> list[float]:
        return self.embed([text])[0]


def closed_schema(schema: dict) -> dict:
    """Copy of a JSON schema with additionalProperties:false on every object, as structured outputs expect."""
    out = copy.deepcopy(schema)

    def walk(node):
        if isinstance(node, dict):
            if node.get("type") == "object" or "properties" in node:
                node.setdefault("additionalProperties", False)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(out)
    return out
