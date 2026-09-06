"""Vector index operations.

ArcadeDB exposes HNSW through its Java API rather than through SQL, so both backends do
brute-force cosine over an in-process float32 matrix. At the corpus sizes this project
evaluates (10^3-10^4 entities) that is microseconds and exactly reproducible; swapping in
a real ANN index is a drop-in behind ``VectorIndex``.
"""

from __future__ import annotations

import numpy as np


def normalise(vec: list[float] | np.ndarray) -> np.ndarray:
    arr = np.asarray(vec, dtype=np.float32)
    norm = float(np.linalg.norm(arr))
    if norm == 0.0:
        return arr
    return arr / norm


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    return float(np.dot(normalise(a), normalise(b)))


class VectorIndex:
    """Insert/delete/search over unit-normalised vectors, keyed by id."""

    def __init__(self, dim: int) -> None:
        self.dim = dim
        self._ids: list[str] = []
        self._pos: dict[str, int] = {}
        self._matrix: np.ndarray = np.zeros((0, dim), dtype=np.float32)

    def __len__(self) -> int:
        return len(self._ids)

    def upsert(self, key: str, embedding: list[float]) -> None:
        if not embedding:
            return
        vec = normalise(embedding)
        if vec.shape[0] != self.dim:
            # Tolerate a dimension change (e.g. switching embedder) by rebuilding.
            self.dim = int(vec.shape[0])
            self._rebuild_dim()
        idx = self._pos.get(key)
        if idx is None:
            self._pos[key] = len(self._ids)
            self._ids.append(key)
            self._matrix = (
                np.vstack([self._matrix, vec[None, :]]) if len(self._ids) > 1 else vec[None, :]
            )
        else:
            self._matrix[idx] = vec

    def remove(self, key: str) -> None:
        idx = self._pos.pop(key, None)
        if idx is None:
            return
        last = len(self._ids) - 1
        if idx != last:
            moved = self._ids[last]
            self._ids[idx] = moved
            self._pos[moved] = idx
            self._matrix[idx] = self._matrix[last]
        self._ids.pop()
        self._matrix = self._matrix[:last]

    def search(self, embedding: list[float], k: int) -> list[tuple[str, float]]:
        if not self._ids or not embedding:
            return []
        query = normalise(embedding)
        if query.shape[0] != self._matrix.shape[1]:
            return []
        scores = self._matrix @ query
        k = min(k, len(self._ids))
        top = np.argpartition(-scores, k - 1)[:k]
        ordered = top[np.argsort(-scores[top])]
        return [(self._ids[int(i)], float(scores[int(i)])) for i in ordered]

    def clear(self) -> None:
        self._ids.clear()
        self._pos.clear()
        self._matrix = np.zeros((0, self.dim), dtype=np.float32)

    def _rebuild_dim(self) -> None:
        self._ids.clear()
        self._pos.clear()
        self._matrix = np.zeros((0, self.dim), dtype=np.float32)
