# TESTS.md

## Running the suite

```
pytest
```

`pytest.ini` limits default discovery to `tests/`, so diagnostic scripts under
`scratchpad/` are not collected. pytest-django creates the test database and
applies committed migrations automatically. DB tests opt in through the `db`
fixture (directly or through another fixture).

---

## `tests/fakes.py` — Shared test infrastructure

`FakeFileStore` is a second concrete implementation of the `FileStore` port.
It inherits from `FileStore(Protocol)`, which gives it all eight primitive
slots plus all the rich default methods (str_replace, search, read_text, …)
from the Protocol's own implementations. It stores file content in a plain
dict and fulfills all port invariants without touching the filesystem. Its
`list` primitive delegates to the shared glob validator and matcher so it is
a faithful second consumer of that contract rather than a test-only glob
implementation.

`FakeEmbeddingPort` returns a deterministic fixed-dimension vector. Designed
for KB tests that need an embedding that's consistent within a single run
but doesn't require an LLM.

---

## `tests/test_filestore_port.py` — Port contract

**What it enforces:** The eight primitives behave correctly, and the rich
default methods built on those primitives work correctly when inherited.
Because `FakeFileStore` inherits from the Protocol rather than implementing
it structurally, these tests also double as protocol-contract tests: if a
test passes for `FakeFileStore` but fails for `LocalFileStore`, the adapter
is deviating from the port contract.

Groups:
- **TestPrimitives** — read/write roundtrip, FileNotFoundError on missing
  path, delete, mkdir, move, list with and without a glob; `stat` returns exact
  file/directory metadata and raises FileNotFoundError for a missing path;
  `create_whole` creates once and preserves existing files/directories
- **TestReadOnlyEnforcement** — all five write-path primitives (`create_whole`,
  `write_whole`, delete, mkdir, move) raise PermissionError on a read-only store
- **TestRichDefaults** — str_replace happy path; ValueError when string is
  absent or appears more than once (ambiguous); read_text/write_text UTF-8
  roundtrip; read_lines slice; search finds matching lines, skips binary or
  undecodable content, and propagates storage failures; read_as_markdown falls
  back to read_text for plain text

---

## `tests/test_glob_contract.py` — Cross-adapter path and glob contract

**What it enforces:** `FakeFileStore` and `LocalFileStore` expose identical,
backend-independent path metadata and glob semantics over complete
store-relative POSIX paths. The same parameterized cases run against both
adapters so filesystem-native behavior cannot leak into the public contract.

Groups:
- **TestGlobSelection** — omitted glob recursively lists files and directories;
  exact file and directory paths work; `*` and `?` stay within one path segment;
  `**` matches zero or more segments; character classes work; unmatched patterns
  return an empty list; empty, absolute/drive-qualified, backslash, NUL, and
  traversal patterns raise `ValueError`
- **TestGlobScopedLiteralSearch** — literal search reads only matching files and
  returns no results when the glob selects no files
- **TestStatConformance** — exact files and directories return equivalent
  `FileEntry` metadata, while missing paths raise `FileNotFoundError`; local
  metadata lookup does not read file contents
- **TestExactPathValidation** — exact paths reject non-relative or malformed
  values without banning legal colons in later path segments
- **TestLocalFailureSemantics** — exclusive Local creation has one winner under
  concurrency; files disappearing during enumeration are tolerated, while
  permission and metadata failures propagate instead of shrinking the manifest

---

## `tests/test_file_transfer.py` — REST binary file transfer (DB + filesystem)

**What it enforces:** authenticated REST callers can upload and download exact
binary files without weakening the shared owner/access rule, overwriting existing
entries, or buffering content after a request is already known to exceed the
configured temporary limit. Successful cases use the real local adapter; spies
make negative I/O ordering observable without allocating 100 MiB test payloads.

Groups:
- **TestTransferAccess** — both endpoints require authentication and hide private
  stores from other users before adapter resolution; read-only stores reject
  uploads before storage access
- **TestUpload** — multipart bytes are written unchanged to the explicit nested
  destination rather than the multipart filename; existing files/directories are
  not overwritten and conflicts are detected before reading the upload body;
  exclusive creation catches a destination that appears after the preliminary
  check; required fields and exact POSIX path validation return 400; the
  configured limit is inclusive; oversized declared bodies are rejected before
  `read`, and oversized actual bytes are rejected before `create_whole`
- **TestDownload** — returned bytes are identical, known MIME types are inferred,
  unknown types fall back to `application/octet-stream`, and responses are
  attachments; missing/invalid/directory paths return the documented status;
  the configured limit is inclusive; oversized `stat` metadata prevents
  `read_whole`, with a second guard against content growing after metadata lookup
- **TestTransferConfiguration** — the production buffered-transfer limit remains
  exactly `100 * 1024 * 1024` bytes (100 MiB)

---

## `tests/test_tools.py` — Tool-layer invariants

**What it enforces:** The tool functions in `transport/server.py` uphold
their documented guarantees, independent of the underlying adapter. `_get_store`
is patched to return a `FakeFileStore` so these tests run with no DB and no
filesystem I/O.

Groups:
- **TestCreateFilestore** — MCP creation delegates to the shared provisioning
  boundary with all caller metadata and returns the generated uid
- **TestCreateFile** — creates file; raises `ValueError` if file already
  exists (no silent overwrite); original content untouched after refused
  overwrite; raises `PermissionError` on read-only store
- **TestStrReplaceFile** — successful replacement; `ValueError` on missing
  string; `ValueError` on ambiguous string; file unchanged after failure
- **TestDeleteFile** — successful delete; `ValueError` if file not found
- **TestListFiles** — returns tree string for non-empty store; returns "No
  files found" message for empty store; filters output by glob and identifies
  an unmatched glob in the empty result
- **TestSearchFiles** — applies the glob before literal search and excludes
  matching text in files outside the selected paths
- **TestSearchKnowledge** — forwards the same glob, selected stores, and top-k
  limit to semantic retrieval

---

## `tests/test_filestore_discovery.py` — MCP filestore discovery

**What it enforces:** bounded search helps the model select an opaque filestore
uid from a large accessible set without changing the unbounded list tool or
exposing private registry records. Ranking is explicit and tested independently
from the ORM and human-readable MCP formatting so database ordering and display
changes cannot silently redefine relevance.

Groups:
- **TestDiscoveryRanking** — matching is literal and case-insensitive across
  display name and description; exact name, name prefix, name substring, and
  description substring have decreasing priority regardless of recency; duplicate
  names use newest `updated_at` then uid ascending; the default and explicit
  limits are applied after ranking; blank queries, booleans, non-integers, and
  values outside `1..100` are rejected
- **TestDiscoveryFormatting** — typed summaries produce the canonical readable
  uid, permissions, local/remote type, and optional-description entry format
- **TestDiscoveryTools** — list remains unbounded; list and search use the same
  entry format; ranked search and no-match messages are clear; invalid inputs
  fail before the accessible-record query
- **TestDiscoveryAccessBoundary** — access filtering occurs before matching and
  ranking, so an inaccessible exact-name match neither appears nor outranks an
  accessible description match

---

## `tests/test_access_rules.py` — Access control (DB, HTTP level)

**What it enforces:** The rule "a filestore is accessible when `shared=True`
OR `owner=current_user`" is a security invariant. These tests hit the full
HTTP stack (view decorator → SQL query) so that regressions anywhere in that
chain are caught.

Groups:
- **TestAuthentication** — unauthenticated request and invalid token both
  return 401
- **TestListAccessRule** — shared store appears for both owner and non-owner;
  private store appears only for owner, not other users
- **TestDetailAccessRule** — owner can GET a private store; non-owner gets 404
- **TestDeleteAccessRule** — owner can DELETE an ephemeral store; non-owner gets
  403 on a shared store; non-owner gets 404 on a private store
- **TestPatch** — owner can update `displayName`; non-owner gets 403; empty
  `displayName` returns 400
- **TestDeregister** — owner can deregister (record removed, returns
  `deregistered: true`); non-owner gets 403 on shared store; non-owner gets
  404 on private store
- **TestEphemeralDelete** — owner gets 403 when attempting to DELETE a shared
  store or a non-ephemeral private store (guard: only `ephemeral=True` stores
  may be fully deleted via DELETE)
- **TestRegister** — valid registration returns a generated `[a-z0-9]{8}` uid;
  caller-supplied uid is ignored; duplicate display names are allowed; missing
  display name returns 400; creator becomes the owner record
- **TestProvision** — backing directory and `ref` use the generated uid;
  provisioned stores are ephemeral; caller-supplied uid is ignored; missing
  display name returns 400
- **TestFilesEndpoint** — file list returns expected paths (including nested);
  unauthenticated request returns 401; non-owner gets 404 on private store

---

## `tests/test_filestore_creation.py` — Creation invariants (DB + filesystem)

**What it enforces:** REST and MCP share one creation boundary that owns opaque
uid generation, collision handling, and local backing-directory provisioning.
The database unique constraint, rather than a race-prone pre-check, decides
whether a generated uid collides.

Groups:
- **TestUidGeneration** — generated uids match `[a-z0-9]{8}`; a forced database
  collision retries with a new uid; repeated collisions stop after the bounded
  attempt count and fail loudly
- **TestRecordCreation** — display name is required but need not be unique
- **TestLocalProvisioning** — local backing directory is named with the generated
  uid; stores are marked ephemeral; a directory-creation failure removes the
  registry record instead of leaving partial state

---

## `tests/test_auth.py` — Auth token resolution (DB)

**What it enforces:** `APIKey.generate` produces valid keys; `resolve_user`
(transport layer) correctly maps tokens to users; `get_bearer_user` (REST API
layer) parses the `Authorization: Bearer` header and delegates to the same
table. Both layers are tested here because they share a failure mode: if the
APIKey table structure changes, both break.

Groups:
- **TestAPIKeyModel** — generate creates one key, key is long and URL-safe,
  label is stored, two calls produce distinct keys
- **TestResolveUser** — valid key returns correct user; invalid key and empty
  string both return None
- **TestGetBearerUser** — valid Bearer header returns user; missing header,
  non-Bearer scheme, and invalid token all return None

---

## `tests/test_kb_chunking.py` — Chunking math (pure unit)

**What it enforces:** `_chunk()` in `kb/engine.py` is the only purely
functional piece of the KB pipeline. Testing it precisely is cheap and
catches regressions in the overlap arithmetic without needing ChromaDB or an
embedding model.

Groups:
- **TestChunkEdgeCases** — empty string and whitespace-only return `[]`;
  single word, short text, and text at exactly `CHUNK_SIZE` each produce a
  single chunk with correct `(start, end)` boundaries
- **TestChunkOverlap** — two consecutive chunks overlap by exactly
  `CHUNK_OVERLAP` characters; the last chunk's end equals `len(text)` (no
  trailing text dropped); every character position in the source text appears
  in at least one chunk (no gaps); all chunk texts are non-empty strings

---

## `tests/test_kb_retrieval.py` — Semantic retrieval scope (pure unit)

**What it enforces:** semantic glob scope is resolved from each store's current
source manifest and sent to Chroma as a candidate filter before distance ranking.
The scripted collection makes filtering, batching, and ranking observable without
a live Chroma instance, database, or embedding model.

Groups:
- **TestSemanticGlobScope** — no matching sources skip Chroma entirely; omitted
  glob queries all sources without a `where` clause; partial scope uses a
  `source.$in` prefilter; large source sets are sorted into bounded batches;
  results from all batches and stores are merged into the exact global top-k;
  one glob is resolved independently for every selected store; invalid patterns
  fail before embedding, freshness scans, or backend queries

## Observabilidad v0.7.1

Observabilidad v0.7.1 protege clasificación, ciclo de vida, agrupación de sesiones, contexto de servicio autenticado y privacidad antes de OTLP. Véanse TEST-RESULTS.md para comandos/resultados y VERIFY-LANGFUSE.md para verificación de despliegue.


## Observabilidad v0.7.2

Modo redacted/debug en exportador OTLP, con contexto acotado de herramientas y documentos de read_file siempre excluidos. Contratos y permisos MCP sin cambios. Variables de modo propagadas por Compose. Scripts externos de despliegue intactos.

Las pruebas nuevas comprueban límites de contenido, filtrado antes de exportar y no mutación de datos. Roko ejecuta processStream real sobre un grafo de prueba y comprueba trazas, títulos, usuarios, errores y cancelación. Bridge verifica por HTTP la firma y el padre remoto con SDK real. Filestore mantiene sus 12 herramientas y pruebas de acceso. Véanse TEST-RESULTS.md y OBSERVABILITY-V072.md.
