import re
from typing import List

import numpy as np


def chunk_text(text: str, size: int = 600, overlap: int = 80) -> List[str]:
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    chunks, start = [], 0
    while start < len(text):
        chunk = text[start : start + size].strip()
        if chunk:
            chunks.append(chunk)
        start += size - overlap
    return chunks


_MODEL = None


def _get_model():
    """Load the embedding model once per process.

    Every RAGIndex used to load its own copy, which is wasteful locally and
    exceeds the memory limit of a shared cloud host once several sessions
    are open at the same time.
    """
    global _MODEL
    if _MODEL is None:
        from sentence_transformers import SentenceTransformer

        _MODEL = SentenceTransformer("all-MiniLM-L6-v2")
    return _MODEL


class RAGIndex:
    def __init__(self, text: str):
        import faiss

        self.chunks = chunk_text(text)
        self._model = _get_model()

        emb = self._model.encode(
            self.chunks, show_progress_bar=False, normalize_embeddings=True
        )
        self._index = faiss.IndexFlatIP(emb.shape[1])
        self._index.add(emb.astype(np.float32))

    def retrieve(self, query: str, k: int = 4) -> str:
        emb = self._model.encode([query], normalize_embeddings=True)
        _, idx = self._index.search(
            emb.astype(np.float32), min(k, len(self.chunks))
        )
        return "\n\n---\n\n".join(
            self.chunks[i] for i in idx[0] if i < len(self.chunks)
        )
