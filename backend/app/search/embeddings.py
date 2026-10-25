"""Embedding models. OpenAIEmbedder for real use; HashEmbedder for offline tests."""

from __future__ import annotations

import hashlib
import math
import re
import struct
from typing import Any, List, Optional

from app import config, llm


def normalise(vec: List[float]) -> List[float]:
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


def to_blob(vec: List[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


def from_blob(blob: bytes) -> List[float]:
    return list(struct.unpack(f"{len(blob) // 4}f", blob))


class Embedder:
    name: str
    dim: int

    def embed(self, texts: List[str]) -> List[List[float]]:  # pragma: no cover
        raise NotImplementedError


class OpenAIEmbedder(Embedder):
    def __init__(self, client: Any = None, model: Optional[str] = None, dim: Optional[int] = None):
        self.client = client or llm.get_client()
        self.model = model or config.EMBEDDING_MODEL
        self.dim = dim or config.EMBEDDING_DIMENSIONS
        self.name = f"{self.model}@{self.dim}"

    def embed(self, texts: List[str]) -> List[List[float]]:
        response = self.client.embeddings.create(model=self.model, input=texts, dimensions=self.dim)
        return [normalise(item.embedding) for item in response.data]


class HashEmbedder(Embedder):
    """Deterministic bag-of-words vectors. No semantics beyond shared words:
    only for tests and offline demos of the vector plumbing."""

    def __init__(self, dim: int = 256):
        self.dim = dim
        self.name = f"hash@{dim}"

    def embed(self, texts: List[str]) -> List[List[float]]:
        out = []
        for text in texts:
            vec = [0.0] * self.dim
            for token in re.findall(r"[a-z]{3,}", text.lower()):
                h = int(hashlib.md5(token[:6].encode()).hexdigest(), 16)
                vec[h % self.dim] += 1.0
            out.append(normalise(vec))
        return out


def default_embedder() -> Optional[Embedder]:
    """OpenAI if a key is configured; otherwise no vectors (search falls back to keywords)."""
    if not config.OPENAI_API_KEY:
        return None
    return OpenAIEmbedder()
