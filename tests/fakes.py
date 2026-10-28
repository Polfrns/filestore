"""Fake adapters — second concrete implementation of the FileStore port.

Having two implementations makes the contract concrete: if something only
works against LocalFileStore, the port is leaking an implementation detail.
FakeFileStore inherits from the Protocol so it gets all the rich default
methods (str_replace, search, read_text, …) for free; only the eight
primitives are re-implemented.
"""
from __future__ import annotations

import time

from filestores.domain import (
    FileEntry,
    FileStore,
    path_matches_glob,
    validate_glob,
    validate_store_path,
)


class FakeFileStore(FileStore):
    """In-memory FileStore backed by a plain dict."""

    is_remote = False

    def __init__(self, uid: str, display_name: str = "", read_only: bool = False) -> None:
        self._uid = uid
        self._display_name = display_name or uid
        self._read_only = read_only
        # path -> (content_bytes, mtime)
        self._files: dict[str, tuple[bytes, float]] = {}
        self._dirs: set[str] = set()

    @property
    def uid(self) -> str:
        return self._uid

    @property
    def display_name(self) -> str:
        return self._display_name

    @property
    def read_only(self) -> bool:
        return self._read_only

    # ---- Test helpers ----

    def seed(self, path: str, content: str | bytes, mtime: float | None = None) -> "FakeFileStore":
        """Plant a file directly, bypassing write-permission checks. Chainable."""
        raw = content.encode() if isinstance(content, str) else content
        self._files[path] = (raw, mtime if mtime is not None else time.time())
        return self

    # ---- Primitives ----

    def _check_writable(self) -> None:
        if self._read_only:
            raise PermissionError(f"FileStore '{self._uid}' is read-only.")

    def list(self, glob: str | None = None) -> list[FileEntry]:
        validate_glob(glob)
        entries: list[FileEntry] = []
        for p, (content, mtime) in self._files.items():
            if not path_matches_glob(p, glob):
                continue
            entries.append(FileEntry(path=p, size=len(content), is_dir=False, mtime=mtime))
        for d in self._dirs:
            if not path_matches_glob(d, glob):
                continue
            entries.append(FileEntry(path=d, size=0, is_dir=True, mtime=0.0))
        return sorted(entries, key=lambda e: e.path)

    def stat(self, path: str) -> FileEntry:
        validate_store_path(path)
        if path in self._files:
            content, mtime = self._files[path]
            return FileEntry(path=path, size=len(content), is_dir=False, mtime=mtime)
        if path in self._dirs:
            return FileEntry(path=path, size=0, is_dir=True, mtime=0.0)
        raise FileNotFoundError(f"'{path}' not found in store '{self._uid}'.")

    def read_whole(self, path: str) -> bytes:
        validate_store_path(path)
        if path not in self._files:
            raise FileNotFoundError(f"'{path}' not found in store '{self._uid}'.")
        return self._files[path][0]

    def create_whole(self, path: str, content: bytes) -> None:
        self._check_writable()
        validate_store_path(path)
        if path in self._files or path in self._dirs:
            raise FileExistsError(f"'{path}' already exists in store '{self._uid}'.")
        self._files[path] = (content, time.time())

    def write_whole(self, path: str, content: bytes) -> None:
        self._check_writable()
        validate_store_path(path)
        self._files[path] = (content, time.time())

    def delete(self, path: str) -> None:
        self._check_writable()
        validate_store_path(path)
        if path not in self._files and path not in self._dirs:
            raise FileNotFoundError(f"'{path}' not found in store '{self._uid}'.")
        self._files.pop(path, None)
        self._dirs.discard(path)

    def mkdir(self, path: str) -> None:
        self._check_writable()
        validate_store_path(path)
        self._dirs.add(path)

    def move(self, src: str, dst: str) -> None:
        self._check_writable()
        validate_store_path(src)
        validate_store_path(dst)
        if src not in self._files:
            raise FileNotFoundError(f"'{src}' not found in store '{self._uid}'.")
        content, mtime = self._files.pop(src)
        self._files[dst] = (content, mtime)


class FakeEmbeddingPort:
    """Deterministic fixed-dimension embedding — same text always produces the same vector."""

    DIM = 8

    def embed(self, text: str) -> list[float]:
        base = sum(ord(c) for c in text) % 1000
        return [(base + i) % 1000 / 1000 for i in range(self.DIM)]