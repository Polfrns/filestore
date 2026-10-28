"""KB engine — lazy staleness detection and ChromaDB indexing.

Called at query time (via search_knowledge tool). No background jobs, no schedulers.
The first query on a freshly attached filestore pays the full ingest cost;
subsequent queries on an unchanged store skip steps 3-4 entirely.
"""
from __future__ import annotations

import logging
import numpy as np
from time import perf_counter
from typing import Sequence

logger = logging.getLogger(__name__)

from django.conf import settings

from filestores.domain import FileStore as FileStorePort, path_matches_glob, validate_glob
from .domain import ChunkLocation, EmbeddingPort, FileReadError, IngestResult, RetrievedChunk, SourceManifest
from .models import KBIndex

# ---- Tuning knobs ----
CHUNK_SIZE = 800
CHUNK_OVERLAP = 100
TOP_K = 5
MAX_DISTANCE = 1.0
SOURCE_FILTER_BATCH_SIZE = 500

# ---- Module-level state ----
_client = None
_collections: dict = {}
_embedding_port: EmbeddingPort | None = None


def set_embedding_port(port: EmbeddingPort) -> None:
    global _embedding_port
    _embedding_port = port


def _embed(text: str) -> np.ndarray:
    if _embedding_port is None:
        raise RuntimeError(
            "EmbeddingPort not configured — call kb.engine.set_embedding_port() first"
        )
    return _embedding_port.embed(text)


# ---- Lazy ChromaDB resources ----

def _get_client():
    global _client
    if _client is None:
        import chromadb
        _client = chromadb.PersistentClient(path=str(settings.CHROMA_DB_PATH))
    return _client


def _get_collection(name: str):
    if name not in _collections:
        _collections[name] = _get_client().get_or_create_collection(
            name, metadata={"hnsw:space": "cosine"}
        )
    return _collections[name]


# ---- Chunking ----

def _chunk(text: str) -> list[tuple[str, int, int]]:
    """Split text into overlapping fixed-size windows. Returns (chunk, start_char, end_char)."""
    text = text.strip()
    if not text:
        return []
    step = CHUNK_SIZE - CHUNK_OVERLAP
    chunks = []
    for i in range(0, len(text), step):
        chunks.append((text[i:i + CHUNK_SIZE], i, min(i + CHUNK_SIZE, len(text))))
    return chunks


# ---- Ingestion ----

def _get_store_model(uid: str):
    from filestores.models import FileStore as FileStoreModel
    try:
        return FileStoreModel.objects.get(uid=uid)
    except FileStoreModel.DoesNotExist:
        return None


def ingest_file(store: FileStorePort, path: str, mtime: float) -> IngestResult:
    """Chunk, embed and upsert a document; update KBIndex row."""
    try:
        text = store.read_as_markdown(path)
    except Exception as exc:
        raise FileReadError(f"Could not read '{path}' from store '{store.uid}'") from exc
    chunks_data = _chunk(text)
    collection = _get_collection(f"store_{store.uid}")
    collection.delete(where={"source": path})

    if chunks_data:
        docs = [c[0] for c in chunks_data]
        locations = [ChunkLocation(char_start=c[1], char_end=c[2]) for c in chunks_data]
        metadatas = [
            {"source": path, "chunk": i, **loc.to_dict()}
            for i, loc in enumerate(locations)
        ]
        collection.add(
            ids=[f"{path}::{i}" for i in range(len(chunks_data))],
            embeddings=[_embed(d) for d in docs],
            documents=docs,
            metadatas=metadatas,
        )

    store_model = _get_store_model(store.uid)
    if store_model is not None:
        KBIndex.objects.update_or_create(
            filestore=store_model,
            file_path=path,
            defaults={"file_mtime": mtime},
        )

    return IngestResult(source=path, chunks=len(chunks_data))


def sync_store(store: FileStorePort) -> SourceManifest:
    """Diff the live filestore against KBIndex and return the current file manifest."""
    manifest = SourceManifest(tuple(entry for entry in store.list() if not entry.is_dir))
    store_model = _get_store_model(store.uid)
    if store_model is None:
        return manifest

    entries = {entry.path: entry for entry in manifest.files}
    indexed = {r.file_path: r for r in KBIndex.objects.filter(filestore=store_model)}
    collection = _get_collection(f"store_{store.uid}")

    for path in [p for p in indexed if p not in entries]:
        collection.delete(where={"source": path})
        indexed[path].delete()

    for path, entry in entries.items():
        if path not in indexed or indexed[path].file_mtime != entry.mtime:
            try:
                ingest_file(store, path, entry.mtime)
            except FileReadError:
                logger.warning("Failed to ingest %s from store %s", path, store.uid, exc_info=True)
    return manifest


# ---- Retrieval ----

def retrieve(
    query: str,
    stores: Sequence[FileStorePort],
    glob: str | None = None,
    k: int = TOP_K,
    max_distance: float = MAX_DISTANCE,
) -> list[RetrievedChunk]:
    """Semantic search across stores; triggers lazy re-ingest first. Returns top-k by distance."""
    validate_glob(glob)
    query_emb = _embed(query)
    all_chunks: list[RetrievedChunk] = []

    for store in stores:
        manifest = sync_store(store)
        all_sources = manifest.paths
        matched_sources = (
            all_sources
            if glob is None
            else frozenset(path for path in all_sources if path_matches_glob(path, glob))
        )
        if not matched_sources:
            logger.info(
                "KB query scope store=%s sources=%d matched=0 batches=0 duration_ms=0",
                store.uid,
                len(all_sources),
            )
            continue

        collection = _get_collection(f"store_{store.uid}")
        collection_count = collection.count()
        if collection_count == 0:
            continue

        source_batches: list[tuple[str, ...] | None]
        if matched_sources == all_sources:
            source_batches = [None]
        else:
            ordered_sources = sorted(matched_sources)
            source_batches = [
                tuple(ordered_sources[start:start + SOURCE_FILTER_BATCH_SIZE])
                for start in range(0, len(ordered_sources), SOURCE_FILTER_BATCH_SIZE)
            ]

        started_at = perf_counter()
        for source_batch in source_batches:
            where = None if source_batch is None else {"source": {"$in": list(source_batch)}}
            result = collection.query(
                query_embeddings=[query_emb],
                n_results=min(k, collection_count),
                where=where,
                include=["documents", "metadatas", "distances"],
            )
            if not result["documents"] or not result["documents"][0]:
                continue

            for doc, meta, dist in zip(
                result["documents"][0], result["metadatas"][0], result["distances"][0]
            ):
                if dist > max_distance:
                    continue
                meta = meta or {}
                all_chunks.append(RetrievedChunk(
                    text=doc,
                    filestore_uid=store.uid,
                    filestore_display=store.display_name,
                    source=meta.get("source", "?"),
                    distance=dist,
                    location=ChunkLocation.from_dict(meta),
                ))
        logger.info(
            "KB query scope store=%s sources=%d matched=%d batches=%d duration_ms=%.1f",
            store.uid,
            len(all_sources),
            len(matched_sources),
            len(source_batches),
            (perf_counter() - started_at) * 1000,
        )

    all_chunks.sort(key=lambda c: c.distance)
    return all_chunks[:k]


def retrieve_context(
    query: str,
    stores: Sequence[FileStorePort],
    glob: str | None = None,
) -> str | None:
    """Retrieved chunks formatted as an injectable context block, or None if empty."""
    chunks = retrieve(query, stores, glob=glob)
    if not chunks:
        return None
    body = "\n\n---\n\n".join(
        f"[{c.filestore_display} ({c.filestore_uid}) : {c.source} "
        f"(chars {c.location.char_start}-{c.location.char_end})]\n{c.text}"
        for c in chunks
    )
    return f"[CONTEXT FROM KNOWLEDGE BASE]\n{body}\n[END CONTEXT]"
