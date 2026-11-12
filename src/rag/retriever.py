"""Hybrid retrieval: BM25 (lexical) + Qdrant dense search, fused with Reciprocal Rank Fusion.

RRF score(d) = Σ_r 1 / (k + rank_r(d)), summed over the BM25 and dense rankings (ranks
start at 1). Both legs honour the same metadata filter (scheme_name, section).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from qdrant_client import AsyncQdrantClient, models
from rank_bm25 import BM25Okapi

from src.rag.ingest import Chunk, Embedder
from src.telemetry import stage

_TOKEN = re.compile(r"[a-z0-9₹]+")


def tokenize(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


@dataclass
class RetrievedChunk:
    chunk: Chunk
    score: float  # fused RRF score
    bm25_rank: int | None
    dense_rank: int | None


def rrf_fuse(rankings: list[list[str]], k: int) -> dict[str, float]:
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, chunk_id in enumerate(ranking, start=1):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + rank)
    return scores


def _matches(chunk: Chunk, scheme: str | None, section: str | None) -> bool:
    if scheme and chunk.scheme_name != scheme:
        return False
    if section:
        return chunk.section_id == section if "." in section else chunk.section_major == section
    return True


def _qdrant_filter(scheme: str | None, section: str | None) -> models.Filter | None:
    must: list[models.Condition] = []
    if scheme:
        must.append(models.FieldCondition(key="scheme_name", match=models.MatchValue(value=scheme)))
    if section:
        key = "section_id" if "." in section else "section_major"
        must.append(models.FieldCondition(key=key, match=models.MatchValue(value=section)))
    return models.Filter(must=must) if must else None


class HybridRetriever:
    def __init__(
        self,
        client: AsyncQdrantClient,
        collection: str,
        embedder: Embedder,
        chunks: list[Chunk],
        rrf_k: int = 60,
        candidate_k: int = 20,
    ) -> None:
        if not chunks:
            raise ValueError("HybridRetriever needs a non-empty corpus; run `python -m src.rag.ingest`")
        self.client = client
        self.collection = collection
        self.embedder = embedder
        self.rrf_k = rrf_k
        self.candidate_k = candidate_k
        self.chunks = chunks
        self.by_id = {c.chunk_id: c for c in chunks}
        self._bm25 = BM25Okapi([tokenize(c.text) for c in chunks])

    @classmethod
    async def from_qdrant(
        cls, client: AsyncQdrantClient, collection: str, embedder: Embedder, **kwargs
    ) -> HybridRetriever:
        """Rebuild the BM25 index from the payloads stored in Qdrant (single source of truth)."""
        if not await client.collection_exists(collection):
            raise RuntimeError(f"Qdrant collection '{collection}' not found; run `uv run python -m src.rag.ingest`")
        chunks: list[Chunk] = []
        offset = None
        while True:
            points, offset = await client.scroll(
                collection, limit=256, offset=offset, with_payload=True, with_vectors=False
            )
            chunks.extend(Chunk(**p.payload) for p in points if p.payload)
            if offset is None:
                break
        chunks.sort(key=lambda c: c.chunk_id)
        return cls(client, collection, embedder, chunks, **kwargs)

    def bm25_ranking(self, query: str, scheme: str | None, section: str | None) -> list[str]:
        scores = self._bm25.get_scores(tokenize(query))
        ranked = sorted(
            (
                (score, c.chunk_id)
                for c, score in zip(self.chunks, scores, strict=True)
                if score > 0 and _matches(c, scheme, section)
            ),
            reverse=True,
        )
        return [chunk_id for _, chunk_id in ranked[: self.candidate_k]]

    async def dense_ranking(self, query: str, scheme: str | None, section: str | None) -> list[str]:
        with stage("retrieval_ms", "retrieval.embed_query"):
            [vector] = await self.embedder.embed([query])
        with stage("retrieval_ms", "retrieval.qdrant_search"):
            response = await self.client.query_points(
                self.collection,
                query=vector,
                query_filter=_qdrant_filter(scheme, section),
                limit=self.candidate_k,
                with_payload=["chunk_id"],
            )
        return [p.payload["chunk_id"] for p in response.points if p.payload]

    async def retrieve(
        self,
        query: str,
        top_k: int = 8,
        scheme: str | None = None,
        section: str | None = None,
    ) -> list[RetrievedChunk]:
        with stage("retrieval_ms", "retrieval.bm25"):
            bm25 = self.bm25_ranking(query, scheme, section)
        dense = await self.dense_ranking(query, scheme, section)
        fused = rrf_fuse([bm25, dense], self.rrf_k)
        bm25_pos = {cid: i for i, cid in enumerate(bm25, start=1)}
        dense_pos = {cid: i for i, cid in enumerate(dense, start=1)}
        best = sorted(fused.items(), key=lambda kv: kv[1], reverse=True)[:top_k]
        return [
            RetrievedChunk(self.by_id[cid], round(score, 5), bm25_pos.get(cid), dense_pos.get(cid))
            for cid, score in best
            if cid in self.by_id
        ]
