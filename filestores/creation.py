from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from django.contrib.auth.models import User
from django.db import IntegrityError, transaction

from .models import FileStore, FileStoreKind, generate_filestore_uid


UID_GENERATION_ATTEMPTS = 5


def create_filestore_record(
    *,
    owner: User,
    display_name: str,
    description: str,
    kind: FileStoreKind,
    ref_for_uid: Callable[[str], str],
    read_only: bool,
    shared: bool,
    ephemeral: bool,
) -> FileStore:
    display_name = display_name.strip()
    if not display_name:
        raise ValueError("display_name is required.")

    for _ in range(UID_GENERATION_ATTEMPTS):
        uid = generate_filestore_uid()
        try:
            with transaction.atomic():
                return FileStore.objects.create(
                    uid=uid,
                    display_name=display_name,
                    description=description,
                    kind=kind,
                    ref=ref_for_uid(uid),
                    read_only=read_only,
                    shared=shared,
                    ephemeral=ephemeral,
                    owner=owner,
                )
        except IntegrityError:
            if FileStore.objects.filter(uid=uid).exists():
                continue
            raise
    raise RuntimeError("Could not generate a unique filestore uid.")


def provision_local_filestore(
    *,
    owner: User,
    root: Path,
    display_name: str,
    description: str,
    read_only: bool,
    shared: bool,
) -> FileStore:
    record = create_filestore_record(
        owner=owner,
        display_name=display_name,
        description=description,
        kind=FileStoreKind.LOCAL,
        ref_for_uid=lambda uid: str(root / uid),
        read_only=read_only,
        shared=shared,
        ephemeral=True,
    )
    try:
        Path(record.ref).mkdir(parents=True, exist_ok=True)
    except Exception:
        record.delete()
        raise
    return record