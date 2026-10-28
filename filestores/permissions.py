from django.db.models import Q

RANK = {"read": 1, "write": 2, "admin": 3}


def base_role(user, store) -> str | None:
    if user.is_superuser or store.owner_id == user.id:
        return "admin"
    return (
        store.access.filter(user=user).values_list("role", flat=True).first()
        or ("read" if store.shared else None)
    )


def role_for(user, store) -> str | None:
    """Effective role for data operations (read_only caps everyone at read)."""
    role = base_role(user, store)
    if role and store.read_only and RANK[role] > 1:
        role = "read"
    return role


def visible_stores(user):
    from .models import FileStore
    if user.is_superuser:
        return FileStore.objects.all()
    return FileStore.objects.filter(
        Q(owner=user) | Q(shared=True) | Q(access__user=user)
    ).distinct()


def require(user, store, needed: str):
    role = role_for(user, store)
    if role is None:
        # Mismo error que "no existe" para no revelar uids ajenos
        raise LookupError(f"Filestore '{store.uid}' not found.")
    if RANK[role] < RANK[needed]:
        raise PermissionError(f"Requires '{needed}' access on '{store.uid}'.")