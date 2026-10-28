from __future__ import annotations

import contextvars

from django.contrib.auth.models import User

_current_user: contextvars.ContextVar[User | None] = contextvars.ContextVar(
    "current_user", default=None
)


def get_current_user() -> User | None:
    return _current_user.get()


def resolve_user(token: str) -> User | None:
    """Look up a Django User by API key. Returns None if the key is invalid."""
    from api.models import APIKey
    try:
        return APIKey.objects.select_related("user").get(key=token).user
    except APIKey.DoesNotExist:
        return None