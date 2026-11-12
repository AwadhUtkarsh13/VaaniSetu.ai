"""Section-aware ingestion of policy markdown into Qdrant.

Each `### N.M Title` subsection (and any intro text directly under a `## N. Title`) becomes
one chunk carrying scheme / section metadata, so answers can cite e.g. `pm_kisan:4.6`.

Run:  uv run python -m src.rag.ingest
"""

from __future__ import annotations

import asyncio
import re
import uuid
import warnings
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel
from qdrant_client import AsyncQdrantClient, models

from config.settings import Settings, get_settings

H2 = re.compile(r"^##\s+(\d+)\.\s+(.+?)\s*$")
H3 = re.compile(r"^###\s+(\d+\.\d+)\s+(.+?)\s*$")
MAX_CHUNK_CHARS = 1500


class Chunk(BaseModel):
    chunk_id: str  # "<doc_id>:<section_id>" — also the citation key
    doc_id: str
    scheme_name: str  # PM_KISAN | PM_JAY | CPGRAMS
    doc_title: str
    section_id: str  # "4.6"
    section_major: str  # "4"
    section_title: str
    parent_title: str
    source_url: str
    body: str

    @property
    def label(self) -> str:
        """Human-readable citation label, e.g. 'PM-KISAN §4.6 Income tax payers'."""
        return f"{self.scheme_name.replace('_', '-')} §{self.section_id} {self.section_title}"

    @property
    def text(self) -> str:
        """Text used for both BM25 and embeddings: breadcrumb + body."""
        scheme = self.scheme_name.replace("_", "-")
        return f"{scheme} — {self.parent_title} — {self.section_title}\n{self.body}"


class Embedder(Protocol):
    dim: int

    async def embed(self, texts: list[str]) -> list[list[float]]: ...


class OpenAIEmbedder:
    def __init__(self, settings: Settings) -> None:
        from langchain_openai import OpenAIEmbeddings

        if not settings.openai_enabled:
            raise RuntimeError("OPENAI_API_KEY is not set; it is required for embeddings.")
        self.dim = settings.openai_embedding_dim
        self._client = OpenAIEmbeddings(
            model=settings.openai_embedding_model,
            dimensions=settings.openai_embedding_dim,
            api_key=settings.openai_api_key,
            max_retries=settings.llm_max_retries,
            # Chunks are <= MAX_CHUNK_CHARS, far below the 8k-token limit, so skip the
            # tiktoken length check (it downloads a tokenizer file at runtime).
            check_embedding_ctx_length=False,
        )

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return await self._client.aembed_documents(texts)


def parse_frontmatter(raw: str) -> tuple[dict[str, str], str]:
    if not raw.startswith("---"):
        return {}, raw
    _, header, body = raw.split("---", 2)
    meta = {}
    for line in header.strip().splitlines():
        key, _, value = line.partition(":")
        meta[key.strip()] = value.strip()
    return meta, body


def chunk_document(path: Path) -> list[Chunk]:
    meta, body = parse_frontmatter(path.read_text(encoding="utf-8"))
    for required in ("doc_id", "scheme_name", "title", "source_url"):
        if required not in meta:
            raise ValueError(f"{path.name}: frontmatter is missing '{required}'")

    chunks: list[Chunk] = []
    parent_id, parent_title = "", ""
    section_id, section_title = "", ""
    lines: list[str] = []

    def flush() -> None:
        text = "\n".join(lines).strip()
        if not section_id or not text:
            return
        for i, part in enumerate(_split_long(text)):
            suffix = f"#{i + 1}" if i else ""
            chunks.append(
                Chunk(
                    chunk_id=f"{meta['doc_id']}:{section_id}{suffix}",
                    doc_id=meta["doc_id"],
                    scheme_name=meta["scheme_name"],
                    doc_title=meta["title"],
                    section_id=section_id,
                    section_major=section_id.split(".")[0],
                    section_title=section_title,
                    parent_title=parent_title,
                    source_url=meta["source_url"],
                    body=part,
                )
            )

    for line in body.splitlines():
        if line.startswith(">"):  # disclaimers are document-level, not retrievable content
            continue
        if m := H2.match(line):
            flush()
            parent_id, parent_title = m.group(1), m.group(2)
            section_id, section_title, lines = parent_id, parent_title, []
        elif m := H3.match(line):
            flush()
            section_id, section_title, lines = m.group(1), m.group(2), []
        else:
            lines.append(line)
    flush()
    return chunks


def _split_long(text: str) -> list[str]:
    if len(text) <= MAX_CHUNK_CHARS:
        return [text]
    parts, current = [], ""
    for sentence in re.split(r"(?<=[.;])\s+", text):
        if current and len(current) + len(sentence) > MAX_CHUNK_CHARS:
            parts.append(current.strip())
            current = ""
        current += sentence + " "
    if current.strip():
        parts.append(current.strip())
    return parts


def load_corpus(policies_dir: Path) -> list[Chunk]:
    chunks = [c for p in sorted(policies_dir.glob("*.md")) for c in chunk_document(p)]
    ids = [c.chunk_id for c in chunks]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate chunk ids in policy corpus")
    return chunks


def point_id(chunk_id: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"vaanisetu/{chunk_id}"))


def make_client(settings: Settings) -> AsyncQdrantClient:
    if settings.qdrant_url:
        return AsyncQdrantClient(url=settings.qdrant_url)
    return AsyncQdrantClient(path=str(settings.qdrant_path))


async def ingest(chunks: list[Chunk], client: AsyncQdrantClient, collection: str, embedder: Embedder) -> int:
    """(Re)create the collection and upsert all chunks. Idempotent."""
    if await client.collection_exists(collection):
        await client.delete_collection(collection)
    await client.create_collection(
        collection,
        vectors_config=models.VectorParams(size=embedder.dim, distance=models.Distance.COSINE),
    )
    with warnings.catch_warnings():  # local mode ignores indexes (and warns); server mode uses them
        warnings.simplefilter("ignore", UserWarning)
        for field in ("scheme_name", "section_id", "section_major"):
            await client.create_payload_index(collection, field, models.PayloadSchemaType.KEYWORD)

    vectors = await embedder.embed([c.text for c in chunks])
    await client.upsert(
        collection,
        points=[
            models.PointStruct(id=point_id(c.chunk_id), vector=v, payload=c.model_dump())
            for c, v in zip(chunks, vectors, strict=True)
        ],
    )
    return len(chunks)


async def _main() -> None:
    settings = get_settings()
    chunks = load_corpus(settings.policies_dir)
    client = make_client(settings)
    try:
        n = await ingest(chunks, client, settings.qdrant_collection, OpenAIEmbedder(settings))
    finally:
        await client.close()
    by_scheme: dict[str, int] = {}
    for c in chunks:
        by_scheme[c.scheme_name] = by_scheme.get(c.scheme_name, 0) + 1
    print(f"Ingested {n} chunks into '{settings.qdrant_collection}': {by_scheme}")


if __name__ == "__main__":
    asyncio.run(_main())
