from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import numpy as np

from filestores.domain import FileEntry


class FileReadError(Exception):
    """A file could not be read or converted for KB ingestion."""


class EmbeddingPort(Protocol):
    """Provider-neutral embedding interface. Swap the adapter without touching the engine."""

    def embed(self, text: str) -> np.ndarray: ...


@dataclass
class ChunkLocation:
    """Position of a chunk within its source document."""

    char_start: int
    char_end: int
    page: int | None = None       # populated for paginated formats (pdf, pptx)
    section: str | None = None    # best-effort section heading, e.g. "4.3.2"

    def to_dict(self) -> dict:
        d: dict = {"char_start": self.char_start, "char_end": self.char_end}
        if self.page is not None:
            d["page"] = self.page
        if self.section is not None:
            d["section"] = self.section
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "ChunkLocation":
        return cls(
            char_start=d.get("char_start", 0),
            char_end=d.get("char_end", 0),
            page=d.get("page"),
            section=d.get("section"),
        )


@dataclass
class RetrievedChunk:
    text: str
    filestore_uid: str
    filestore_display: str
    source: str         # file path within the store
    distance: float
    location: ChunkLocation


@dataclass
class IngestResult:
    source: str
    chunks: int


@dataclass(frozen=True)
class SourceManifest:
    """Current searchable files from one filestore freshness scan."""

    files: tuple[FileEntry, ...]

    @property
    def paths(self) -> frozenset[str]:
        return frozenset(entry.path for entry in self.files)
