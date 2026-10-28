from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatchcase
from functools import lru_cache
from pathlib import Path
from pathlib import PurePosixPath
from pathlib import PureWindowsPath
from typing import Protocol


@dataclass(frozen=True)
class FileEntry:
    path: str       # relative to store root, POSIX separators
    size: int
    is_dir: bool
    mtime: float


def _validate_store_relative_posix_path(path: str, label: str) -> None:
    if (
        not path
        or path.startswith("/")
        or "\\" in path
        or "\x00" in path
        or PureWindowsPath(path).drive
    ):
        raise ValueError(f"{label} must be a non-empty store-relative POSIX path")
    if ".." in PurePosixPath(path).parts:
        raise ValueError(f"{label} path traversal '..' is not allowed")


def validate_glob(glob: str | None) -> None:
    if glob is None:
        return
    _validate_store_relative_posix_path(glob, "Glob")


def validate_store_path(path: str) -> None:
    _validate_store_relative_posix_path(path, "Path")


def path_matches_glob(path: str, glob: str | None) -> bool:
    """Match a store-relative POSIX path using segment-aware glob semantics."""
    validate_glob(glob)
    if glob is None:
        return True

    path_segments = tuple(PurePosixPath(path).parts)
    glob_segments = tuple(PurePosixPath(glob).parts)

    @lru_cache(maxsize=None)
    def matches(glob_index: int, path_index: int) -> bool:
        if glob_index == len(glob_segments):
            return path_index == len(path_segments)
        segment = glob_segments[glob_index]
        if segment == "**":
            return matches(glob_index + 1, path_index) or (
                path_index < len(path_segments) and matches(glob_index, path_index + 1)
            )
        return (
            path_index < len(path_segments)
            and fnmatchcase(path_segments[path_index], segment)
            and matches(glob_index + 1, path_index + 1)
        )

    return matches(0, 0)


class FileStore(Protocol):
    """FileStore port — the seam between the tool layer and a storage backend.

    Adapters implement the eight primitives. Rich operations are default methods
    built on those primitives; adapters may override them for efficiency or
    native capability (e.g. str_replace on LocalFileStore, server-side search
    on GraphFileStore).
    """

    @property
    def uid(self) -> str: ...

    @property
    def display_name(self) -> str: ...

    @property
    def read_only(self) -> bool: ...

    @property
    def is_remote(self) -> bool: ...

    # ---- Primitives (every adapter must implement) ----

    def list(self, glob: str | None = None) -> list[FileEntry]:
        """List entries whose store-relative paths match glob. Defaults to all."""
        ...

    def stat(self, path: str) -> FileEntry:
        """Return metadata for one exact store-relative path."""
        ...

    def read_whole(self, path: str) -> bytes: ...

    def create_whole(self, path: str, content: bytes) -> None:
        """Create one file exclusively, raising FileExistsError on conflict."""
        ...

    def write_whole(self, path: str, content: bytes) -> None: ...

    def delete(self, path: str) -> None: ...

    def mkdir(self, path: str) -> None: ...

    def move(self, src: str, dst: str) -> None: ...

    # ---- Rich defaults (built on primitives; adapters may override) ----

    def read_text(self, path: str) -> str:
        return self.read_whole(path).decode("utf-8")

    def write_text(self, path: str, content: str) -> None:
        self.write_whole(path, content.encode("utf-8"))

    def read_lines(self, path: str, start_line: int | None = None, end_line: int | None = None) -> str:
        lines = self.read_text(path).splitlines()
        s = (start_line - 1) if start_line is not None else 0
        e = end_line if end_line is not None else len(lines)
        return "\n".join(lines[s:e])

    def str_replace(self, path: str, old_str: str, new_str: str) -> None:
        """Replace old_str with new_str. Fails if absent or ambiguous (non-unique)."""
        content = self.read_text(path)
        count = content.count(old_str)
        if count == 0:
            raise ValueError(f"str_replace: string not found in '{path}'")
        if count > 1:
            raise ValueError(f"str_replace: string appears {count} times in '{path}' — must be unique")
        self.write_text(path, content.replace(old_str, new_str, 1))

    def read_as_markdown(self, path: str) -> str:
        """Read file as Markdown. Binary formats are converted; plain text passes through."""
        return self.read_text(path)

    def search(self, query: str, glob: str | None = None) -> dict[str, list[str]]:
        """Literal substring search over text files. Returns {relative_path: [matching lines]}."""
        _SKIP = {".pdf", ".docx", ".xlsx", ".xls", ".jpg", ".jpeg", ".png", ".zip", ".bin"}
        results: dict[str, list[str]] = {}
        for entry in self.list(glob):
            if entry.is_dir or Path(entry.path).suffix.lower() in _SKIP:
                continue
            try:
                lines = self.read_text(entry.path).splitlines()
                hits = [f"line {i + 1}: {line}" for i, line in enumerate(lines) if query in line]
                if hits:
                    results[entry.path] = hits
            except UnicodeDecodeError:
                pass
        return results


@dataclass(frozen=True)
class StoreContext:
    """A set of FileStore adapters accessible within a single request."""

    stores: tuple[FileStore, ...]

    def resolve(self, uid: str | None = None) -> FileStore:
        """Resolve by uid. If uid is None and exactly one store is loaded, returns it."""
        if uid is None:
            if len(self.stores) == 1:
                return self.stores[0]
            available = ", ".join(f"{s.display_name} ({s.uid})" for s in self.stores)
            raise ValueError(
                f"filestore_uid is required when multiple stores are accessible. "
                f"Available: {available or 'none'}"
            )
        for s in self.stores:
            if s.uid == uid:
                return s
        available = ", ".join(f"{s.display_name} ({s.uid})" for s in self.stores)
        raise ValueError(f"Filestore '{uid}' is not accessible. Available: {available or 'none'}")