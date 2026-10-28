from __future__ import annotations

import shutil
from pathlib import Path

from ..domain import (
    FileEntry,
    FileStore,
    path_matches_glob,
    validate_glob,
    validate_store_path,
)


class LocalFileStore(FileStore):
    """A rooted local directory backing a named FileStore.

    All paths are relative to root_path; traversal above root is rejected on
    every operation. 'Local' means local to the server — client-side paths are
    out of scope (use a LAN share or OneDrive instead).
    """

    is_remote = False

    def __init__(
        self,
        uid: str,
        root_path: str | Path,
        display_name: str = "",
        read_only: bool = False,
    ) -> None:
        self._uid = uid
        self._display_name = display_name or uid
        self._root_path = Path(root_path)
        self._read_only = read_only

    @property
    def uid(self) -> str:
        return self._uid

    @property
    def display_name(self) -> str:
        return self._display_name

    @property
    def read_only(self) -> bool:
        return self._read_only

    def _safe_path(self, path: str) -> Path:
        """Resolve path, rejecting any traversal above root_path."""
        validate_store_path(path)
        target = (self._root_path / path).resolve()
        if not target.is_relative_to(self._root_path.resolve()):
            raise ValueError("Path traversal is not allowed")
        return target

    def _check_writable(self) -> None:
        if self._read_only:
            raise PermissionError(f"FileStore '{self._uid}' is read-only.")

    # ---- Primitives ----

    def list(self, glob: str | None = None) -> list[FileEntry]:
        validate_glob(glob)
        if not self._root_path.exists():
            return []
        root_resolved = self._root_path.resolve()
        results = []
        for p in self._root_path.rglob("*"):
            try:
                if not p.resolve().is_relative_to(root_resolved):
                    continue
                rel_path = p.relative_to(self._root_path).as_posix()
                if not rel_path or rel_path == "." or not path_matches_glob(rel_path, glob):
                    continue
                stat_res = p.stat()
                results.append(FileEntry(
                    path=rel_path,
                    size=stat_res.st_size if p.is_file() else 0,
                    is_dir=p.is_dir(),
                    mtime=stat_res.st_mtime,
                ))
            except FileNotFoundError:
                pass
        return sorted(results, key=lambda e: e.path)

    def stat(self, path: str) -> FileEntry:
        target = self._safe_path(path)
        if not target.exists():
            raise FileNotFoundError(f"'{path}' does not exist in store '{self._uid}'.")
        stat_result = target.stat()
        return FileEntry(
            path=path,
            size=stat_result.st_size if target.is_file() else 0,
            is_dir=target.is_dir(),
            mtime=stat_result.st_mtime,
        )

    def read_whole(self, path: str) -> bytes:
        target = self._safe_path(path)
        if not target.exists():
            raise FileNotFoundError(f"'{path}' does not exist in store '{self._uid}'.")
        if not target.is_file():
            raise IsADirectoryError(f"'{path}' is a directory.")
        return target.read_bytes()

    def create_whole(self, path: str, content: bytes) -> None:
        self._check_writable()
        target = self._safe_path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as file:
            file.write(content)

    def write_whole(self, path: str, content: bytes) -> None:
        self._check_writable()
        target = self._safe_path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)

    def delete(self, path: str) -> None:
        self._check_writable()
        target = self._safe_path(path)
        if not target.exists():
            raise FileNotFoundError(f"'{path}' does not exist in store '{self._uid}'.")
        if target.is_file():
            target.unlink()
        elif target.is_dir():
            shutil.rmtree(target)

    def mkdir(self, path: str) -> None:
        self._check_writable()
        self._safe_path(path).mkdir(parents=True, exist_ok=True)

    def move(self, src: str, dst: str) -> None:
        self._check_writable()
        src_path = self._safe_path(src)
        dst_path = self._safe_path(dst)
        if not src_path.exists():
            raise FileNotFoundError(f"'{src}' does not exist in store '{self._uid}'.")
        dst_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src_path), str(dst_path))

    # ---- Rich overrides ----

    def read_text(self, path: str) -> str:
        target = self._safe_path(path)
        if not target.exists():
            raise FileNotFoundError(f"'{path}' does not exist in store '{self._uid}'.")
        return target.read_text(encoding="utf-8")

    def write_text(self, path: str, content: str) -> None:
        self._check_writable()
        target = self._safe_path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    def str_replace(self, path: str, old_str: str, new_str: str) -> None:
        """Efficient in-place str_replace — reads and writes directly to disk."""
        self._check_writable()
        target = self._safe_path(path)
        content = target.read_text(encoding="utf-8")
        count = content.count(old_str)
        if count == 0:
            raise ValueError(f"str_replace: string not found in '{path}'")
        if count > 1:
            raise ValueError(f"str_replace: string appears {count} times in '{path}' — must be unique")
        target.write_text(content.replace(old_str, new_str, 1), encoding="utf-8")

    def read_as_markdown(self, path: str) -> str:
        """Read file as Markdown, converting binary formats via MarkItDown."""
        target = self._safe_path(path)
        if target.suffix.lower() in {".docx", ".xlsx", ".xls", ".pdf", ".pptx", ".html"}:
            try:
                from markitdown import MarkItDown
                result = MarkItDown().convert(str(target))
                return result.text_content
            except ImportError:
                pass
        return target.read_text(encoding="utf-8")