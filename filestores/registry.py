from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.db.models import Q

from .adapters.local import LocalFileStore
from .domain import FileStore as FileStorePort, StoreContext
from .models import FileStore as FileStoreModel, FileStoreKind


def get_adapter(store: FileStoreModel) -> FileStorePort:
    """Instantiate the right adapter for a FileStore registry record."""
    if store.kind == FileStoreKind.LOCAL:
        path = Path(store.ref)
        if not path.is_absolute():
            base_dir = Path(getattr(settings, "MEDIA_ROOT", Path.cwd() / "uploads"))
            path = base_dir / path
        return LocalFileStore(
            uid=store.uid,
            root_path=path,
            display_name=store.display_name,
            read_only=store.read_only,
        )
    raise NotImplementedError(f"FileStore kind '{store.kind}' has no adapter yet.")


def load_user_stores(user) -> StoreContext:
    """Load adapters for all filestores accessible to user (shared + owned)."""
    records = FileStoreModel.objects.filter(Q(shared=True) | Q(owner=user))
    adapters = tuple(get_adapter(r) for r in records)
    return StoreContext(stores=adapters)