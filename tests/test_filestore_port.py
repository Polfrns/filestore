"""Port contract tests using FakeFileStore.

If a test here breaks with LocalFileStore but passes with FakeFileStore (or
vice versa), the port contract is leaking an adapter detail.
"""
from unittest.mock import patch

import pytest

from tests.fakes import FakeFileStore


@pytest.fixture
def store():
    return FakeFileStore("test-store")


@pytest.fixture
def ro_store():
    return FakeFileStore("ro-store", read_only=True)


class TestPrimitives:
    def test_write_and_read_bytes_roundtrip(self, store):
        store.write_whole("hello.txt", b"world")
        assert store.read_whole("hello.txt") == b"world"

    def test_create_whole_creates_new_file(self, store):
        store.create_whole("hello.txt", b"world")

        assert store.read_whole("hello.txt") == b"world"

    @pytest.mark.parametrize("existing_kind", ["file", "directory"])
    def test_create_whole_refuses_existing_entry(self, store, existing_kind):
        if existing_kind == "file":
            store.seed("existing", b"original")
        else:
            store.mkdir("existing")

        with pytest.raises(FileExistsError):
            store.create_whole("existing", b"replacement")

        if existing_kind == "file":
            assert store.read_whole("existing") == b"original"

    def test_read_missing_file_raises_file_not_found(self, store):
        with pytest.raises(FileNotFoundError):
            store.read_whole("doesnotexist.txt")

    def test_delete_removes_file(self, store):
        store.seed("bye.txt", "ciao")
        store.delete("bye.txt")
        with pytest.raises(FileNotFoundError):
            store.read_whole("bye.txt")

    def test_delete_missing_file_raises(self, store):
        with pytest.raises(FileNotFoundError):
            store.delete("ghost.txt")

    def test_mkdir_creates_dir_entry(self, store):
        store.mkdir("subdir")
        entries = store.list()
        assert any(e.path == "subdir" and e.is_dir for e in entries)

    def test_move_relocates_content(self, store):
        store.seed("src.txt", "data")
        store.move("src.txt", "dst.txt")
        assert store.read_whole("dst.txt") == b"data"
        with pytest.raises(FileNotFoundError):
            store.read_whole("src.txt")

    def test_move_missing_src_raises(self, store):
        with pytest.raises(FileNotFoundError):
            store.move("ghost.txt", "other.txt")

    def test_list_returns_seeded_files(self, store):
        store.seed("a.txt", "A").seed("b.txt", "B")
        paths = [e.path for e in store.list()]
        assert "a.txt" in paths and "b.txt" in paths

    def test_list_is_sorted_by_path(self, store):
        store.seed("z.txt", "Z").seed("a.txt", "A").seed("m.txt", "M")
        paths = [e.path for e in store.list()]
        assert paths == sorted(paths)

    def test_list_with_glob_filters_complete_paths(self, store):
        store.seed("docs/readme.txt", "R").seed("notes.txt", "N")
        entries = store.list(glob="docs/**/*.txt")
        assert all(e.path.startswith("docs/") for e in entries)
        assert not any(e.path == "notes.txt" for e in entries)

    def test_stat_returns_exact_file_metadata(self, store):
        store.seed("docs/report.bin", b"\x00\x01\x02", mtime=123.5)

        entry = store.stat("docs/report.bin")

        assert entry.path == "docs/report.bin"
        assert entry.size == 3
        assert entry.is_dir is False
        assert entry.mtime == 123.5

    def test_stat_returns_directory_metadata(self, store):
        store.mkdir("docs")

        entry = store.stat("docs")

        assert entry.path == "docs"
        assert entry.size == 0
        assert entry.is_dir is True

    def test_stat_missing_path_raises_file_not_found(self, store):
        with pytest.raises(FileNotFoundError):
            store.stat("missing.bin")


class TestReadOnlyEnforcement:
    def test_create_whole_blocked(self, ro_store):
        with pytest.raises(PermissionError):
            ro_store.create_whole("f.txt", b"data")

    def test_write_whole_blocked(self, ro_store):
        with pytest.raises(PermissionError):
            ro_store.write_whole("f.txt", b"data")

    def test_delete_blocked(self, ro_store):
        ro_store.seed("f.txt", "data")
        with pytest.raises(PermissionError):
            ro_store.delete("f.txt")

    def test_mkdir_blocked(self, ro_store):
        with pytest.raises(PermissionError):
            ro_store.mkdir("newdir")

    def test_move_blocked(self, ro_store):
        ro_store.seed("src.txt", "x")
        with pytest.raises(PermissionError):
            ro_store.move("src.txt", "dst.txt")


class TestRichDefaults:
    """These methods are inherited from the FileStore Protocol.

    The fact that they work correctly against FakeFileStore confirms the
    Protocol's default implementations are correctly wired to the primitives.
    """

    def test_read_text_decodes_utf8(self, store):
        store.seed("utf.txt", "héllo wörld")
        assert store.read_text("utf.txt") == "héllo wörld"

    def test_write_text_encodes_utf8(self, store):
        store.write_text("utf.txt", "héllo")
        assert store.read_whole("utf.txt") == "héllo".encode("utf-8")

    def test_read_lines_returns_requested_slice(self, store):
        store.seed("lines.txt", "line1\nline2\nline3\nline4")
        assert store.read_lines("lines.txt", start_line=2, end_line=3) == "line2\nline3"

    def test_str_replace_happy_path(self, store):
        store.seed("edit.txt", "hello world")
        store.str_replace("edit.txt", "world", "Python")
        assert store.read_text("edit.txt") == "hello Python"

    def test_str_replace_not_found_raises_value_error(self, store):
        store.seed("edit.txt", "hello world")
        with pytest.raises(ValueError, match="not found"):
            store.str_replace("edit.txt", "missing", "x")

    def test_str_replace_ambiguous_raises_value_error(self, store):
        store.seed("edit.txt", "foo foo foo")
        with pytest.raises(ValueError, match="3 times"):
            store.str_replace("edit.txt", "foo", "bar")

    def test_str_replace_blocked_on_read_only_store(self, ro_store):
        ro_store.seed("edit.txt", "hello world")
        with pytest.raises(PermissionError):
            ro_store.str_replace("edit.txt", "world", "Python")

    def test_search_finds_matching_lines(self, store):
        store.seed("doc.txt", "the quick brown fox\njumps over the lazy dog")
        results = store.search("lazy")
        assert "doc.txt" in results
        assert any("lazy" in line for line in results["doc.txt"])

    def test_search_returns_empty_dict_on_no_match(self, store):
        store.seed("doc.txt", "hello world")
        assert store.search("xyz") == {}

    def test_search_skips_pdf_files(self, store):
        store.seed("doc.pdf", b"fake binary content")
        assert "doc.pdf" not in store.search("fake")

    def test_search_skips_undecodable_content(self, store):
        store.seed("unknown.dat", b"\xff\xfe")

        assert store.search("anything") == {}

    def test_search_propagates_storage_failures(self, store):
        store.seed("doc.txt", "content")

        with (
            patch.object(store, "read_text", side_effect=PermissionError("denied")),
            pytest.raises(PermissionError, match="denied"),
        ):
            store.search("content")

    def test_read_as_markdown_falls_back_to_read_text(self, store):
        store.seed("notes.txt", "plain text")
        assert store.read_as_markdown("notes.txt") == "plain text"