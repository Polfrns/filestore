# Changelog

## 1.0.0 — Workspace MCP

- Replace the legacy reusable filestore with signed, per-conversation workspaces and a stateless MCP endpoint.
- Add Linux bubblewrap execution, startup filesystem/network canaries, bounded REST attachment upload/output delivery, workspace quotas and TTL.
- Run Python document preparation and validation tools inside the sandbox.
- Add Docker Compose deployment for the same Linux host as Roko and Bridge; no host port is published.
- Roko-side chat tools, global MCP settings, model filtering and legacy sessions/filestore removal remain a separate repository integration.
