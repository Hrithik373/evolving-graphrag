"""LLM gateway: the single doorway to every model call, and where cost is metered."""

from egraph.gateway.client import LLMClient, Usage
from egraph.gateway.embeddings import Embedder, hash_embed

__all__ = ["Embedder", "LLMClient", "Usage", "hash_embed"]
