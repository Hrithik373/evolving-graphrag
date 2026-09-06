"""Embeddings, metered like every other model call.

Default backend is ``hash``: a deterministic hashed-bag-of-ngrams projection. It costs
nothing, downloads nothing, and is stable across machines - which is what makes the eval
reproducible and CI possible. It is a genuine lexical embedding (character 3-grams plus
word unigrams, L2-normalised), so entity resolution and vector retrieval behave sensibly;
set ``EGRAPH_EMBED_BACKEND=sentence-transformers`` for semantic quality.
"""

from __future__ import annotations

import hashlib
import re
import time
from functools import lru_cache

import numpy as np

from egraph.cost.meter import CostMeter
from egraph.settings import Settings, get_settings

WORD = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> list[str]:
    words = WORD.findall(text.lower())
    grams = list(words)
    for word in words:
        padded = f" {word} "
        grams.extend(padded[i : i + 3] for i in range(len(padded) - 2))
    return grams


@lru_cache(maxsize=8192)
def _bucket(token: str, dim: int) -> tuple[int, float]:
    digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
    value = int.from_bytes(digest, "big")
    return value % dim, 1.0 if (value >> 63) & 1 else -1.0


def hash_embed(text: str, dim: int) -> list[float]:
    vec = np.zeros(dim, dtype=np.float32)
    for token in _tokens(text):
        idx, sign = _bucket(token, dim)
        vec[idx] += sign
    norm = float(np.linalg.norm(vec))
    if norm:
        vec /= norm
    return vec.tolist()


class Embedder:
    def __init__(self, meter: CostMeter, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.meter = meter
        self.dim = self.settings.embed_dim
        self._model = None

    def embed(self, text: str) -> list[float]:
        return self.embed_batch([text])[0]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        started = time.perf_counter()
        backend = self.settings.embed_backend
        if backend == "sentence-transformers":
            vectors = self._sentence_transformers(texts)
        else:
            # 'gateway' embeddings are served by the same proxy in production; offline and
            # in CI the hashed projection stands in, which keeps the eval deterministic.
            vectors = [hash_embed(text, self.dim) for text in texts]
        wall = (time.perf_counter() - started) * 1000
        self.meter.record(
            "embed",
            tokens_in=sum(max(1, len(t) // 4) for t in texts),
            wall_ms=wall,
            model=self.settings.embed_model if backend != "hash" else "hash",
            kind="embed",
        )
        return vectors

    def _sentence_transformers(self, texts: list[str]) -> list[list[float]]:
        if self._model is None:  # pragma: no cover - optional heavy dependency
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self.settings.embed_model)
            self.dim = int(self._model.get_sentence_embedding_dimension())
        matrix = self._model.encode(texts, normalize_embeddings=True)
        return [row.tolist() for row in matrix]
