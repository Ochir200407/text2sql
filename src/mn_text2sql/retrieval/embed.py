"""Embedding загвар (сонголттой). sentence-transformers байхгүй бол None буцааж BM25-аар л ажиллана.

Загвар бүрийн query/passage prefix (зааврын 2-р хэсэг "Embedding загвар"):
  intfloat/multilingual-e5-*: "query: " / "passage: "
  Qwen/Qwen3-Embedding-*:     query-д заавар; passage-д юу ч үгүй
  BAAI/bge-m3, sentence-transformers/LaBSE: prefix-гүй
"""

from __future__ import annotations

import logging
from functools import lru_cache

import numpy as np

log = logging.getLogger(__name__)


def _prefixes(model_name: str) -> tuple[str, str]:
    name = model_name.lower()
    if "e5" in name:
        return "query: ", "passage: "
    if "qwen3-embedding" in name:
        return ("Instruct: Given a Mongolian banking question, retrieve relevant database schema, "
                "business terms and example questions\nQuery: "), ""
    return "", ""


class Embedder:
    def __init__(self, model_name: str, device: str = "auto", batch_size: int = 32):
        from sentence_transformers import SentenceTransformer  # noqa: WPS433 (сонголттой хамаарал)

        if device == "auto":
            try:
                import torch
                device = "cuda" if torch.cuda.is_available() else "cpu"
            except ImportError:
                device = "cpu"
        self.model_name = model_name
        self.model = SentenceTransformer(model_name, device=device, trust_remote_code=True)
        self.batch_size = batch_size
        self.q_prefix, self.p_prefix = _prefixes(model_name)

    def encode(self, texts: list[str], is_query: bool = False) -> np.ndarray:
        prefix = self.q_prefix if is_query else self.p_prefix
        vecs = self.model.encode([prefix + t for t in texts], batch_size=self.batch_size,
                                 normalize_embeddings=True, show_progress_bar=False)
        return np.asarray(vecs, dtype=np.float32)


@lru_cache(maxsize=2)
def get_embedder(model_name: str | None, device: str = "auto", batch_size: int = 32) -> Embedder | None:
    if not model_name or model_name.lower() in ("none", "off", "bm25"):
        return None
    try:
        return Embedder(model_name, device, batch_size)
    except ImportError:
        log.warning("sentence-transformers суугаагүй — embedding-гүй (BM25) горимоор ажиллана")
    except Exception as e:  # загвар татагдаагүй, GPU алдаа г.м.
        log.warning("embedding загвар (%s) ачаалагдсангүй: %s — BM25 горим", model_name, e)
    return None


def cosine_top(matrix: np.ndarray | None, query_vec: np.ndarray | None, k: int) -> dict[int, float]:
    if matrix is None or query_vec is None or len(matrix) == 0:
        return {}
    sims = matrix @ query_vec.reshape(-1)
    idx = np.argsort(-sims)[:k]
    return {int(i): float(sims[i]) for i in idx}
