# Workspace service deployment

Deploy on the Linux VM that already runs Roko and Bridge. Do not deploy to the legacy filestore VM. Compose publishes no host port and expects a separate Docker network shared with Roko.

## 1. Prepare Docker networking

Create the private network once:

    docker network create --internal roko-workspace

Attach only the Roko api service and roko-workspace container to this network. Configure Roko with ROKO_WORKSPACE_URL=http://roko-workspace:8080 and the same ROKO_WORKSPACE_KEY as this service.

The workspace container also joins a separate Compose egress network for Langfuse. Apply host firewall rules to allow only the configured Langfuse address and port. The private roko-workspace network is internal and has no outside route.

## 2. Configure and build

Generate a random secret with at least 32 characters, set the same value as ROKO_WORKSPACE_KEY in this service and Roko, and create the local environment file:

    cp .env.example .env
    openssl rand -hex 32

Put the generated value in both .env files. Fill Langfuse credentials only if exporting traces. Keep OBSERVABILITY_CONTENT_MODE=redacted unless bounded debug content is deliberately required.

Check Compose without printing its expanded configuration, then build and start:

    docker compose -f compose.yaml config -q
    docker compose -f compose.yaml build roko-workspace
    docker compose -f compose.yaml up -d roko-workspace
    docker compose -f compose.yaml ps
    docker compose -f compose.yaml logs --tail=50 roko-workspace

The process listens on container port 8080 only. A healthy container means the startup canary passed its workspace write, outside read/write, inputs/ read-only, and network probes. If it remains unhealthy, inspect its logs and run npm run check:workspace inside the image. Execution stays disabled while the canary fails.

## 3. Storage and permissions

The named volume persists workspace data and the service removes idle workspaces after 30 minutes. Put the Docker volume on a filesystem with a 20 GiB quota; application quotas do not stop a shell command temporarily filling the filesystem.

The service runs bubblewrap with Linux user, PID, IPC, UTS, and network namespaces. It drops all capabilities inside the sandbox, applies a seccomp filter against nested user namespaces, io_uring, and VSOCK, and exposes only the current workspace as writable. The service container drops all capabilities except SYS_ADMIN, uses no-new-privileges, and runs read-only except /data and /tmp. It does not mount the Docker socket or provider credentials.

## 4. Rollback

Stop this Compose service and restore its prior image/configuration. Keep the Docker volume unless workspace data should be discarded. Remove the roko-workspace Docker network only after Roko no longer references it.

No production server has been changed from this development environment.

## Roko integration still required

This repository cannot deploy the Roko-side changes. Before enabling the MCP, Roko needs signed per-request async context, the global MCP toggle, Bridge model filter, explicit attach_chat_file and deliver_workspace_file tools, and the conversation deletion hook described in scratchpad/DISENO-workspace-mcp.md section 11. The existing attachment workflow must perform ownership and access checks before calling this service.
