# Roko workspace MCP

## Purpose

Provide a private, disposable filesystem and Linux execution environment for each Roko conversation. Roko authenticates every MCP and REST request with a fresh HMAC-signed identity. The service never selects a conversation when identity is missing.

## Runtime and deployment

- Node.js 24, MCP SDK 1.32.2, Zod 4.6.7, bubblewrap, Python 3.
- Linux x86_64 or ARM64 container only. Docker Compose runs on the same Linux VM as Roko and Bridge; other architectures fail closed because the seccomp filter has no architecture mapping.
- No published host port. Roko reaches http://roko-workspace:8080 over external Docker network roko-workspace.
- The service also joins its one-service egress network for Langfuse. Host firewall rules must restrict egress to the configured Langfuse endpoint. The sandbox itself has no network.
- Shell commands and scripts/file-tools.py run in a bubblewrap user, PID, IPC, UTS, and network namespace. The sandbox sees a read-only runtime root and only the current workspace from `/data`; `inputs/` is rebound read-only by directory descriptor. A seccomp filter blocks nested user namespaces, VSOCK sockets, and io_uring. Startup checks workspace writes, sibling data denial, read-only inputs/, blocked nested namespaces, and failed TCP connections to the container's own address/port and 1.1.1.1:53. Failure keeps /health at 503 and rejects execution.

## Identity contract

Every MCP and REST request carries Authorization: Bearer <ROKO_WORKSPACE_KEY>, x-roko-context (base64url JSON, at most 4096 characters), and x-roko-signature (lowercase hex HMAC-SHA256 of the encoded context).

Derive ctxKey = HMAC-SHA256(ROKO_WORKSPACE_KEY, roko-workspace/context/v1). The payload is {v: 1, aud: roko-workspace, tenantId, userId, conversationId, telemetry?, timestamp}. Timestamp is Unix milliseconds and may be at most ten minutes old. telemetry may contain 64-character lowercase hex sessionId and userId pseudonyms. Any invalid credentials, signature, payload or timestamp returns 401 before workspace I/O.

The opaque workspace key uses the HMAC derivation documented in scratchpad/DISENO-workspace-mcp.md section 4. Raw tenant, user and conversation ids are never directory names.

The MCP endpoint is stateless. Every POST creates a new Streamable HTTP transport with no session id. GET SSE returns 405. No MCP tool accepts a workspace identifier.

## Workspace and limits

/data/w/<opaque-key>/ contains meta.json and workspace/. The workspace has inputs/, work/, outputs/, and .exec/{home,tmp}. Roko owns inputs/; file tools reject writes there. Bubblewrap receives the workspace through an open directory descriptor and mounts inputs/ read-only through a second descriptor. The architecture-specific seccomp filter lives under `/data/.runtime/` and reaches bubblewrap as a read-only descriptor.

| Limit | Default |
|---|---:|
| Idle TTL | 30 minutes |
| Sweep interval | 60 seconds |
| Workspace size | 512 MiB |
| Files per workspace | 5,000 |
| Global workspace bytes | 20 GiB |
| Uploaded file | 100 MiB |
| Concurrent commands per user | 2 |
| Concurrent commands globally | 4 |
| Executions per workspace | 1 |
| Deliveries per workspace | 10 |
| Deliverable file size | 15 MiB |

The named Docker volume must have a host filesystem quota. Service-side quotas cannot stop an arbitrary shell command from temporarily filling the volume before its post-execution check.

## MCP tools

list_files(glob?), read_file(path,start_line?,end_line?), write_file(path,content), edit_file(path,old,new), execute(command,description?,timeout_ms?), prepare_file(path), convert(path,output), recalc(path,output), and validate(path).

All paths are workspace-relative POSIX paths beginning with inputs/, work/, or outputs/. Absolute paths, drive paths, backslashes, NUL, dot/dot-dot, empty segments, and symbolic links are rejected. write_file and edit_file accept only work/ or outputs/. get_output is not an MCP tool.

execute accepts 1–16,000 characters and a 1,000–120,000 ms timeout (60,000 ms default). Combined stdout/stderr is capped at 1 MiB. One command runs per workspace. Queued calls wait at most 30 seconds. Results include exit status, duration, bounded output, workspace usage, and over-quota state.

read_file returns up to 2,000 lines and 100 KiB per call. list_files returns at most 500 entries. File conversion and validation use administrator-installed Python dependencies inside the same sandbox.

## REST contract

All /v1/workspace/* routes require the same signed identity. Errors are JSON {code,message}.

- PUT /v1/workspace/inputs/{filename} requires Content-Length, streams no more than 100 MiB, writes to inputs/, and never overwrites; collisions receive -2, -3, and so on. Returns 201 {path,bytes}.
- GET /v1/workspace/outputs/{path} only serves a regular, non-symlink, non-empty output up to 15 MiB. PDF/PNG/JPEG signatures are checked; other formats run file-tools.py validate. Returns bytes plus Content-Type, Content-Length, and encoded X-Workspace-Filename. At most ten deliveries are allowed before expiry.
- DELETE /v1/workspace cancels active/queued work and deletes the conversation workspace; returns 204, including when it did not exist.
- GET /health is public and returns 200 only after the startup sandbox check; otherwise 503.

Roko exposes attach_chat_file(file_id) to upload selected chat attachments and deliver_workspace_file(path) to validate/download outputs and attach them to chat. MCP tools do not create chat attachments directly.

## Observability

When Langfuse credentials are configured, the service emits one span per MCP tool and REST operation using signed pseudonymous telemetry identifiers. A remote parent is accepted only when OBSERVABILITY_TRACE_SECRET verifies its signature, it is no older than 60 seconds, and its session/user ids match the signed Roko identity.

The privacy export processor filters every span. Paths, file contents, shell commands, edit text, and binary data are never included. Content mode defaults to redacted; tracing failures are fail-open.

## Roko responsibilities outside this repository

Roko must globally enable this MCP by default for non-Bridge models, allow users to disable it in global MCP settings, and omit it for claude-max/gpt-codex. Its shared MCP connection must sign every HTTP request from the active per-call async context. Roko also owns the two chat file tools, workspace deletion on conversation deletion, global MCP UI behavior, and removal of legacy sessions/filestore UI and backend code. See scratchpad/DISENO-workspace-mcp.md section 11.
