"""Retrieval over a small support knowledge base.

* Chunking: one chunk per `##` section of each markdown file, so each chunk
  is a self-contained answer (a policy, a FAQ entry), prefixed with its
  document title for context.
* Lexical search: BM25, implemented here so it runs with no API key.
  Great for exact terms: "COD", "EMI", product names, order IDs.
* Semantic search (optional, EMBEDDINGS_ENABLED=true): OpenAI-compatible
  embeddings + cosine similarity. Catches paraphrases ("send it back"
  matches "returns").
* Hybrid: the two rankings are merged with Reciprocal Rank Fusion
  (score = sum of 1 / (60 + rank)). RRF needs no score normalisation,
  which is why it's the default fusion method in most vector databases.

For a few hundred chunks, numpy cosine similarity is instant. At scale
you'd swap in FAISS, pgvector, Pinecone or Milvus behind the same interface.
"""

from __future__ import annotations

import logging
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

_TOKEN = re.compile(r"[a-z0-9]+")
_STOPWORDS = {
    "a", "an", "the", "is", "are", "was", "to", "of", "and", "or", "in", "on", "for",
    "my", "i", "me", "you", "your", "it", "do", "does", "can", "how", "what", "when",
    "with", "be", "if", "this", "that", "will", "we", "our", "at", "by", "from",
}


def tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(text.lower()) if t not in _STOPWORDS]


@dataclass
class Chunk:
    id: str
    title: str
    text: str
    tokens: list[str] = field(default_factory=list, repr=False)


@dataclass
class SearchResult:
    chunk: Chunk
    score: float


def load_markdown_chunks(directory: str | Path) -> list[Chunk]:
    chunks: list[Chunk] = []
    for path in sorted(Path(directory).glob("*.md")):
        raw = path.read_text(encoding="utf-8")
        title_match = re.search(r"^#\s+(.+)$", raw, re.MULTILINE)
        doc_title = title_match.group(1).strip() if title_match else path.stem
        sections = re.split(r"^##\s+", raw, flags=re.MULTILINE)[1:]
        for i, section in enumerate(sections):
            heading, _, body = section.partition("\n")
            text = f"{doc_title} > {heading.strip()}\n{body.strip()}"
            chunks.append(Chunk(id=f"{path.stem}#{i}", title=heading.strip(), text=text))
    for c in chunks:
        c.tokens = tokenize(c.text)
    return chunks


class BM25:
    def __init__(self, chunks: list[Chunk], k1: float = 1.5, b: float = 0.75) -> None:
        self.chunks = chunks
        self.k1, self.b = k1, b
        self.avgdl = sum(len(c.tokens) for c in chunks) / max(len(chunks), 1)
        df: Counter = Counter()
        for c in chunks:
            df.update(set(c.tokens))
        n = len(chunks)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}
        self.tf = [Counter(c.tokens) for c in chunks]

    def search(self, query: str, k: int = 3) -> list[SearchResult]:
        q = tokenize(query)
        scored = []
        for chunk, tf in zip(self.chunks, self.tf):
            dl = len(chunk.tokens)
            score = 0.0
            for term in q:
                if term not in tf:
                    continue
                f = tf[term]
                score += self.idf[term] * f * (self.k1 + 1) / (
                    f + self.k1 * (1 - self.b + self.b * dl / self.avgdl)
                )
            if score > 0:
                scored.append(SearchResult(chunk, score))
        scored.sort(key=lambda r: r.score, reverse=True)
        return scored[:k]


class EmbeddingIndex:
    def __init__(self, client, model: str) -> None:
        self.client = client
        self.model = model
        self.chunks: list[Chunk] = []
        self.matrix: np.ndarray | None = None

    async def build(self, chunks: list[Chunk]) -> None:
        resp = await self.client.embeddings.create(model=self.model, input=[c.text for c in chunks])
        vectors = np.array([d.embedding for d in resp.data], dtype=np.float32)
        self.matrix = vectors / np.linalg.norm(vectors, axis=1, keepdims=True)
        self.chunks = chunks
        log.info("embedded %d chunks with %s", len(chunks), self.model)

    async def search(self, query: str, k: int = 3) -> list[SearchResult]:
        if self.matrix is None:
            return []
        resp = await self.client.embeddings.create(model=self.model, input=[query])
        q = np.array(resp.data[0].embedding, dtype=np.float32)
        q /= np.linalg.norm(q)
        sims = self.matrix @ q
        top = np.argsort(-sims)[:k]
        return [SearchResult(self.chunks[i], float(sims[i])) for i in top]


def reciprocal_rank_fusion(rankings: list[list[SearchResult]], k: int = 60) -> list[SearchResult]:
    scores: dict[str, float] = {}
    by_id: dict[str, Chunk] = {}
    for ranking in rankings:
        for rank, result in enumerate(ranking):
            by_id[result.chunk.id] = result.chunk
            scores[result.chunk.id] = scores.get(result.chunk.id, 0.0) + 1.0 / (k + rank + 1)
    fused = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    return [SearchResult(by_id[cid], score) for cid, score in fused]


class KnowledgeBase:
    def __init__(self, chunks: list[Chunk], embeddings: EmbeddingIndex | None = None) -> None:
        self.chunks = chunks
        self.bm25 = BM25(chunks)
        self.embeddings = embeddings

    @classmethod
    def from_directory(cls, directory: str | Path, embeddings: EmbeddingIndex | None = None):
        return cls(load_markdown_chunks(directory), embeddings)

    async def build_embeddings(self) -> None:
        if self.embeddings is not None:
            await self.embeddings.build(self.chunks)

    async def search(self, query: str, k: int = 3) -> list[SearchResult]:
        lexical = self.bm25.search(query, k=k * 2)
        if self.embeddings is None or self.embeddings.matrix is None:
            return lexical[:k]
        semantic = await self.embeddings.search(query, k=k * 2)
        return reciprocal_rank_fusion([lexical, semantic])[:k]
