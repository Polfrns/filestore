from __future__ import annotations

import numpy as np


class ChromaDefaultEmbeddingAdapter:
    """Wraps ChromaDB's built-in DefaultEmbeddingFunction (all-MiniLM-L6-v2 via onnxruntime).

    No extra dependencies beyond chromadb. Suitable for local dev and testing.
    Swap for an OpenAI or multilingual adapter before production.
    """

    def __init__(self) -> None:
        from chromadb.utils.embedding_functions import DefaultEmbeddingFunction
        self._fn = DefaultEmbeddingFunction()

    def embed(self, text: str) -> np.ndarray:
        return self._fn([text])[0]
