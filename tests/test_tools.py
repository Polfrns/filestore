"""Tool-layer invariant tests.

We patch _get_store to return a FakeFileStore so that these tests stay pure
in-memory — no DB, no filesystem. What we're testing is the tool LOGIC, not
the adapter. The adapter has its own tests in test_filestore_port.py.
"""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from transport import server
from tests.fakes import FakeFileStore


def _make_store(uid: str = "test", **kwargs) -> FakeFileStore:
    return FakeFileStore(uid, **kwargs)


def _patch_store(store):
    return patch("transport.server._get_store", new=AsyncMock(return_value=store))


class TestCreateFilestore:
    async def test_delegates_to_shared_provisioning(self, settings):
        user = object()
        record = SimpleNamespace(
            uid="abc123xy",
            display_name="Project Files",
            read_only=False,
        )
        with (
            patch("transport.server._get_user", return_value=user),
            patch("filestores.creation.provision_local_filestore", return_value=record) as provision,
        ):
            result = await server.create_filestore(
                "Project Files",
                description="Current project documents",
                shared=True,
            )

        provision.assert_called_once_with(
            owner=user,
            root=Path(settings.FILESTORE_ROOT),
            display_name="Project Files",
            description="Current project documents",
            read_only=False,
            shared=True,
        )
        assert "uid: abc123xy" in result


class TestCreateFile:
    async def test_creates_new_file_and_returns_confirmation(self):
        store = _make_store()
        with _patch_store(store):
            result = await server.create_file("test", "hello.txt", "world")
        assert "Created" in result
        assert store.read_text("hello.txt") == "world"

    async def test_refuses_to_overwrite_existing_file(self):
        store = _make_store()
        store.seed("hello.txt", "existing content")
        with _patch_store(store):
            with pytest.raises(ValueError, match="already exists"):
                await server.create_file("test", "hello.txt", "new content")

    async def test_existing_file_content_unchanged_after_refused_overwrite(self):
        store = _make_store()
        store.seed("hello.txt", "original")
        with _patch_store(store):
            try:
                await server.create_file("test", "hello.txt", "overwrite")
            except ValueError:
                pass
        assert store.read_text("hello.txt") == "original"

    async def test_raises_permission_error_on_read_only_store(self):
        store = _make_store(read_only=True)
        with _patch_store(store):
            with pytest.raises(PermissionError):
                await server.create_file("test", "new.txt", "data")


class TestStrReplaceFile:
    async def test_successful_replacement_returns_confirmation(self):
        store = _make_store()
        store.seed("file.txt", "hello world")
        with _patch_store(store):
            result = await server.str_replace_file("test", "file.txt", "world", "Python")
        assert "Edited" in result
        assert store.read_text("file.txt") == "hello Python"

    async def test_raises_value_error_when_string_not_found(self):
        store = _make_store()
        store.seed("file.txt", "hello world")
        with _patch_store(store):
            with pytest.raises(ValueError):
                await server.str_replace_file("test", "file.txt", "missing", "x")

    async def test_raises_value_error_when_string_is_ambiguous(self):
        store = _make_store()
        store.seed("file.txt", "foo foo foo")
        with _patch_store(store):
            with pytest.raises(ValueError, match="3 times"):
                await server.str_replace_file("test", "file.txt", "foo", "bar")

    async def test_file_unchanged_after_failed_replacement(self):
        store = _make_store()
        store.seed("file.txt", "foo foo foo")
        with _patch_store(store):
            try:
                await server.str_replace_file("test", "file.txt", "foo", "bar")
            except ValueError:
                pass
        assert store.read_text("file.txt") == "foo foo foo"


class TestDeleteFile:
    async def test_delete_existing_file_returns_confirmation(self):
        store = _make_store()
        store.seed("bye.txt", "gone")
        with _patch_store(store):
            result = await server.delete_file("test", "bye.txt")
        assert "Deleted" in result
        with pytest.raises(FileNotFoundError):
            store.read_whole("bye.txt")

    async def test_delete_missing_file_raises_value_error(self):
        store = _make_store()
        with _patch_store(store):
            with pytest.raises(ValueError, match="not found"):
                await server.delete_file("test", "ghost.txt")


class TestListFiles:
    async def test_returns_tree_for_non_empty_store(self):
        store = _make_store()
        store.seed("readme.txt", "hello")
        with _patch_store(store):
            result = await server.list_files("test")
        assert "readme.txt" in result

    async def test_returns_no_files_message_for_empty_store(self):
        store = _make_store()
        with _patch_store(store):
            result = await server.list_files("test")
        assert "No files found" in result

    async def test_returns_only_entries_matching_glob(self):
        store = _make_store()
        store.seed("docs/SPEC-api.md", "spec").seed("docs/notes.txt", "notes")
        with _patch_store(store):
            result = await server.list_files("test", glob="**/SPEC*.md")

        assert "SPEC-api.md" in result
        assert "notes.txt" not in result

    async def test_no_match_message_names_glob(self):
        store = _make_store()
        with _patch_store(store):
            result = await server.list_files("test", glob="**/*.pdf")

        assert "**/*.pdf" in result


class TestSearchFiles:
    async def test_searches_only_files_matching_glob(self):
        store = _make_store()
        store.seed("docs/a.txt", "needle").seed("notes.txt", "needle")
        with _patch_store(store):
            result = await server.search_files("test", "needle", glob="docs/**/*.txt")

        assert "docs/a.txt" in result
        assert "notes.txt" not in result


class TestSearchKnowledge:
    async def test_forwards_one_glob_across_all_selected_stores(self):
        first = _make_store("first")
        second = _make_store("second")
        get_store = AsyncMock(side_effect=[first, second])
        with (
            patch("transport.server._get_store", new=get_store),
            patch("kb.engine.retrieve", return_value=[]) as retrieve,
        ):
            result = await server.search_knowledge(
                "braking",
                ["first", "second"],
                glob="**/SPEC*.md",
                top_k=3,
            )

        retrieve.assert_called_once_with(
            "braking",
            [first, second],
            glob="**/SPEC*.md",
            k=3,
        )
        assert "No relevant content" in result