"""MCP tool registry — all tools the LLM can call, wired to the filestores port."""
from __future__ import annotations

from functools import partial

from asgiref.sync import sync_to_async
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings

from filestores.discovery import (
    DEFAULT_DISCOVERY_LIMIT,
    FilestoreSummary,
    format_filestore_summaries,
    search_filestore_summaries,
    validate_discovery_query,
)

from .auth import get_current_user
from .middleware import APIKeyMiddleware
from .langfuse_tracing import traced_tool

mcp = MCPServer(
    "leedeo-filestore",
    
)

# Filesystem adapter calls are sync; wrap with this to avoid blocking the event loop.
# thread_sensitive=False: filesystem I/O has no thread affinity (unlike Django ORM).
_run_sync = partial(sync_to_async, thread_sensitive=False)


# ---- Request helpers ----

def _get_user():
    user = get_current_user()
    if user is None:
        raise PermissionError("Authentication required.")
    return user


async def _get_store(filestore_uid: str):
    """Resolve uid → adapter, enforcing the shared/owner access rule."""
    from django.db.models import Q
    from filestores.models import FileStore as FileStoreModel
    from filestores.registry import get_adapter

    user = _get_user()

    def _query():
        try:
            return FileStoreModel.objects.get(
                Q(uid=filestore_uid) & (Q(shared=True) | Q(owner=user))
            )
        except FileStoreModel.DoesNotExist:
            raise ValueError(f"Filestore '{filestore_uid}' not found or not accessible.")

    record = await sync_to_async(_query)()
    return get_adapter(record)


async def _get_accessible_filestore_summaries() -> list[FilestoreSummary]:
    from django.db.models import Q
    from filestores.models import FileStore as FileStoreModel, FileStoreKind

    user = _get_user()

    def _query() -> list[FilestoreSummary]:
        records = FileStoreModel.objects.filter(
            Q(shared=True) | Q(owner=user)
        )
        return [
            FilestoreSummary(
                uid=record.uid,
                display_name=record.display_name,
                description=record.description,
                read_only=record.read_only,
                is_remote=record.kind == FileStoreKind.GRAPH,
                updated_at=record.updated_at,
            )
            for record in records
        ]

    return await sync_to_async(_query)()


def _format_tree(entries) -> str:
    if not entries:
        return "(empty)"
    lines = []
    for e in entries:
        depth = e.path.count("/")
        name = e.path.rsplit("/", 1)[-1] if "/" in e.path else e.path
        indent = "  " * depth
        suffix = "/" if e.is_dir else f"  ({e.size / 1024:.1f} KB)"
        lines.append(f"{indent}{name}{suffix}")
    return "\n".join(lines)


# ---- Tools ----

@traced_tool(mcp)
async def create_filestore(
    display_name: str,
    description: str = "",
    shared: bool = False,
    read_only: bool = False,
) -> str:
    """Provision a new filestore. The server creates the backing directory and returns the store details."""
    from pathlib import Path
    from django.conf import settings
    from filestores.creation import provision_local_filestore

    user = _get_user()

    def _create():
        return provision_local_filestore(
            owner=user,
            root=Path(settings.FILESTORE_ROOT),
            display_name=display_name,
            description=description,
            read_only=read_only,
            shared=shared,
        )

    record = await sync_to_async(_create)()
    ro = "read-only" if record.read_only else "read-write"
    return f"Created filestore '{record.display_name}' (uid: {record.uid}, {ro})."


@traced_tool(mcp)
async def list_filestores() -> str:
    """List all filestores accessible to the authenticated user."""
    summaries = await _get_accessible_filestore_summaries()
    return format_filestore_summaries(
        summaries,
        heading="Accessible filestores:",
        empty_message="No filestores accessible.",
    )


@traced_tool(mcp)
async def search_filestores(
    query: str,
    limit: int = DEFAULT_DISCOVERY_LIMIT,
) -> str:
    """Find accessible filestores by display name or description."""
    validate_discovery_query(query, limit)
    summaries = await _get_accessible_filestore_summaries()
    matches = search_filestore_summaries(summaries, query, limit)
    return format_filestore_summaries(
        matches,
        heading=f"Filestores matching '{query.strip()}':",
        empty_message=f"No accessible filestores match '{query.strip()}'.",
    )


@traced_tool(mcp)
async def list_files(filestore_uid: str, glob: str | None = None) -> str:
    """List files and directories matching a store-relative glob."""
    store = await _get_store(filestore_uid)
    entries = await _run_sync(store.list)(glob=glob)
    if not entries:
        scope = f" matching '{glob}'" if glob else ""
        return f"No files found{scope} in '{filestore_uid}'."
    return _format_tree(entries)


@traced_tool(mcp)
async def read_file(filestore_uid: str, path: str) -> str:
    """Read a file. Binary formats (pdf, docx, xlsx, pptx) are converted to Markdown."""
    store = await _get_store(filestore_uid)
    try:
        return await _run_sync(store.read_as_markdown)(path)
    except FileNotFoundError:
        raise ValueError(f"'{path}' not found in '{filestore_uid}'.")


@traced_tool(mcp)
async def create_file(filestore_uid: str, path: str, content: str) -> str:
    """Create a new text file. Fails if the file already exists — no silent overwrites."""
    store = await _get_store(filestore_uid)
    try:
        await _run_sync(store.create_whole)(path, content.encode("utf-8"))
    except FileExistsError:
        raise ValueError(
            f"'{path}' already exists in '{filestore_uid}'. "
            "Use str_replace_file to edit it, or delete_file + create_file to replace it."
        )
    except PermissionError as e:
        raise PermissionError(str(e))
    return f"Created '{path}' in '{filestore_uid}' ({len(content)} chars)."


@traced_tool(mcp)
async def create_folder(filestore_uid: str, path: str) -> str:
    """Create a directory (and any missing parents) inside the store."""
    store = await _get_store(filestore_uid)
    try:
        await _run_sync(store.mkdir)(path)
    except PermissionError as e:
        raise PermissionError(str(e))
    return f"Folder '{path}' created in '{filestore_uid}'."


@traced_tool(mcp)
async def move_file(filestore_uid: str, src: str, dst: str) -> str:
    """Move or rename a file or directory within the store."""
    store = await _get_store(filestore_uid)
    try:
        await _run_sync(store.move)(src, dst)
    except FileNotFoundError:
        raise ValueError(f"'{src}' not found in '{filestore_uid}'.")
    except PermissionError as e:
        raise PermissionError(str(e))
    return f"Moved '{src}' → '{dst}' in '{filestore_uid}'."


@traced_tool(mcp)
async def delete_file(filestore_uid: str, path: str) -> str:
    """Delete a file or directory. Blocked on read-only stores."""
    store = await _get_store(filestore_uid)
    try:
        await _run_sync(store.delete)(path)
    except FileNotFoundError:
        raise ValueError(f"'{path}' not found in '{filestore_uid}'.")
    except PermissionError as e:
        raise PermissionError(str(e))
    return f"Deleted '{path}' from '{filestore_uid}'."


@traced_tool(mcp)
async def str_replace_file(filestore_uid: str, path: str, old_str: str, new_str: str) -> str:
    """Surgical in-place edit: replace old_str with new_str.

    Fails explicitly if old_str is not found or appears more than once (ambiguous).
    This is not a find-replace tool — old_str must be unique in the file.
    """
    store = await _get_store(filestore_uid)
    try:
        await _run_sync(store.str_replace)(path, old_str, new_str)
    except FileNotFoundError:
        raise ValueError(f"'{path}' not found in '{filestore_uid}'.")
    except (ValueError, PermissionError) as e:
        raise type(e)(str(e))
    return f"Edited '{path}' in '{filestore_uid}'."


@traced_tool(mcp)
async def search_files(filestore_uid: str, query: str, glob: str | None = None) -> str:
    """Literal substring search over file contents within the store."""
    store = await _get_store(filestore_uid)
    results = await _run_sync(store.search)(query=query, glob=glob)
    if not results:
        return f"No matches for '{query}' in '{filestore_uid}'."
    lines = [f"Matches for '{query}' in '{filestore_uid}':"]
    for file_path, matches in results.items():
        lines.append(f"\n{file_path}:")
        lines.extend(f"  {m}" for m in matches)
    return "\n".join(lines)


@traced_tool(mcp)
async def search_knowledge(
    query: str,
    filestore_uids: list[str],
    glob: str | None = None,
    top_k: int = 5,
) -> str:
    """Semantic search over the knowledge index of one or more filestores.

    Triggers lazy staleness detection and re-ingest before querying.
    Returns ranked chunks with source citations.
    """
    from kb.engine import retrieve

    stores = [await _get_store(uid) for uid in filestore_uids]
    chunks = await sync_to_async(retrieve)(query, stores, glob=glob, k=top_k)
    if not chunks:
        return f"No relevant content found for '{query}'."
    lines = [f"Found {len(chunks)} relevant snippet(s) for '{query}':"]
    for i, c in enumerate(chunks, 1):
        loc = c.location
        lines.append(
            f"\n[{i}] {c.filestore_display} ({c.filestore_uid}) : {c.source} "
            f"(chars {loc.char_start}–{loc.char_end}, distance {c.distance:.3f})"
        )
        lines.append(c.text)
    return "\n".join(lines)


# ASGI app — MCPServer Starlette app wrapped with our API key auth middleware.
# Mounted at /mcp in asgi.py.
mcp_app = APIKeyMiddleware(
    mcp.streamable_http_app(
        stateless_http=True, 
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )
)

# Wire the embedding port for the HTTP path. (The stdio path does this in
# run_mcp_stdio.py instead, after Django setup but before mcp.run().)
from kb.adapters.chroma_default import ChromaDefaultEmbeddingAdapter  # noqa: E402
from kb.engine import set_embedding_port  # noqa: E402
set_embedding_port(ChromaDefaultEmbeddingAdapter())
