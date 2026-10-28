"""Auth helpers for the REST API layer.

Deliberately self-contained — does NOT import from transport to avoid a
circular dependency (transport already imports api.models).
"""
from __future__ import annotations

import functools

from django.http import HttpRequest, JsonResponse

from api.models import APIKey


def get_bearer_user(request: HttpRequest):
    auth = request.META.get("HTTP_AUTHORIZATION", "")
    if not auth.lower().startswith("bearer "):
        return None
    token = auth[7:].strip()
    try:
        return APIKey.objects.select_related("user").get(key=token).user
    except APIKey.DoesNotExist:
        return None


def require_api_key(view_fn):
    """Decorator: resolves Bearer token → user, attaches as request.auth_user.

    Returns 401 if the token is missing or invalid.
    """
    @functools.wraps(view_fn)
    def wrapper(request: HttpRequest, *args, **kwargs):
        user = get_bearer_user(request)
        if user is None:
            return JsonResponse({"error": "Unauthorized."}, status=401)
        request.auth_user = user
        return view_fn(request, *args, **kwargs)
    return wrapper