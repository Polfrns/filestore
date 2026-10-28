"""Filestore discovery ranking, formatting, and access-boundary tests."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from asgiref.sync import sync_to_async
from django.contrib.auth.models import User
from django.db import connections

from filestores.discovery import (
    DEFAULT_DISCOVERY_LIMIT,
    MAX_DISCOVERY_LIMIT,
    FilestoreSummary,
    format_filestore_summaries,
    search_filestore_summaries,
)
from filestores.models import FileStore
from transport import server


BASE_TIME = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)


def summary(
    uid: str,
    display_name: str,
    description: str = "",
    *,
    updated_at: datetime = BASE_TIME,
    read_only: bool = False,
    is_remote: bool = False,
) -> FilestoreSummary:
    return FilestoreSummary(
        uid=uid,
        display_name=display_name,
        description=description,
        read_only=read_only,
        is_remote=is_remote,
        updated_at=updated_at,
    )


class TestDiscoveryRanking:
    def test_ranks_name_categories_before_recency(self):
        summaries = [
            summary("desc0001", "Regulations", "Current PROJECT documents", updated_at=BASE_TIME + timedelta(days=4)),
            summary("name0001", "Archived Project Files", updated_at=BASE_TIME + timedelta(days=3)),
            summary("pref0001", "Project Archive", updated_at=BASE_TIME + timedelta(days=2)),
            summary("exact001", "PROJECT", updated_at=BASE_TIME),
            summary("other001", "Unrelated", "No match", updated_at=BASE_TIME + timedelta(days=5)),
        ]

        result = search_filestore_summaries(summaries, "  project  ")

        assert [item.uid for item in result] == [
            "exact001",
            "pref0001",
            "name0001",
            "desc0001",
        ]

    def test_duplicate_names_use_newest_then_uid_as_stable_tiebreakers(self):
        summaries = [
            summary("same0002", "Project", updated_at=BASE_TIME),
            summary("older001", "Project", updated_at=BASE_TIME - timedelta(seconds=1)),
            summary("same0001", "Project", updated_at=BASE_TIME),
            summary("newer001", "Project", updated_at=BASE_TIME + timedelta(seconds=1)),
        ]

        result = search_filestore_summaries(summaries, "project")

        assert [item.uid for item in result] == [
            "newer001",
            "same0001",
            "same0002",
            "older001",
        ]

    def test_default_limit_is_bounded(self):
        summaries = [summary(f"store{i:03}", "Project") for i in range(DEFAULT_DISCOVERY_LIMIT + 2)]

        result = search_filestore_summaries(summaries, "project")

        assert len(result) == DEFAULT_DISCOVERY_LIMIT

    def test_explicit_limit_is_applied_after_ranking(self):
        summaries = [
            summary("prefix01", "Project Archive", updated_at=BASE_TIME + timedelta(days=1)),
            summary("exact001", "Project", updated_at=BASE_TIME),
        ]

        result = search_filestore_summaries(summaries, "project", limit=1)

        assert [item.uid for item in result] == ["exact001"]

    @pytest.mark.parametrize("query", ["", " ", "\r\n\t"])
    def test_rejects_empty_query(self, query):
        with pytest.raises(ValueError, match="query must not be empty"):
            search_filestore_summaries([], query)

    @pytest.mark.parametrize("limit", [0, -1, MAX_DISCOVERY_LIMIT + 1, True, 1.5, "10"])
    def test_rejects_invalid_limit(self, limit):
        with pytest.raises(ValueError, match="limit must be an integer"):
            search_filestore_summaries([], "project", limit=limit)

    @pytest.mark.parametrize("limit", [1, MAX_DISCOVERY_LIMIT])
    def test_accepts_limit_boundaries(self, limit):
        assert search_filestore_summaries([], "project", limit=limit) == []


class TestDiscoveryFormatting:
    def test_formats_summary_fields_for_model_readability(self):
        summaries = [
            summary(
                "graph001",
                "Project Files",
                "Current project documents",
                read_only=True,
                is_remote=True,
            )
        ]

        result = format_filestore_summaries(
            summaries,
            heading="Results:",
            empty_message="None.",
        )

        assert result == (
            "Results:\n"
            "- Project Files (uid: graph001)\n"
            "  permissions: read-only\n"
            "  type: remote\n"
            "  description: Current project documents"
        )

    def test_returns_caller_supplied_empty_message(self):
        assert format_filestore_summaries([], "Results:", "None.") == "None."


class TestDiscoveryTools:
    async def test_list_remains_unbounded_and_uses_shared_entry_format(self):
        summaries = [summary(f"store{i:03}", f"Store {i}") for i in range(12)]
        get_summaries = AsyncMock(return_value=summaries)

        with patch(
            "transport.server._get_accessible_filestore_summaries",
            new=get_summaries,
        ):
            result = await server.list_filestores()

        assert "Accessible filestores:" in result
        assert "Store 0 (uid: store000)" in result
        assert "Store 11 (uid: store011)" in result
        get_summaries.assert_awaited_once_with()

    async def test_search_uses_same_entry_format_in_ranked_order(self):
        summaries = [
            summary("prefix01", "Project Archive", updated_at=BASE_TIME + timedelta(days=1)),
            summary("exact001", "Project", updated_at=BASE_TIME, read_only=True),
        ]

        with patch(
            "transport.server._get_accessible_filestore_summaries",
            new=AsyncMock(return_value=summaries),
        ):
            result = await server.search_filestores(" project ")

        assert result.startswith("Filestores matching 'project':")
        assert result.index("Project (uid: exact001)") < result.index(
            "Project Archive (uid: prefix01)"
        )
        assert "permissions: read-only" in result
        assert "type: local" in result

    async def test_search_returns_clear_no_match_message(self):
        with patch(
            "transport.server._get_accessible_filestore_summaries",
            new=AsyncMock(return_value=[]),
        ):
            result = await server.search_filestores("missing")

        assert result == "No accessible filestores match 'missing'."

    @pytest.mark.parametrize(
        ("query", "limit"),
        [
            (" ", DEFAULT_DISCOVERY_LIMIT),
            ("project", 0),
            ("project", MAX_DISCOVERY_LIMIT + 1),
        ],
    )
    async def test_invalid_input_fails_before_discovery_query(self, query, limit):
        get_summaries = AsyncMock()

        with (
            patch(
                "transport.server._get_accessible_filestore_summaries",
                new=get_summaries,
            ),
            pytest.raises(ValueError),
        ):
            await server.search_filestores(query, limit=limit)

        get_summaries.assert_not_awaited()


@pytest.fixture
def access_records(transactional_db):
    alice = User.objects.create_user("discovery-alice")
    bob = User.objects.create_user("discovery-bob")
    accessible = FileStore.objects.create(
        uid="visible1",
        display_name="Visible",
        description="Contains secret project material",
        ref="/tmp/visible",
        shared=True,
        owner=bob,
    )
    inaccessible = FileStore.objects.create(
        uid="hidden01",
        display_name="Secret",
        description="Exact but private",
        ref="/tmp/hidden",
        shared=False,
        owner=bob,
    )
    return alice, accessible, inaccessible


class TestDiscoveryAccessBoundary:
    async def test_access_filtering_precedes_matching_and_ranking(self, access_records):
        alice, accessible, inaccessible = access_records

        try:
            with patch("transport.server._get_user", return_value=alice):
                result = await server.search_filestores("secret")
        finally:
            await sync_to_async(connections.close_all)()

        assert accessible.uid in result
        assert inaccessible.uid not in result
        assert inaccessible.display_name not in result
