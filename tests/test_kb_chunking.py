"""Chunking math tests — pure unit tests, no DB or external I/O.

_chunk() is the only truly pure function in kb.engine, so it gets the most
precise testing. The key invariants are: no text is skipped, chunks overlap
by exactly CHUNK_OVERLAP chars, and edge cases (empty, tiny, exact-boundary)
are handled cleanly.
"""
import pytest

from kb.engine import CHUNK_OVERLAP, CHUNK_SIZE, _chunk

STEP = CHUNK_SIZE - CHUNK_OVERLAP


class TestChunkEdgeCases:
    def test_empty_string_returns_empty_list(self):
        assert _chunk("") == []

    def test_whitespace_only_returns_empty_list(self):
        assert _chunk("   \n\t  ") == []

    def test_single_word_produces_one_chunk(self):
        result = _chunk("hello")
        assert len(result) == 1
        text, start, end = result[0]
        assert text == "hello"
        assert start == 0
        assert end == 5

    def test_text_shorter_than_step_is_one_chunk(self):
        # A new chunk starts every STEP chars, so anything ≤ STEP fits in one chunk.
        text = "x" * (STEP - 1)
        result = _chunk(text)
        assert len(result) == 1
        _, start, end = result[0]
        assert start == 0
        assert end == len(text)

    def test_text_at_exact_step_size_is_one_chunk(self):
        text = "a" * STEP
        result = _chunk(text)
        assert len(result) == 1
        _, start, end = result[0]
        assert end == STEP


class TestChunkOverlap:
    def test_two_chunks_overlap_by_chunk_overlap_amount(self):
        # Need len > CHUNK_SIZE so the first chunk ends at CHUNK_SIZE (not at len(text)).
        # Need len <= CHUNK_SIZE + STEP so there are exactly two chunks.
        text = "a" * (CHUNK_SIZE + 1)
        result = _chunk(text)
        assert len(result) == 2
        _, s1, e1 = result[0]
        _, s2, e2 = result[1]
        assert e1 - s2 == CHUNK_OVERLAP

    def test_last_chunk_ends_at_text_length(self):
        text = "z" * (CHUNK_SIZE + 50)
        result = _chunk(text)
        assert result[-1][2] == len(text)

    def test_no_text_is_skipped(self):
        text = "".join(str(i % 10) for i in range(CHUNK_SIZE * 3))
        result = _chunk(text)
        covered: set[int] = set()
        for chunk_text, start, end in result:
            assert text[start:end] == chunk_text, "chunk text doesn't match source slice"
            covered.update(range(start, end))
        assert covered == set(range(len(text)))

    def test_chunks_are_non_empty_strings(self):
        text = "word " * 400
        for chunk_text, _, _ in _chunk(text):
            assert isinstance(chunk_text, str)
            assert len(chunk_text) > 0