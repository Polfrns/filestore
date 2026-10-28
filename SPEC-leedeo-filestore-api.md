# leedeo-filestore - API contract (REST + MCP)

Specification for the **leedeo-filestore server**. Consumed by any MCP-capable AI tool or administrative client.

Auth: a bearer token that identifies the caller. In production, this will be a
Microsoft OAuth access token (Azure AD delegated auth), API keys are the current
stand-in during development, and carry the same shape - the auth layer does not
change when OAuth lands. Each key/token is resolved to a user identity on every
request.

```
Authorization: Bearer <api-key>
```

## Management REST API

Base path: `/api/filestores`. All endpoints require the bearer. JSON in/out. Fields are camelCase.

The Filestore object:
```json
{
  "uid":         "k7m2p9qx",
  "displayName": "CENELEC Regulations",
  "description": "Full set of CENELEC railway standards.",
  "kind":        "local",
  "ref":         "/data/filestores/k7m2p9qx",
  "readOnly":    true,
  "shared":      true,
  "ephemeral":   false,
  "owner":       "admin",
  "createdAt":   "2026-06-17T10:00:00+00:00",
  "updatedAt":   "2026-06-19T08:00:00+00:00"
}
```

`kind`: `"local"` | `"graph"` (SharePoint/OneDrive, future).
`ref`: backing storage pointer (f)ilesystem path for `local`, drive/site URL for `graph`).
Access rule: a filestore is visible to a caller if `shared=true` OR `owner=caller`.

`uid` is a server-generated eight-character identifier matching `[a-z0-9]{8}`.
It is opaque and immutable, not a human-readable shortname: callers cannot supply
or patch it. Generation uses cryptographically secure randomness; the database
unique constraint detects collisions and the server generates another value and
retries the insert a small bounded number of times. Lowercase-only values remain
unambiguous when used as directory names on case-insensitive filesystems.
REST clients receive it in Filestore responses and use it in routes. MCP callers
receive it from `list_filestores`, `search_filestores`, or `create_filestore` and
copy it into later tool calls. Human-facing interfaces normally show `displayName`; that label is
mutable, need not be globally unique, and is never used as resource identity.
The database primary key remains internal and is not part of either API.

### `GET /api/filestores`
List stores accessible to the caller, newest-updated first.
```json
200 -> { "filestores": [ <Filestore>, ... ] }
```

### `POST /api/filestores`
Provision a new store owned by the caller. The server creates the backing
storage location and assigns both `uid` and `ref` automatically. Provisioned
stores are `ephemeral=true`, `DELETE` will remove both registry entry and backing
storage.
```json
body -> { "displayName": "My Store", "description"?: "...", "shared"?: false, "readOnly"?: false }
201  -> <Filestore>
400  -> displayName missing/empty
```
Defaults: `description=""`, `shared=false`, `readOnly=false`.

### `POST /api/filestores/register`
Register an existing directory (or SharePoint drive) as a filestore. The
backing storage is not created or modified - only the registry entry is added,
with a server-generated `uid`.
Symmetric counterpart to `POST /api/filestores/:uid/deregister`.
```json
body -> { "ref": "/data/existing", "displayName": "My Store", "kind"?: "local", "description"?: "...", "shared"?: false, "readOnly"?: false }
201  -> <Filestore>
400  -> ref or displayName missing/empty
```

### `GET /api/filestores/:uid`
```json
200 -> <Filestore>      404 -> not found or not accessible
```

### `PATCH /api/filestores/:uid`  (owner only)
Editable fields only - `uid`, `kind`, and `ref` are immutable.
```json
body -> { "displayName"?: "...", "description"?: "...", "readOnly"?: bool, "shared"?: bool }
200  -> <Filestore>     400 -> resulting displayName empty     403 -> not owner     404 -> not found or not accessible
```

### `POST /api/filestores/:uid/deregister`  (owner only)
Removes the registry record. Backing storage (the files themselves) is left
untouched. Symmetric counterpart to `POST /api/filestores/register`.
```json
200 -> { "deregistered": true }     403 -> not owner     404 -> not found or not accessible
```

### `DELETE /api/filestores/:uid`  (owner only)
Removes the registry record **and** deletes the backing storage. Only valid for
stores where `ephemeral=true`. Symmetric counterpart to `POST /api/filestores`.
```json
200 -> { "deleted": true }     403 -> not owner or store is not ephemeral     404 -> not found or not accessible
```

### `GET /api/filestores/:uid/files`
The full file tree (recursive), files + directories (incl. empty dirs).
The client builds a UI tree from `path` strings.
```json
200 -> { "files": [ { "path": "a.txt", "bytes": 12, "mtimeMs": 1718000, "type": "file" },
                   { "path": "sub",   "bytes": 0,  "mtimeMs": 1718000, "type": "dir"  } ] }
404 -> not found or not accessible
```
`path` is store-relative, POSIX separators. `mtimeMs` is Unix time in milliseconds.

### `POST /api/filestores/:uid/files/upload`
Upload one binary file without overwriting an existing entry.

```text
Content-Type: multipart/form-data
fields:
  path: complete store-relative destination path
  file: uploaded file body
```

The multipart filename is descriptive metadata only; the server always uses the
explicit `path` field as the destination. The path must use POSIX separators and
must not be absolute, drive-qualified, contain NUL/backslash, or contain `..`.
Authorization is enforced before storage is touched. Read-only stores reject the
operation. Existing files or directories are never overwritten: the adapter's
final exclusive-create operation detects a concurrent conflict even if the
destination appeared after the preliminary metadata check.

```json
201 -> { "path": "reports/result.bin", "bytes": 1234 }
400 -> path or file missing; invalid destination path
403 -> filestore is read-only
404 -> filestore not found or not accessible
409 -> destination already exists
413 -> file exceeds the buffered-transfer limit
```

### `GET /api/filestores/:uid/files/download?path=<store-relative-path>`
Download one exact file. The successful response body is byte-identical to the
stored content. `Content-Type` is inferred best-effort from the source filename,
falling back to `application/octet-stream`. `Content-Disposition` is
`attachment` and includes the source filename.

```json
200 -> binary file body
400 -> path missing or invalid; path identifies a directory
404 -> filestore, path, or accessible file not found
413 -> file exceeds the buffered-transfer limit
```

Upload and download currently buffer whole files and share a temporary limit of
`100 * 1024 * 1024` bytes (100 MiB). A file exactly at the limit is accepted; a
file one byte larger is rejected before whole-file I/O. The 413 JSON body is
stable in both directions:

```json
{
  "error": "Files larger than 100 MB are not currently supported because file transfers are buffered in memory. This is a temporary limitation pending streaming upload and download support."
}
```

---

## MCP server

- **Transport (dev):** stdio - spawned as a child process by the MCP host.
  Identity bound at startup (`--username`).
- **Transport (prod):** streamable-HTTP at `/mcp` (MCP `initialize` handshake,
  then an `mcp-session-id` header on subsequent calls; `POST` for requests,
  `GET`/`DELETE` for the stream/close). Bearer auth on every HTTP request;
  identity resolved live per request.
- **Server name:** `leedeo-filestore`.
- **Result casing: `snake_case`** (model-facing). Errors are returned as a tool
  result with `isError: true` and a human-readable message, not a transport error.

Each filestore tool authorizes its `filestore_uid` against the caller.

| Tool | Params | Returns |
|---|---|---|
| `list_filestores` | - | the caller's complete accessible set as human-readable entries |
| `search_filestores` | `query: string`, `limit?: int = 10` | matching accessible stores in the same entry format, at most `limit` items |
| `create_filestore` | `display_name: string`, `description?: string`, `shared?: bool`, `read_only?: bool` | human-readable creation confirmation including the generated uid |
| `list_files` | `filestore_uid: string`, `glob?: string` | indented directory tree string, or `(empty)` |
| `read_file` | `filestore_uid: string`, `path: string` | file contents as text; binary formats (PDF, DOCX, etc.) converted to Markdown |
| `create_file` | `filestore_uid: string`, `path: string`, `content: string` | `Created <path>`; fails if file already exists |
| `str_replace_file` | `filestore_uid: string`, `path: string`, `old_str: string`, `new_str: string` | `Updated <path>`; fails if `old_str` not found or not unique |
| `create_folder` | `filestore_uid: string`, `path: string` | `Created <path>` |
| `move_file` | `filestore_uid: string`, `src: string`, `dst: string` | `Moved to <dst>` |
| `delete_file` | `filestore_uid: string`, `path: string` | `Deleted <path>` |
| `search_files` | `filestore_uid: string`, `query: string`, `glob?: string` | `path:line: snippet` per hit, or `No matches` |
| `search_knowledge` | `filestore_uids: string[]`, `query: string`, `glob?: string`, `top_k?: int` | ranked semantic search results with source citations and char offsets |

Notes:
- List and search share one typed internal filestore summary and one formatter,
  but their MCP result is intentionally human-readable text. A future structured
  response contract would be a deliberate, versioned protocol change rather than
  a per-tool formatting change.
- `create_filestore` provisions a new store owned by the caller (equivalent to
  `POST /api/filestores`) and returns its server-generated `filestore_uid`.
  Registering an existing directory is an administrative operation not exposed
  via MCP.
- `list_filestores` gives the model the human-readable name and description for
  selection plus the opaque `filestore_uid` it must copy into subsequent calls.
- `search_filestores` trims `query`, rejects an empty result, and matches literal
  substrings case-insensitively in `display_name` and `description`. It ranks an
  exact display name before a display-name prefix, then a display-name substring,
  then a description substring. Within a rank, newer `updated_at` wins and uid
  ascending is the final deterministic tie-breaker. `limit` must be an integer in
  `1..100`; pagination beyond that named maximum is deferred. Access filtering
  occurs before matching and ranking, and no inaccessible store may influence or
  appear in results.
- `create_file` uses the same exclusive-create primitive as REST upload and
  refuses concurrent as well as pre-existing conflicts - no silent overwrites.
- `str_replace_file` requires `old_str` to appear exactly once in the file.
- `search_knowledge` triggers lazy staleness detection and re-ingest before querying.
  First call on a large filestore may be slow.
- `glob` has one meaning across `list_files`, `search_files`, and
  `search_knowledge`: it matches complete store-relative paths using POSIX `/`
  separators. Omitted means all files recursively. `*` and `?` do not cross `/`,
  `**` matches zero or more path segments, and exact relative paths are valid.
  Absolute or drive-qualified patterns, backslashes, NUL, and any `..` component
  are errors; no matches is a clean empty result. Listing includes matching
  directories, while searches ignore them.
- `search_knowledge` applies the same glob independently to every selected
  filestore and filters chunks by source before vector ranking. It never obtains
  an unfiltered top-k and discards nonmatching results afterward.
- `list_filestores` returns the caller's entire accessible set - scoping to a
  session or project is applied by the consuming app, or callers use
  `search_filestores` for bounded metadata discovery.

