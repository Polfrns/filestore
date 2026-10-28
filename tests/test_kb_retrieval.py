"""Semantic retrieval scope tests with a scripted Chroma collection."""
from __future__ import annotations

from collections.abc import Callable
from unittest.mock import patch

import pytest

from kb.engine import retrieve
from tests.fakes import FakeFileStore


QueryResponse = dict[str, list[list[object]]]


def query_response(*hits: tuple[str, str, float]) -> QueryResponse:
    return {
        "documents": [[text for text, _, _ in hits]],
        "metadatas": [[{"source": source} for _, source, _ in hits]],
        "distances": [[distance for _, _, distance in hits]],
    }


class ScriptedCollection:
    def __init__(
        self,
        respond: Callable[[dict[str, object]], QueryResponse],
        count: int = 100,
    ) -> None:
        self.respond = respond
        self.collection_count = count
        self.queries: list[dict[str, object]] = []

    def count(self) -> int:
        return self.collection_count

    def query(self, **kwargs: object) -> QueryResponse:
        self.queries.append(kwargs)
        return self.respond(kwargs)


def retrieval_patches(collections: list[ScriptedCollection] | None = None):
    collection_side_effect = collections if collections is not None else []
    return (
        patch("kb.engine._get_store_model", return_value=None),
        patch("kb.engine._embed", return_value=[0.25]),
        patch("kb.engine._get_collection", side_effect=collection_side_effect),
    )


class TestSemanticGlobScope:
    def test_no_matching_sources_skips_chroma_query(self):
        store = FakeFileStore("store").seed("docs/notes.txt", "text")
        model_patch, embed_patch, collection_patch = retrieval_patches()
        with model_patch, embed_patch, collection_patch as get_collection:
            result = retrieve("query", [store], glob="**/*.pdf")

        assert result == []
        get_collection.assert_not_called()

    def test_all_sources_query_omits_chroma_where_filter(self):
        store = FakeFileStore("store").seed("a.md", "A").seed("b.md", "B")
        collection = ScriptedCollection(
            lambda _: query_response(("closest", "a.md", 0.1))
        )
        patches = retrieval_patches([collection])
        with patches[0], patches[1], patches[2]:
            result = retrieve("query", [store])

        assert collection.queries[0]["where"] is None
        assert [hit.source for hit in result] == ["a.md"]

    def test_partial_scope_filters_candidates_before_ranking(self):
        store = (
            FakeFileStore("store")
            .seed("docs/in-scope.md", "scope")
            .seed("outside.md", "closer but excluded")
        )

        def respond(query: dict[str, object]) -> QueryResponse:
            where = query["where"]
            if where is None:
                return query_response(("unscoped", "outside.md", 0.01))
            return query_response(("scoped", "docs/in-scope.md", 0.2))

        collection = ScriptedCollection(respond)
        patches = retrieval_patches([collection])
        with patches[0], patches[1], patches[2]:
            result = retrieve("query", [store], glob="docs/**/*.md", k=1)

        assert collection.queries[0]["where"] == {
            "source": {"$in": ["docs/in-scope.md"]}
        }
        assert [hit.source for hit in result] == ["docs/in-scope.md"]

    def test_source_filters_are_batched_and_merged_into_global_top_k(self):
        store = FakeFileStore("store")
        for path in ["docs/a.md", "docs/b.md", "docs/c.md", "outside.txt"]:
            store.seed(path, path)

        distances = {
            "docs/a.md": 0.4,
            "docs/b.md": 0.2,
            "docs/c.md": 0.1,
        }

        def respond(query: dict[str, object]) -> QueryResponse:
            sources = query["where"]["source"]["$in"]  # type: ignore[index]
            return query_response(
                *((source, source, distances[source]) for source in sources)
            )

        collection = ScriptedCollection(respond)
        patches = retrieval_patches([collection])
        with (
            patches[0],
            patches[1],
            patches[2],
            patch("kb.engine.SOURCE_FILTER_BATCH_SIZE", 2),
        ):
            result = retrieve("query", [store], glob="docs/**/*.md", k=2)

        assert [query["where"] for query in collection.queries] == [
            {"source": {"$in": ["docs/a.md", "docs/b.md"]}},
            {"source": {"$in": ["docs/c.md"]}},
        ]
        assert [(hit.source, hit.distance) for hit in result] == [
            ("docs/c.md", 0.1),
            ("docs/b.md", 0.2),
        ]

    def test_same_glob_is_resolved_independently_for_each_store(self):
        first = (
            FakeFileStore("first", "First")
            .seed("docs/first.md", "first")
            .seed("first.txt", "outside")
        )
        second = (
            FakeFileStore("second", "Second")
            .seed("docs/second.md", "second")
            .seed("second.txt", "outside")
        )
        first_collection = ScriptedCollection(
            lambda _: query_response(("first", "docs/first.md", 0.3))
        )
        second_collection = ScriptedCollection(
            lambda _: query_response(("second", "docs/second.md", 0.1))
        )
        patches = retrieval_patches([first_collection, second_collection])
        with patches[0], patches[1], patches[2]:
            result = retrieve("query", [first, second], glob="docs/**/*.md")

        assert first_collection.queries[0]["where"] == {
            "source": {"$in": ["docs/first.md"]}
        }
        assert second_collection.queries[0]["where"] == {
            "source": {"$in": ["docs/second.md"]}
        }
        assert [(hit.filestore_uid, hit.source) for hit in result] == [
            ("second", "docs/second.md"),
            ("first", "docs/first.md"),
        ]

    @pytest.mark.parametrize("glob", ["", "/absolute/*.md", "../secret.md", r"docs\*.md"])
    def test_invalid_glob_fails_before_embedding_or_store_access(self, glob):
        store = FakeFileStore("store").seed("docs/file.md", "text")
        model_patch, embed_patch, collection_patch = retrieval_patches()
        with (
            model_patch as get_model,
            embed_patch as embed,
            collection_patch as get_collection,
            pytest.raises(ValueError),
        ):
            retrieve("query", [store], glob=glob)

        embed.assert_not_called()
        get_model.assert_not_called()
        get_collection.assert_not_called()
