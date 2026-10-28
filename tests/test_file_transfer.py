"""REST binary transfer tests across authentication, storage, and size boundaries."""
from __future__ import annotations

import json
from inspect import unwrap
from unittest.mock import Mock, patch

import pytest
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.http import HttpRequest, QueryDict
from django.test import Client
from django.utils.datastructures import MultiValueDict

from api.models import APIKey
from api.views import filestore_file_upload
from filestores.domain import FileEntry
from filestores.models import FileStore


TOO_LARGE_ERROR = (
    "Files larger than 100 MB are not currently supported because file transfers "
    "are buffered in memory. This is a temporary limitation pending streaming "
    "upload and download support."
)


@pytest.fixture
def alice(db) -> User:
    return User.objects.create_user("alice", password="test")


@pytest.fixture
def bob(db) -> User:
    return User.objects.create_user("bob", password="test")


@pytest.fixture
def alice_key(alice: User) -> APIKey:
    return APIKey.generate(alice, label="alice-key")


@pytest.fixture
def bob_key(bob: User) -> APIKey:
    return APIKey.generate(bob, label="bob-key")


@pytest.fixture
def transfer_store(alice: User, tmp_path) -> FileStore:
    return FileStore.objects.create(
        uid="transfer",
        display_name="Transfer",
        ref=str(tmp_path),
        shared=False,
        owner=alice,
    )


def auth(key: APIKey) -> dict[str, str]:
    return {"HTTP_AUTHORIZATION": f"Bearer {key.key}"}


def upload_url(store: FileStore) -> str:
    return f"/api/filestores/{store.uid}/files/upload"


def download_url(store: FileStore) -> str:
    return f"/api/filestores/{store.uid}/files/download"


class NeverReadUpload:
    def __init__(self, size: int, content: bytes = b"") -> None:
        self.size = size
        self._content = content
        self.read_calls = 0

    def read(self) -> bytes:
        self.read_calls += 1
        if not self._content:
            raise AssertionError("Oversized upload body must not be read")
        return self._content


def call_upload_view(
    user: User,
    store: FileStore,
    path: str,
    uploaded_file: NeverReadUpload,
):
    request = HttpRequest()
    request.method = "POST"
    request.POST = QueryDict(mutable=True)
    request.POST["path"] = path
    request.FILES = MultiValueDict({"file": [uploaded_file]})
    setattr(request, "auth_user", user)
    return unwrap(filestore_file_upload)(request, store.uid)


class TestTransferAccess:
    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("post", "/api/filestores/transfer/files/upload"),
            ("get", "/api/filestores/transfer/files/download?path=file.bin"),
        ],
    )
    def test_authentication_precedes_adapter_access(self, transfer_store, method, path):
        with patch("api.views.get_adapter") as get_adapter:
            response = getattr(Client(), method)(path)

        assert response.status_code == 401
        get_adapter.assert_not_called()

    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("post", "/api/filestores/transfer/files/upload"),
            ("get", "/api/filestores/transfer/files/download?path=file.bin"),
        ],
    )
    def test_private_store_is_hidden_before_adapter_access(
        self,
        transfer_store,
        bob_key,
        method,
        path,
    ):
        with patch("api.views.get_adapter") as get_adapter:
            response = getattr(Client(), method)(path, **auth(bob_key))

        assert response.status_code == 404
        get_adapter.assert_not_called()

    def test_read_only_store_rejects_upload_before_adapter_access(
        self,
        transfer_store,
        alice_key,
    ):
        transfer_store.read_only = True
        transfer_store.save(update_fields=["read_only"])

        with patch("api.views.get_adapter") as get_adapter:
            response = Client().post(upload_url(transfer_store), **auth(alice_key))

        assert response.status_code == 403
        get_adapter.assert_not_called()

    def test_authenticated_non_owner_can_transfer_with_shared_store(
        self,
        transfer_store,
        bob_key,
    ):
        transfer_store.shared = True
        transfer_store.save(update_fields=["shared"])

        upload = Client().post(
            upload_url(transfer_store),
            data={
                "path": "shared.bin",
                "file": SimpleUploadedFile("shared.bin", b"shared content"),
            },
            **auth(bob_key),
        )
        download = Client().get(
            download_url(transfer_store),
            data={"path": "shared.bin"},
            **auth(bob_key),
        )

        assert upload.status_code == 201
        assert download.status_code == 200
        assert download.content == b"shared content"


class TestUpload:
    def test_uploads_binary_content_to_explicit_nested_path(
        self,
        transfer_store,
        alice_key,
        tmp_path,
    ):
        content = b"\x00\xffbinary\r\ncontent"
        uploaded = SimpleUploadedFile("ignored-name.bin", content)

        response = Client().post(
            upload_url(transfer_store),
            data={"path": "reports/result.bin", "file": uploaded},
            **auth(alice_key),
        )

        assert response.status_code == 201
        assert response.json() == {"path": "reports/result.bin", "bytes": len(content)}
        assert (tmp_path / "reports" / "result.bin").read_bytes() == content
        assert not (tmp_path / "ignored-name.bin").exists()

    def test_refuses_to_overwrite_existing_file(
        self,
        transfer_store,
        alice_key,
        tmp_path,
    ):
        target = tmp_path / "existing.bin"
        target.write_bytes(b"original")

        response = Client().post(
            upload_url(transfer_store),
            data={
                "path": "existing.bin",
                "file": SimpleUploadedFile("replacement.bin", b"replacement"),
            },
            **auth(alice_key),
        )

        assert response.status_code == 409
        assert target.read_bytes() == b"original"

    def test_refuses_to_overwrite_existing_directory(
        self,
        transfer_store,
        alice_key,
        tmp_path,
    ):
        (tmp_path / "existing").mkdir()

        response = Client().post(
            upload_url(transfer_store),
            data={
                "path": "existing",
                "file": SimpleUploadedFile("file.bin", b"content"),
            },
            **auth(alice_key),
        )

        assert response.status_code == 409

    def test_detects_conflict_before_reading_upload_body(
        self,
        transfer_store,
        alice,
        settings,
    ):
        settings.FILE_TRANSFER_MAX_BYTES = 3
        uploaded = NeverReadUpload(size=3)
        adapter = Mock()
        adapter.stat.return_value = FileEntry(
            path="existing.bin",
            size=1,
            is_dir=False,
            mtime=0.0,
        )

        with patch("api.views.get_adapter", return_value=adapter):
            response = call_upload_view(alice, transfer_store, "existing.bin", uploaded)

        assert response.status_code == 409
        assert uploaded.read_calls == 0
        adapter.create_whole.assert_not_called()

    @pytest.mark.parametrize("data", [{}, {"path": "file.bin"}])
    def test_requires_path_and_file(self, transfer_store, alice_key, data):
        response = Client().post(
            upload_url(transfer_store),
            data=data,
            **auth(alice_key),
        )

        assert response.status_code == 400

    @pytest.mark.parametrize(
        "path",
        [
            "/absolute.bin",
            "C:/absolute.bin",
            "../escape.bin",
            r"docs\file.bin",
            "bad\x00name.bin",
        ],
    )
    def test_rejects_invalid_destination_path(self, transfer_store, alice_key, path):
        response = Client().post(
            upload_url(transfer_store),
            data={"path": path, "file": SimpleUploadedFile("file.bin", b"content")},
            **auth(alice_key),
        )

        assert response.status_code == 400

    def test_accepts_file_exactly_at_configured_limit(
        self,
        transfer_store,
        alice_key,
        settings,
        tmp_path,
    ):
        settings.FILE_TRANSFER_MAX_BYTES = 3

        response = Client().post(
            upload_url(transfer_store),
            data={"path": "limit.bin", "file": SimpleUploadedFile("file.bin", b"123")},
            **auth(alice_key),
        )

        assert response.status_code == 201
        assert (tmp_path / "limit.bin").read_bytes() == b"123"

    def test_rejects_oversized_upload_before_reading_body(
        self,
        transfer_store,
        alice,
        settings,
    ):
        settings.FILE_TRANSFER_MAX_BYTES = 3
        uploaded = NeverReadUpload(size=4)

        with patch("api.views.get_adapter") as get_adapter:
            response = call_upload_view(alice, transfer_store, "large.bin", uploaded)

        assert response.status_code == 413
        assert json.loads(response.content) == {"error": TOO_LARGE_ERROR}
        assert uploaded.read_calls == 0
        get_adapter.assert_not_called()

    def test_rechecks_actual_bytes_before_writing(
        self,
        transfer_store,
        alice,
        settings,
    ):
        settings.FILE_TRANSFER_MAX_BYTES = 3
        uploaded = NeverReadUpload(size=3, content=b"1234")
        adapter = Mock()
        adapter.stat.side_effect = FileNotFoundError

        with patch("api.views.get_adapter", return_value=adapter):
            response = call_upload_view(alice, transfer_store, "large.bin", uploaded)

        assert response.status_code == 413
        assert uploaded.read_calls == 1
        adapter.create_whole.assert_not_called()

    def test_concurrent_conflict_at_create_returns_409(
        self,
        transfer_store,
        alice,
    ):
        uploaded = NeverReadUpload(size=3, content=b"new")
        adapter = Mock()
        adapter.stat.side_effect = FileNotFoundError
        adapter.create_whole.side_effect = FileExistsError

        with patch("api.views.get_adapter", return_value=adapter):
            response = call_upload_view(alice, transfer_store, "raced.bin", uploaded)

        assert response.status_code == 409
        assert uploaded.read_calls == 1
        adapter.create_whole.assert_called_once_with("raced.bin", b"new")


class TestDownload:
    def test_downloads_byte_identical_content_as_attachment(
        self,
        transfer_store,
        alice_key,
        tmp_path,
    ):
        content = b"\x00\xffbinary\r\ncontent"
        (tmp_path / "report.bin").write_bytes(content)

        response = Client().get(
            download_url(transfer_store),
            data={"path": "report.bin"},
            **auth(alice_key),
        )

        assert response.status_code == 200
        assert response.content == content
        assert response.headers["Content-Type"] == "application/octet-stream"
        disposition = response.headers["Content-Disposition"]
        assert disposition.startswith("attachment;")
        assert "report.bin" in disposition

    def test_infers_known_content_type(self, transfer_store, alice_key, tmp_path):
        (tmp_path / "report.pdf").write_bytes(b"%PDF")

        response = Client().get(
            download_url(transfer_store),
            data={"path": "report.pdf"},
            **auth(alice_key),
        )

        assert response.status_code == 200
        assert response.headers["Content-Type"] == "application/pdf"

    def test_unknown_extension_uses_binary_content_type(
        self,
        transfer_store,
        alice_key,
        tmp_path,
    ):
        (tmp_path / "payload.unknownextension").write_bytes(b"content")

        response = Client().get(
            download_url(transfer_store),
            data={"path": "payload.unknownextension"},
            **auth(alice_key),
        )

        assert response.status_code == 200
        assert response.headers["Content-Type"] == "application/octet-stream"

    def test_requires_path(self, transfer_store, alice_key):
        response = Client().get(download_url(transfer_store), **auth(alice_key))

        assert response.status_code == 400

    @pytest.mark.parametrize(
        "path",
        [
            "/absolute.bin",
            "C:/absolute.bin",
            "../escape.bin",
            r"docs\file.bin",
            "bad\x00name.bin",
        ],
    )
    def test_rejects_invalid_source_path(self, transfer_store, alice_key, path):
        response = Client().get(
            download_url(transfer_store),
            data={"path": path},
            **auth(alice_key),
        )

        assert response.status_code == 400

    def test_missing_file_returns_404(self, transfer_store, alice_key):
        response = Client().get(
            download_url(transfer_store),
            data={"path": "missing.bin"},
            **auth(alice_key),
        )

        assert response.status_code == 404

    def test_directory_returns_400(self, transfer_store, alice_key, tmp_path):
        (tmp_path / "directory").mkdir()

        response = Client().get(
            download_url(transfer_store),
            data={"path": "directory"},
            **auth(alice_key),
        )

        assert response.status_code == 400

    def test_accepts_file_exactly_at_configured_limit(
        self,
        transfer_store,
        alice_key,
        settings,
        tmp_path,
    ):
        settings.FILE_TRANSFER_MAX_BYTES = 3
        (tmp_path / "limit.bin").write_bytes(b"123")

        response = Client().get(
            download_url(transfer_store),
            data={"path": "limit.bin"},
            **auth(alice_key),
        )

        assert response.status_code == 200
        assert response.content == b"123"

    def test_rejects_oversized_metadata_before_reading_content(
        self,
        transfer_store,
        alice_key,
        settings,
    ):
        settings.FILE_TRANSFER_MAX_BYTES = 3
        adapter = Mock()
        adapter.stat.return_value = FileEntry(
            path="large.bin",
            size=4,
            is_dir=False,
            mtime=0.0,
        )

        with patch("api.views.get_adapter", return_value=adapter):
            response = Client().get(
                download_url(transfer_store),
                data={"path": "large.bin"},
                **auth(alice_key),
            )

        assert response.status_code == 413
        assert response.json() == {"error": TOO_LARGE_ERROR}
        adapter.read_whole.assert_not_called()

    def test_rechecks_actual_bytes_before_returning_content(
        self,
        transfer_store,
        alice_key,
        settings,
    ):
        settings.FILE_TRANSFER_MAX_BYTES = 3
        adapter = Mock()
        adapter.stat.return_value = FileEntry(
            path="changing.bin",
            size=3,
            is_dir=False,
            mtime=0.0,
        )
        adapter.read_whole.return_value = b"1234"

        with patch("api.views.get_adapter", return_value=adapter):
            response = Client().get(
                download_url(transfer_store),
                data={"path": "changing.bin"},
                **auth(alice_key),
            )

        assert response.status_code == 413
        assert response.json() == {"error": TOO_LARGE_ERROR}


class TestTransferConfiguration:
    def test_default_buffered_transfer_limit_is_100_mib(self, settings):
        assert settings.FILE_TRANSFER_MAX_BYTES == 100 * 1024 * 1024
