from __future__ import annotations

import asyncio
import json

from starlette.types import ASGIApp, Receive, Scope, Send

from .trace_context import incoming_trace

from .auth import _current_user, resolve_user


class APIKeyMiddleware:
    """ASGI middleware that resolves a Bearer API key to a Django User.

    Passes lifespan events through untouched. Returns 401 for missing or
    invalid tokens before the request reaches MCPServer.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = {k.lower(): v for k, v in scope.get("headers", [])}
        auth = headers.get(b"authorization", b"").decode("latin-1")

        if not auth.lower().startswith("bearer "):
            await _send_401(send)
            return

        token = auth[7:].strip()
        user = await asyncio.to_thread(resolve_user, token)

        if user is None:
            await _send_401(send)
            return

        ctx_token = _current_user.set(user)
        try:
            with incoming_trace(headers):
                await self.app(scope, receive, send)
        finally:
            _current_user.reset(ctx_token)


async def _send_401(send: Send) -> None:
    body = json.dumps({"error": "Authentication required."}).encode()
    await send({
        "type": "http.response.start",
        "status": 401,
        "headers": [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
            (b"www-authenticate", b'Bearer realm="leedeo-filestore"'),
        ],
    })
    await send({"type": "http.response.body", "body": body})