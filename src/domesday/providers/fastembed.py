"""Local ONNX embeddings via fastembed (BAAI/bge-small-en-v1.5, 384-d). No key, no server."""
from __future__ import annotations

import os
import threading

from domesday.providers.base import Embedder

DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"
_KNOWN_DIMS = {"BAAI/bge-small-en-v1.5": 384, "BAAI/bge-base-en-v1.5": 768, "sentence-transformers/all-MiniLM-L6-v2": 384}


def _cache_dir() -> str | None:
    if path := os.environ.get("FASTEMBED_CACHE_PATH"):
        return path
    if hf := os.environ.get("HF_HOME"):
        return os.path.join(hf, "fastembed")
    return None


class FastEmbedder(Embedder):
    name = "fastembed"

    def __init__(self, model: str | None = None):
        self.model = model or DEFAULT_MODEL
        self._model = None
        self._lock = threading.Lock()
        self._dim = _KNOWN_DIMS.get(self.model)

    def _load(self):
        with self._lock:
            if self._model is None:
                from fastembed import TextEmbedding  # optional dependency, loads ONNX weights

                self._model = TextEmbedding(model_name=self.model, cache_dir=_cache_dir())
        return self._model

    @property
    def dim(self) -> int:
        if self._dim is None:
            self._dim = len(self.embed_one("dimension probe"))
        return self._dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        model = self._load()
        with self._lock:  # the ONNX session is not documented as thread-safe
            return [vec.tolist() for vec in model.embed(texts)]
