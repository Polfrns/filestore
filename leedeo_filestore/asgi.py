import os

from django.core.asgi import get_asgi_application
from starlette.types import Receive, Scope, Send

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'leedeo_filestore.settings')

# get_asgi_application() calls django.setup() — must happen before importing
# anything that touches Django models (including transport.server).
django_app = get_asgi_application()

from transport.server import mcp_app  # noqa: E402


async def application(scope: Scope, receive: Receive, send: Send) -> None:
    if scope["type"] == "lifespan" or scope.get("path", "").startswith("/mcp"):
        await mcp_app(scope, receive, send)
    else:
        await django_app(scope, receive, send)