# leedeo-filestore - System Specification

An MCP server that gives LLM hosts access to named, reusable filestores (local
directories currently, Microsoft SharePoint / OneDrive in the future) with an
automatically maintained knowledge base (KB) over their contents. Host-agnostic:
the same server works equally behind any MCP-capable host (chat UIs, Claude
Desktop, custom agents, etc.), and its core operations are also reachable via a
companion REST API for non-MCP callers.

---

## Architecture overview

```
MCP host (any MCP-capable client)        REST caller (admin clients, scripts, etc.)
  │  MCP over HTTP (Streamable HTTP)       │  HTTP JSON
  │  MCP over stdio (dev / Claude Desktop) │
  ▼                                        ▼
leedeo-filestore  [Django + PostgreSQL]
  ├── transport/    - MCP transport layer (tool registry, request dispatch)
  │    └── management/commands/run_mcp_stdio.py
  ├── api/          - Management REST API (see SPEC-leedeo-filestore-api.md)
  ├── filestores/   - FileStore port + adapters, domain, registry model
  │    ├── domain.py        - FileStore port interface + rich default methods
  │    ├── adapters/
  │    │    ├── local.py    - LocalFileStore (server-reachable path)
  │    │    └── graph.py    - GraphFileStore (OneDrive / SharePoint) [DESIGNED, NOT YET BUILT]
  │    └── models.py        - FileStore registry (metadata only)
  └── kb/           - ChromaDB collections, staleness tracking, lazy re-ingest
       ├── domain.py        - EmbeddingPort interface + chunk/result types
       ├── adapters/
       │    └── chroma_default.py  - ChromaDB default embedding (all-MiniLM-L6-v2)
       ├── models.py        - KBIndex (per-file ingest metadata)
       └── engine.py        - query, diff, ingest, cite

PostgreSQL   - FileStore registry, KBIndex, users, API keys
ChromaDB     - one collection per filestore (store_{uid}), persisted to chroma_db/
EmbeddingPort - pluggable embedding backend (provider-neutral; see below)
```

---

## Two-level design: port interface vs. tool interface

There are two distinct levels that should not be conflated:

**The FileStore port** - what adapter implementers implement. A minimal set of
primitives that every backend (local filesystem, Graph drive) must support. The
seam between the tool layer and the storage backend.

**The MCP tools** - what the LLM sees. Ergonomically shaped for the model:
binary formats are transparently converted to Markdown, edits are expressed as
surgical replacements rather than full rewrites. These tools call into the port
(sometimes via rich default methods), but they are not a 1:1 mapping of port
primitives.

`filestore_uid` is an application-layer dispatch concern: the tool layer looks up
the right registry record and adapter instance by uid, then calls its methods.
The server generates this opaque, immutable identifier; callers receive and copy
it but never choose or change it. The human-readable `display_name` is used to
select a store, while the uid makes the subsequent dispatch unambiguous. The uid
is registry identity, not a backend-supplied identifier; adapters may carry it as
application context for KB collection naming but never generate or mutate it.

---

## The FileStore port

A provider-neutral interface; every adapter implements these primitives and nothing
above the adapter line knows which backend it is talking to.

The interface is the intersection of what all backends can do - not local filesystem richness, not Graph richness:

```python
list(glob: str | None = None) -> list[FileEntry]
stat(path: str) -> FileEntry
read_whole(path: str) -> bytes
create_whole(path: str, content: bytes) -> None
write_whole(path: str, content: bytes) -> None
delete(path: str) -> None
mkdir(path: str) -> None
move(src: str, dst: str) -> None
```

`glob` is the sole selector for listing and searching files. Glob patterns match
complete store-relative paths with POSIX `/` separators, regardless of backend:

- omitted `glob` selects every entry recursively
- `regulations/**/*.pdf` selects PDFs at any depth below `regulations`
- `regulations/2026/*.md` selects immediate Markdown children of that directory
- `**/SPEC*.md` selects matching files anywhere, including at the store root
- an exact relative path such as `reports/final.pdf` selects that entry only
- `*` and `?` do not cross `/`; `**` matches zero or more path segments;
  character classes use the usual glob syntax
- absolute patterns and any `..` path component are rejected
- no matches returns an empty result, not an error

`list` returns matching files and directories. Operations that search contents
ignore matching directories. Adapters must implement these semantics identically;
backend-native glob behavior is not itself the contract. An adapter may derive a
literal traversal root from the glob for efficiency, but that is an implementation
detail and does not reintroduce a separate public `path` selector. An entry that
disappears during enumeration may be omitted, but permission, metadata, and other
backend failures propagate rather than silently producing an incomplete manifest.

Exact paths use the same store-relative POSIX convention. Empty, absolute,
drive-qualified, backslash-containing, NUL-containing, and `..` paths are
rejected before adapter I/O. `stat` returns metadata without reading content.
`create_whole` exclusively creates a new file and raises `FileExistsError` if a
file or directory wins the destination; this final adapter operation, rather than
a separate existence check, owns the no-overwrite guarantee. `write_whole`
deliberately retains overwrite semantics for explicit editing operations.

Rich operations are default methods over the primitives, optionally overridden
by an adapter for native efficiency. The tool layer always calls the rich methods;
which implementation runs is invisible above the adapter line:

- `read_text(path)` → `read_whole` + decode
- `write_text(path, text)` → encode + `write_whole`
- `read_lines(path, start, end)` → `read_whole` + slice
- `str_replace(path, old, new)` → `read_whole` + replace + `write_whole`
  (LocalFileStore overrides this for efficient in-place writes; Graph falls back
  to the default read-modify-write)
- `read_as_markdown(path)` → `read_whole` + MarkItDown conversion (see *File
  format libraries*); plain text files pass through unchanged
- `search(query, glob)` → `list(glob)` + literal substring search over matching
  readable text files; known binary extensions and undecodable content are
  skipped, while storage and permission failures propagate

Capability metadata (on the FileStore instance, not the interface):
- `read_only: bool` - write/delete operations raise if True
- `is_remote: bool` - semantic hint for display purposes; no behavioural effect
  above the adapter line

There are two adapters:

`LocalFileStore` - roots all operations under a server-reachable path (including
  LAN/UNC shares like `\\fileserver\projects\metrorey`). "Local" means local to
  the server; client-laptop directories are out of scope (use a LAN share or
  OneDrive instead). Strict traversal guard on every operation - no path can
  escape above the root.

`GraphFileStore` - a Microsoft Graph drive (OneDrive personal/business or any
  SharePoint / Teams document library - all `driveItem` drives under the Graph
  API). **Designed but not yet built.** The port interface above is defined with
  this adapter fully in mind. When it lands, it plugs in with no changes above the
  adapter line. Auth model: delegated OAuth (acting on behalf of the authenticated
  user) - the server forwards the user's token to Graph API calls; no app-level or
  elevated permissions. Delta cursor handling for efficient staleness detection and
  page-aware PDF chunking are deferred to the implementation spike. Requires Azure
  AD app registration and IT authorization before work begins. See *Designed for
  but not yet built*.

---

## Data models

### `FileStore`

The registry - metadata only. Files themselves live on the backend, this model
never stores individual file records.

| Field | Type | Notes |
|---|---|---|
| `id` | `BigAutoField` (PK) | |
| `uid` | `CharField(8, unique=True, editable=False, default=generate_filestore_uid)` | Server-generated, opaque, immutable public resource identifier matching `[a-z0-9]{8}`. Used in REST routes, tool calls, backing paths for provisioned local stores, and ChromaDB collection names. Callers cannot supply or patch it. |
| `display_name` | `CharField(150)` | Required, mutable human-readable label. It is not resource identity and need not be globally unique. |
| `description` | `TextField` (optional) | What this store contains - shown to the LLM via `list_filestores` and searched by `search_filestores` so it can pick the right store without reading files; optional but recommended |
| `kind` | `FileStoreKind` (`TextChoices`) | `LOCAL` or `GRAPH` - enum-typed in code, string-backed in DB |
| `ref` | `TextField` | Root path (local) or drive identifier (graph) |
| `read_only` | `BooleanField` | If True, write/delete/mkdir/move are blocked at dispatch |
| `shared` | `BooleanField` | `True` → accessible to all authenticated users; `False` → owner only |
| `ephemeral` | `BooleanField` | `True` → `DELETE` removes the backing storage; `False` → use `/deregister` to remove registry entry only. Provisioned stores are ephemeral; registered stores are not. |
| `owner` | FK → `User` (nullable) | Required when `shared=False`; null for shared stores |
| `created_at` | `DateTimeField(auto_now_add)` | |
| `updated_at` | `DateTimeField(auto_now)` | Updated on every save; used for newest-first ordering and stable discovery tie-breaking |

**Access rule:** a user may access a FileStore if `shared=True` OR (`shared=False`
AND `owner == request.user`). Enforced at dispatch before any operation.

The numeric `id` is an internal database primary key and is never exposed through
REST or MCP. The `uid` is public but opaque: accessible REST clients and the LLM
receive it as the stable handle for later calls, while human-facing interfaces
normally display `display_name`. A uid is not secret and carries no user-meaningful
name or authorization information.

`generate_filestore_uid` uses `secrets.choice` over lowercase ASCII letters and
digits. Lowercase-only identifiers avoid case-folding collisions when a uid is
used as a directory name on case-insensitive filesystems. The database unique
constraint is the source of truth for collision detection: the shared creation
operation attempts the insert, catches a uid-specific uniqueness violation,
generates a new uid, and retries a small bounded number of times before failing
loudly. A pre-insert existence check is not sufficient because it is race-prone.

### `KBIndex`

Tracks what has been ingested into ChromaDB, per file per filestore. Used to
derive staleness at query time - no manual freshness flag, no background jobs.

| Field | Type | Notes |
|---|---|---|
| `id` | `BigAutoField` (PK) | |
| `filestore` | FK → `FileStore` | |
| `file_path` | `TextField` | Relative path within the filestore |
| `file_mtime` | `FloatField` | mtime at last ingest (local); Graph delta token for Graph stores |
| `ingested_at` | `DateTimeField` | When this file was last ingested |

One row per file per filestore. Removed when the file is deleted from the backend
(detected during the staleness diff at query time).

---

## File format libraries

All reading goes through **MarkItDown** (Microsoft, MIT licence) - one library
converts pdf / docx / xlsx / pptx / html / images into clean Markdown for the
model. Plain text files pass through unchanged. Token-efficient and format-agnostic
from the model's perspective.

Editing (per format), via thin LLM-facing wrappers over:
- **openpyxl** - xlsx cell/range editing
- **python-docx** - docx paragraph/table editing
- **pypdf** / **PyMuPDF** - PDF reading, annotation, form-filling. Note: PyMuPDF
  is AGPL-3.0 - acceptable for an internal LAN deployment; revisit if the server
  is ever distributed or offered as a hosted service. pypdf (BSD) + pdfplumber
  (MIT) cover most PDF read needs without AGPL if needed.
- **reportlab** - PDF generation (produce-from-scratch flows)
- stdlib `csv` / **pandas** - CSV; large tables must not be dumped whole to the
  model (incomprehensible at scale), so CSV-specific tools exposing ranges, column
  slices, and filtered views are required alongside plain reading

PDF in-place text editing is out of scope (generate or fill, do not edit).

---

## Knowledge base (KB) - self-sustaining, no manual management

All filestores are KB-indexed and queryable at all times. The system maintains
the index automatically; no management commands are required for normal operation.

**ChromaDB layout:** one collection per filestore, named `store_{uid}`, persisted
to `chroma_db/`. Each chunk carries a `ChunkLocation` (a typed dataclass,
serialised to ChromaDB metadata dict):

```python
@dataclass
class ChunkLocation:
    char_start: int
    char_end: int
    page: int | None        # populated for paginated formats (pdf, pptx)
    section: str | None     # best-effort section heading, e.g. "4.3.2"
```

**Embeddings:** behind an `EmbeddingPort` - a provider-neutral interface so the
embedding backend is swappable without touching the KB engine. Currently wired
to `ChromaDefaultEmbeddingAdapter` (all-MiniLM-L6-v2 via onnxruntime, ships with
chromadb - no extra install, no external API). This is a dev-suitable adapter;
see `scratchpad/PLAN.md` ("Embedding model quality") for the production validation
plan before this is considered settled.

**Staleness detection and lazy re-ingest** (triggered at `search_knowledge` call time):
1. List all files in the target filestore(s) via the FileStore adapter (`list()`
  is a cheap directory scan - the diff cost is O(n) mtime comparisons). Retain
  this typed current-file manifest for query scoping; glob filtering must not
  perform a second backend scan.
2. Diff against `KBIndex`: find new files (no row), modified files (mtime
   changed), and deleted files (row present but file gone).
3. Re-ingest new/modified files only: chunk by fixed character window, embed,
  upsert into ChromaDB, update `KBIndex`. This is the expensive step - it only
   runs for the diff, not the whole store.
4. Remove chunks for deleted files from ChromaDB; delete their `KBIndex` rows.
5. If a glob was supplied, match it against file paths in the current manifest
  and constrain ChromaDB by each chunk's `source` metadata before vector ranking.
  If nothing matches, return no results without querying ChromaDB. If every
  searchable file matches, omit the metadata constraint.
6. Query ChromaDB; merge results by distance score; return top-k with full
  `ChunkLocation` citations.

Normal case (no files changed): step 1 is a fast `readdir`, step 2 finds no diff,
steps 3-4 are skipped entirely, steps 5-6 are the only remaining work. The first query on
a freshly attached filestore pays the full ingest cost; this is unavoidable and is
the right tradeoff (no background jobs, no scheduler, no stale-index risk).

ChromaDB does not interpret filesystem globs. The KB engine translates matching
manifest paths into `source` metadata predicates. A reasonably sized source set
uses one prefiltered query. A large set is divided into bounded batches; each
batch returns its own top-k candidates, and those candidates are merged by
distance to obtain the exact global top-k. Post-filtering an unscoped top-k is
incorrect and is not permitted because scoped candidates may rank below excluded
unscoped results.

**Cross-store aggregation:** when `search_knowledge` targets multiple filestores, each
collection is queried independently then results are merged by distance score.
Valid because all collections share one `EmbeddingPort` (same vector space).
One supplied glob is applied independently to every selected filestore; it does
not imply that their directory structures are analogous. Per-filestore selectors
are deferred until a concrete need justifies a structured scopes interface.

**Auto-inject is a host concern.** The server exposes `search_knowledge` as an MCP tool;
calling it before each turn (auto-inject) is a host-layer decision, not enforced
here. There is no REST endpoint for semantic search - that surface is MCP-only.

---

## Tools and REST API

For the full interface contract - all MCP tools (parameters, return shapes,
error behaviour) and all REST endpoints (routes, request/response shapes, auth,
error codes) - see **`SPEC-leedeo-filestore-api.md`**.

Conceptual notes that belong here:

- All tool calls authenticate the caller and enforce the access rule
  (shared vs. owner) before any operation. `read_only` is enforced at dispatch
  for all write-class operations.
- All file tools take `filestore_uid` - the tool layer resolves this to an
  accessible registry record and adapter instance. The model selects by
  `display_name` and description, then copies the corresponding opaque uid into
  later calls.
- MCP discovery returns a consistent human-readable entry format from both tools,
  backed internally by one typed filestore summary and formatter.
  `list_filestores` returns the complete accessible set;
  `search_filestores(query, limit=10)` returns a bounded subset after access
  filtering. Search is case-insensitive over literal `display_name` and
  `description` substrings. Ranking is exact display name, display-name prefix,
  display-name substring, description substring, then newest `updated_at`; uid
  ascending is the final deterministic tie-breaker. Whitespace-only queries and
  limits outside `1..100` are errors.
- File listing, literal search, and semantic search use the same optional,
  store-relative glob contract. There is no separate directory `path` selector.
- REST file transfer uses exact store-relative paths rather than globs. Uploads
  refuse overwrite, and downloads inspect `stat` before reading content.
- Provisioning and registration generate the uid server-side. No REST or MCP
  creation operation accepts a caller-selected uid, and uid is never editable.
- The current tool set covers text files. Binary editing (docx, xlsx, csv ranges)
  is deferred but explicitly in scope - see *Designed for but not yet built*.
- `read_file` uses `read_as_markdown` - binary formats (pdf, docx, xlsx, …) are
  transparently converted to clean Markdown; plain text passes through unchanged.
- `str_replace_file` requires the target string to be unique in the file.
  Ambiguous matches are an explicit error, not a silent multi-replace.
- Semantic search (`search_knowledge`) is MCP-only. There is no REST endpoint for it.

---

## Auth

There is one auth mechanism: a token that identifies the caller as a Microsoft
identity. When MS OAuth is live, this is the user's OAuth access token. For local
development and testing, an API key stands in for it - treated as if it were the
MS token, so the shape of the auth layer does not change when OAuth lands.

**For Graph stores:** the token is forwarded to Graph API calls, and the server
acts on behalf of the user with their own Microsoft permissions. This is standard
OAuth2 delegated access - the token is the user's, scoped to what they authorised.
No separate permission mapping is maintained server-side for Graph stores.

**For local stores:** the token is resolved to a Django `User` (by API key lookup
now; by validating the JWT / fetching the MS user identity later), and the
owner/shared access rule is applied.

**Stdio transport:** no auth. For local development and Claude Desktop wiring only;
never exposed over a network.

---

## Transport

**Primary:** Streamable HTTP (MCP 2025 spec). Django runs in ASGI mode (`uvicorn`
or `daphne`); the MCP router is mounted alongside Django's URL dispatcher.
Supports multiple concurrent callers.

**Secondary (dev/testing):** stdio via `manage.py run_mcp_stdio`. Useful for
Claude Desktop integration during development.

**Management REST API:** plain HTTP endpoints for filestore registry management,
file tree inspection, and binary upload/download. Mounted under `/api/`. Registry
operations and errors use JSON; downloads return the successful file body as
binary. Full endpoint reference in `SPEC-leedeo-filestore-api.md`.

REST upload and download are temporarily limited to 100 MiB
(`100 * 1024 * 1024` bytes) because the current `FileStore` port buffers whole
files. Both directions use one shared configured limit. Oversized requests return
HTTP 413 with a stable JSON error explaining that streaming support is pending.
Upload size is rejected before conversion to a Python `bytes` value; download
size is rejected from `stat` metadata before `read_whole`.

---

## Designed for but not yet built

**`GraphFileStore`** - the FileStore port interface is defined with this adapter
fully in mind. Nothing in the tool layer, KB engine, or data models assumes
local-only. When the adapter lands, it plugs in with no changes above the adapter
line. Auth model: delegated OAuth (acting on behalf of the authenticated user).
Delta cursor handling for staleness and page-aware PDF chunking are deferred to
the implementation spike. Requires Azure AD app registration and IT authorization
before work begins.

**MS OAuth login** - shares the same Azure AD app registration as the Graph
adapter. Deferred to the same sprint; the API-key stand-in is explicitly designed
to be replaced by this without touching the auth layer's interface.

**Projects / richer access model** - per-project filestore grouping, per-user
lists beyond the owner/shared binary. The current model is intentionally simple
and additive; this slots in later without breaking existing behaviour.

**Binary file editing** - `str_replace_file` and `create_file` cover text files.
Structured editing for docx (python-docx), xlsx cell/range editing (openpyxl),
and CSV range/slice tools (pandas) are deferred but explicitly in scope. Reading
these formats already works via MarkItDown.

**CSV range tools** - large-table slicing and column projection. Deferred but
explicitly planned; plain `read_file` on a large CSV is not acceptable.

**Streaming file transfer** - REST upload and download currently buffer one
whole file through the port and therefore enforce the explicit 100 MiB limit.
Streaming reads and writes are a future `FileStore` capability; raising the limit
without adding that capability is not acceptable.

---

## Explicitly out of scope

- SharePoint / OneDrive access (Graph adapter - see above)
- Per-project filestore scoping (host / UI layer concern for now)
- xlsx cell editing, pdf in-place text editing
- Auto-inject at the server layer (host concern)
- Manual KB / ChromaDB management commands (system is self-sustaining)
- Deletion guard beyond `read_only` (will be revisited)

## Observabilidad v0.7.1

Observabilidad v0.7.1: exportación privada, contexto HMAC por servicio y operaciones basadas en eventos reales. Véanse CHANGELOG.md y LIMITATIONS.md. Los permisos y datos enviados al modelo no cambian.


## Observabilidad v0.7.2

Modo redacted/debug en exportador OTLP, con contexto acotado de herramientas y documentos de read_file siempre excluidos. Contratos y permisos MCP sin cambios. Variables de modo propagadas por Compose. Scripts externos de despliegue intactos.

Las pruebas nuevas comprueban límites de contenido, filtrado antes de exportar y no mutación de datos. Roko ejecuta processStream real sobre un grafo de prueba y comprueba trazas, títulos, usuarios, errores y cancelación. Bridge verifica por HTTP la firma y el padre remoto con SDK real. Filestore mantiene sus 12 herramientas y pruebas de acceso. Véanse TEST-RESULTS.md y OBSERVABILITY-V072.md.
