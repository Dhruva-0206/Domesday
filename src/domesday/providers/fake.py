"""Deterministic offline providers: hashed n-gram embeddings and a rule-based LLM stand-in.

Same input always gives the same output, on every machine, with no network. Tests and the
keyless demo run on these.
"""
from __future__ import annotations

import hashlib
import json
import math

from domesday.providers.base import LLM, Completion, Embedder
from domesday.providers.rules import extract_rules
from domesday.text import STOPWORDS, words


def _bucket(feature: str, dim: int) -> tuple[int, float]:
    h = int.from_bytes(hashlib.blake2b(feature.encode(), digest_size=8).digest(), "big")
    return h % dim, (1.0 if (h >> 63) & 1 else -1.0)


class FakeEmbedder(Embedder):
    """Signed feature hashing of word unigrams, word bigrams and character trigrams, L2-normalised.

    Lexically similar texts land close together; unrelated texts are near-orthogonal.
    """

    name = "fake"

    def __init__(self, dim: int = 256):
        self._dim = dim
        self.model = f"fake-hash-ngram-{dim}"

    @property
    def dim(self) -> int:
        return self._dim

    def _features(self, text: str) -> list[tuple[str, float]]:
        toks = words(text)
        feats: list[tuple[str, float]] = []
        for t in toks:
            weight = 0.25 if t in STOPWORDS else 1.0
            feats.append((f"w:{t}", weight))
            padded = f"<{t}>"
            feats.extend((f"c:{padded[i:i + 3]}", 0.35 * weight) for i in range(len(padded) - 2))
        feats.extend((f"b:{a}_{b}", 0.5) for a, b in zip(toks, toks[1:]))
        return feats

    def embed(self, texts: list[str]) -> list[list[float]]:
        out = []
        for text in texts:
            vec = [0.0] * self._dim
            for feat, weight in self._features(text):
                idx, sign = _bucket(feat, self._dim)
                vec[idx] += sign * weight
            norm = math.sqrt(sum(v * v for v in vec)) or 1.0
            out.append([v / norm for v in vec])
        return out


class FakeLLM(LLM):
    """Answers the tasks Domesday asks of an LLM using rules over the `payload`.

    - extract:   rule-based extraction of payload["text"] into the Extraction schema
    - answer:    returns the highest-ranked context line (payload["context"] is a list of strings)
    - summarize: joins the first sentence of each payload["texts"] item
    """

    name = "fake"
    model = "fake-rules-1"

    def complete(self, system: str, user: str, *, schema: dict | None = None, task: str = "chat",
                 payload: dict | None = None, max_tokens: int = 4096) -> Completion:
        payload = payload or {}
        if task == "extract":
            text = json.dumps(extract_rules(payload.get("text", user), now_ms=payload.get("now_ms")).model_dump())
        elif task == "answer":
            context = payload.get("context") or []
            text = context[0] if context else "I don't have that in memory."
        elif task == "summarize":
            parts = [t.split(". ")[0].rstrip(".") for t in payload.get("texts", []) if t.strip()]
            text = "; ".join(parts)[:1000] or "(empty)"
        else:
            text = user[:max_tokens * 4]
        return Completion(text=text, tokens_in=len(words(system + " " + user)), tokens_out=len(words(text)),
                          model=self.model)
