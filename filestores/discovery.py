from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


DEFAULT_DISCOVERY_LIMIT = 10
MAX_DISCOVERY_LIMIT = 100


@dataclass(frozen=True)
class FilestoreSummary:
    uid: str
    display_name: str
    description: str
    read_only: bool
    is_remote: bool
    updated_at: datetime


def validate_discovery_query(query: str, limit: int) -> None:
    if not query.strip():
        raise ValueError("query must not be empty")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_DISCOVERY_LIMIT:
        raise ValueError(f"limit must be an integer from 1 to {MAX_DISCOVERY_LIMIT}")


def search_filestore_summaries(
    summaries: list[FilestoreSummary],
    query: str,
    limit: int = DEFAULT_DISCOVERY_LIMIT,
) -> list[FilestoreSummary]:
    validate_discovery_query(query, limit)
    normalized_query = query.strip().casefold()

    ranked: list[tuple[int, FilestoreSummary]] = []
    for summary in summaries:
        name = summary.display_name.casefold()
        description = summary.description.casefold()
        if name == normalized_query:
            rank = 0
        elif name.startswith(normalized_query):
            rank = 1
        elif normalized_query in name:
            rank = 2
        elif normalized_query in description:
            rank = 3
        else:
            continue
        ranked.append((rank, summary))

    ranked.sort(
        key=lambda item: (
            item[0],
            -item[1].updated_at.timestamp(),
            item[1].uid,
        )
    )
    return [summary for _, summary in ranked[:limit]]


def format_filestore_summaries(
    summaries: list[FilestoreSummary],
    heading: str,
    empty_message: str,
) -> str:
    if not summaries:
        return empty_message

    lines = [heading]
    for summary in summaries:
        permissions = "read-only" if summary.read_only else "read-write"
        kind = "remote" if summary.is_remote else "local"
        lines.append(
            f"- {summary.display_name} (uid: {summary.uid})\n"
            f"  permissions: {permissions}\n"
            f"  type: {kind}"
        )
        if summary.description:
            lines.append(f"  description: {summary.description}")
    return "\n".join(lines)
