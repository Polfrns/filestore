"""Filestore creation invariants shared by REST and MCP."""
import re
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User

from filestores.creation import (
    UID_GENERATION_ATTEMPTS,
    create_filestore_record,
    provision_local_filestore,
)
from filestores.models import FileStore, FileStoreKind, generate_filestore_uid


@pytest.fixture
def owner(db):
    return User.objects.create_user("owner")


def _create_record(owner: User, display_name: str = "Documents") -> FileStore:
    return create_filestore_record(
        owner=owner,
        display_name=display_name,
        description="",
        kind=FileStoreKind.LOCAL,
        ref_for_uid=lambda uid: f"/stores/{uid}",
        read_only=False,
        shared=False,
        ephemeral=False,
    )


class TestUidGeneration:
    def test_generates_eight_lowercase_alphanumeric_characters(self):
        for _ in range(100):
            assert re.fullmatch(r"[a-z0-9]{8}", generate_filestore_uid())

    def test_retries_a_database_uid_collision(self, owner):
        FileStore.objects.create(uid="same0001", display_name="Existing", ref="/existing")

        with patch(
            "filestores.creation.generate_filestore_uid",
            side_effect=["same0001", "next0001"],
        ):
            record = _create_record(owner)

        assert record.uid == "next0001"

    def test_fails_loudly_after_bounded_collision_retries(self, owner):
        FileStore.objects.create(uid="same0001", display_name="Existing", ref="/existing")

        with patch(
            "filestores.creation.generate_filestore_uid",
            return_value="same0001",
        ) as generate:
            with pytest.raises(RuntimeError, match="unique filestore uid"):
                _create_record(owner)

        assert generate.call_count == UID_GENERATION_ATTEMPTS


class TestRecordCreation:
    def test_requires_a_non_empty_display_name(self, owner):
        with pytest.raises(ValueError, match="display_name is required"):
            _create_record(owner, "   ")

    def test_duplicate_display_names_are_allowed(self, owner):
        first = _create_record(owner, "Same Name")
        second = _create_record(owner, "Same Name")

        assert first.uid != second.uid
        assert first.display_name == second.display_name == "Same Name"


class TestLocalProvisioning:
    def test_uses_generated_uid_for_backing_directory(self, owner, tmp_path):
        record = provision_local_filestore(
            owner=owner,
            root=tmp_path,
            display_name="Documents",
            description="",
            read_only=False,
            shared=False,
        )

        assert record.ref == str(tmp_path / record.uid)
        assert (tmp_path / record.uid).is_dir()
        assert record.ephemeral is True

    def test_removes_registry_record_when_directory_creation_fails(self, owner, tmp_path):
        with patch("filestores.creation.Path.mkdir", side_effect=OSError("disk unavailable")):
            with pytest.raises(OSError, match="disk unavailable"):
                provision_local_filestore(
                    owner=owner,
                    root=tmp_path,
                    display_name="Documents",
                    description="",
                    read_only=False,
                    shared=False,
                )

        assert not FileStore.objects.filter(display_name="Documents").exists()