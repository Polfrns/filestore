"""Cross-adapter conformance tests for FileStore path and glob semantics."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from unittest.mock import patch

import pytest

from filestores.adapters.local import LocalFileStore
from filestores.domain import FileStore, validate_store_path
from tests.fakes import FakeFileStore


FILES = {
    "SPEC-root.md": "root spec",
    "docs/SPEC-api.md": "nested spec",
    "docs/notes.txt": "shared needle",
    "notes.txt": "shared needle",
    "regulations/root.pdf": "root pdf",
    "regulations/a/deep.pdf": "deep pdf",
    "regulations/2026/report.md": "report",
    "regulations/2026/nested/deep.md": "deep report",
}
DIRECTORIES = {
    "docs",
    "regulations",
    "regulations/a",
    "regulations/2026",
    "regulations/2026/nested",
}


@pytest.fixture(params=["fake", "local"])
def glob_store(request, tmp_path) -> FileStore:
    if request.param == "fake":
        store = FakeFileStore("globtest")
        for directory in DIRECTORIES:
            store.mkdir(directory)
        for path, content in FILES.items():
            store.seed(path, content)
        return store

    root = Path(tmp_path) / "store"
    root.mkdir()
    store = LocalFileStore("globtest", root)
    for directory in DIRECTORIES:
        store.mkdir(directory)
    for path, content in FILES.items():
        store.write_text(path, content)
    return store


def listed_paths(store: FileStore, glob: str | None = None) -> list[str]:
    return [entry.path for entry in store.list(glob)]


class TestGlobSelection:
    def test_omitted_glob_lists_every_entry_recursively(self, glob_store):
        assert set(listed_paths(glob_store)) == set(FILES) | DIRECTORIES

    @pytest.mark.parametrize(
        ("glob", "expected"),
        [
            ("docs/SPEC-api.md", ["docs/SPEC-api.md"]),
            ("docs", ["docs"]),
            ("**/SPEC*.md", ["SPEC-root.md", "docs/SPEC-api.md"]),
            ("regulations/**/*.pdf", ["regulations/a/deep.pdf", "regulations/root.pdf"]),
            ("regulations/2026/*.md", ["regulations/2026/report.md"]),
            ("docs/notes.?xt", ["docs/notes.txt"]),
            ("docs/[Sn]*.md", ["docs/SPEC-api.md"]),
            ("**/*.csv", []),
        ],
    )
    def test_matches_complete_posix_paths(self, glob_store, glob, expected):
        assert listed_paths(glob_store, glob) == expected

    @pytest.mark.parametrize(
        "glob",
        [
            "",
            "/absolute/*.txt",
            "C:/absolute/*.txt",
            "../secret.txt",
            "docs/../../secret.txt",
            r"docs\*.txt",
            "bad\x00name.txt",
        ],
    )
    def test_rejects_invalid_patterns(self, glob_store, glob):
        with pytest.raises(ValueError):
            glob_store.list(glob)


class TestGlobScopedLiteralSearch:
    def test_search_reads_only_files_matching_glob(self, glob_store):
        results = glob_store.search("needle", glob="docs/**/*.txt")

        assert set(results) == {"docs/notes.txt"}

    def test_search_returns_empty_when_glob_matches_no_files(self, glob_store):
        assert glob_store.search("needle", glob="**/*.csv") == {}


class TestStatConformance:
    def test_returns_exact_file_metadata(self, glob_store):
        entry = glob_store.stat("docs/notes.txt")

        assert entry.path == "docs/notes.txt"
        assert entry.size == len(FILES["docs/notes.txt"].encode())
        assert entry.is_dir is False
        assert entry.mtime > 0

    def test_returns_directory_metadata(self, glob_store):
        entry = glob_store.stat("docs")

        assert entry.path == "docs"
        assert entry.size == 0
        assert entry.is_dir is True

    def test_missing_path_raises_file_not_found(self, glob_store):
        with pytest.raises(FileNotFoundError):
            glob_store.stat("missing.bin")

    def test_local_stat_does_not_read_file_contents(self, tmp_path):
        root = Path(tmp_path) / "store"
        root.mkdir()
        (root / "large.bin").write_bytes(b"content")
        store = LocalFileStore("local", root)

        with patch.object(Path, "read_bytes", side_effect=AssertionError("content read")):
            entry = store.stat("large.bin")

        assert entry.size == len(b"content")


class TestExactPathValidation:
    @pytest.mark.parametrize(
        "path",
        [
            "",
            "/absolute.txt",
            "C:/absolute.txt",
            "C:relative.txt",
            "../escape.txt",
            r"docs\file.txt",
            "bad\x00name.txt",
        ],
    )
    def test_rejects_non_relative_posix_paths(self, path):
        with pytest.raises(ValueError):
            validate_store_path(path)

    def test_colons_outside_a_drive_prefix_remain_valid(self):
        validate_store_path("reports/revision:final.txt")


class TestLocalFailureSemantics:
    def test_list_propagates_metadata_failures(self, tmp_path):
        root = Path(tmp_path) / "store"
        root.mkdir()
        broken = root / "broken.txt"
        broken.write_text("content")
        store = LocalFileStore("local", root)
        original_stat = Path.stat

        def fail_broken_path(path, *args, **kwargs):
            if path == broken:
                raise PermissionError("denied")
            return original_stat(path, *args, **kwargs)

        with (
            patch.object(Path, "stat", fail_broken_path),
            pytest.raises(PermissionError, match="denied"),
        ):
            store.list()

    def test_list_ignores_entry_deleted_during_scan(self, tmp_path):
        root = Path(tmp_path) / "store"
        root.mkdir()
        vanishing = root / "vanishing.txt"
        vanishing.write_text("content")
        store = LocalFileStore("local", root)
        original_stat = Path.stat

        def lose_vanishing_path(path, *args, **kwargs):
            if path == vanishing:
                raise FileNotFoundError(path)
            return original_stat(path, *args, **kwargs)

        with patch.object(Path, "stat", lose_vanishing_path):
            assert store.list() == []

    def test_create_whole_is_exclusive_under_concurrency(self, tmp_path):
        root = Path(tmp_path) / "store"
        root.mkdir()
        store = LocalFileStore("local", root)
        barrier = Barrier(2)

        def create(content: bytes) -> str:
            barrier.wait()
            try:
                store.create_whole("result.bin", content)
            except FileExistsError:
                return "conflict"
            return "created"

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(create, [b"first", b"second"]))

        assert sorted(outcomes) == ["conflict", "created"]
        assert store.read_whole("result.bin") in {b"first", b"second"}
