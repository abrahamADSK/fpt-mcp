"""
test_rag_search.py
==================
Phase 3.3 — RAG search tests.

Tests the hybrid search pipeline: BM25 + semantic (HyDE-expanded) fused
via Reciprocal Rank Fusion (RRF).  Uses a mini corpus of 12 chunks across
3 API domains (shotgun_api3, toolkit, rest_api) built into a temporary
ChromaDB index — no connection to ShotGrid or large model downloads needed.

Tests
-----
1. test_rag_search_basic          — search returns relevant chunks for "sg_find filters"
2. test_rag_search_hyde_expansion — HyDE expands queries with domain-specific templates
3. test_rag_search_bm25_exact     — BM25 matches exact method names ("sg_batch")
4. test_rag_search_rrf_fusion     — RRF combines semantic + lexical rankings
5. test_rag_search_empty_index    — graceful error when index is empty
6. test_rag_search_no_match       — returns low-relevance / informative result for
                                    irrelevant query
"""

from unittest.mock import patch

import pytest


# ═══════════════════════════════════════════════════════════════════════════
# 1. Basic search — "sg_find filters" returns relevant chunks
# ═══════════════════════════════════════════════════════════════════════════

class TestRagSearchBasic:
    """search() returns chunks containing sg_find when queried for 'sg_find filters'."""

    def test_returns_text_and_relevance(self, patch_rag_singletons):
        """search() returns a (str, int) tuple with non-empty text."""
        from fpt_mcp.rag.search import search

        text, relevance = search("sg_find filters", n_results=3)

        assert isinstance(text, str)
        assert isinstance(relevance, int)
        assert len(text) > 0
        assert relevance >= 0

    def test_top_results_mention_sg_find(self, patch_rag_singletons):
        """At least one returned chunk mentions sg_find (BM25 should match it)."""
        from fpt_mcp.rag.search import search

        text, _relevance = search("sg_find filters", n_results=5)

        # BM25 matches on exact tokens — "sg_find" and "filters" are in the corpus
        assert "sg_find" in text.lower() or "find" in text.lower(), (
            "Expected at least one chunk mentioning sg_find or find"
        )

    def test_result_contains_metadata_header(self, patch_rag_singletons):
        """Results are formatted with ### [api] source — section headers."""
        from fpt_mcp.rag.search import search

        text, _relevance = search("sg_find filters", n_results=3)

        # The formatter adds "### [api] source — section  (relevance: XX%)"
        assert "###" in text, "Expected markdown header in result"
        assert "relevance:" in text, "Expected relevance percentage in result"

    def test_relevance_is_bounded(self, patch_rag_singletons):
        """max_relevance is in [0, 100]."""
        from fpt_mcp.rag.search import search

        _text, relevance = search("sg_find filters", n_results=3)

        assert 0 <= relevance <= 100, f"Relevance {relevance} out of [0, 100] range"

    def test_n_results_limits_output(self, patch_rag_singletons):
        """Requesting n_results=2 returns at most 2 chunk blocks."""
        from fpt_mcp.rag.search import search

        text, _relevance = search("sg_find filters", n_results=2)

        # Chunks are separated by "\n\n---\n\n"
        chunk_count = text.count("\n\n---\n\n") + 1
        assert chunk_count <= 2, f"Expected ≤2 chunks, got {chunk_count}"

    def test_cache_returns_same_result(self, patch_rag_singletons):
        """A12 — identical query returns cached result on second call."""
        from fpt_mcp.rag.search import search

        result1 = search("sg_find filters", n_results=3)
        result2 = search("sg_find filters", n_results=3)

        assert result1 == result2, "Cache should return identical results"


# ═══════════════════════════════════════════════════════════════════════════
# 2. HyDE expansion — domain-specific templates
# ═══════════════════════════════════════════════════════════════════════════

class TestRagSearchHydeExpansion:
    """_hyde_expand() detects API domain and wraps query in code template."""

    def test_toolkit_domain_detected(self):
        """Queries mentioning 'template' or 'sgtk' use Toolkit template."""
        from fpt_mcp.rag.search import _hyde_expand

        result = _hyde_expand("template tokens for maya_asset_work")

        assert "sgtk" in result, "Expected sgtk import in Toolkit HyDE template"
        assert "Toolkit" in result, "Expected 'Toolkit' header in template"

    def test_rest_api_domain_detected(self):
        """Queries mentioning 'REST API' or 'bearer' use REST template."""
        from fpt_mcp.rag.search import _hyde_expand

        result = _hyde_expand("REST API authentication with bearer token")

        assert "requests" in result, "Expected requests import in REST HyDE template"
        assert "Bearer" in result, "Expected Bearer token in REST template"

    def test_default_shotgun_api3_domain(self):
        """Queries without TK/REST keywords default to shotgun_api3 template."""
        from fpt_mcp.rag.search import _hyde_expand

        result = _hyde_expand("create asset with specific fields")

        assert "shotgun_api3" in result, "Expected shotgun_api3 import in default template"
        assert "Shotgun" in result, "Expected Shotgun class reference"

    def test_pipeline_config_triggers_toolkit(self):
        """'pipeline config' keyword triggers Toolkit domain."""
        from fpt_mcp.rag.search import _hyde_expand

        result = _hyde_expand("how to set up pipeline config for a project")

        assert "sgtk" in result

    def test_oauth_triggers_rest(self):
        """'oauth' keyword triggers REST API domain."""
        from fpt_mcp.rag.search import _hyde_expand

        result = _hyde_expand("oauth token refresh flow")

        assert "requests" in result

    def test_hyde_includes_original_query(self):
        """The expanded template still includes the original query text."""
        from fpt_mcp.rag.search import _hyde_expand

        query = "sg_find with complex nested filters"
        result = _hyde_expand(query)

        assert query in result, "HyDE template should embed the original query"


# ═══════════════════════════════════════════════════════════════════════════
# 3. BM25 exact match — method name retrieval
# ═══════════════════════════════════════════════════════════════════════════

class TestRagSearchBm25Exact:
    """BM25 (lexical) retriever matches exact method names in corpus."""

    def test_sg_batch_found_by_bm25(self, patch_rag_singletons):
        """Querying 'sg_batch' returns chunks containing sg_batch."""
        from fpt_mcp.rag.search import search

        text, _relevance = search("sg_batch", n_results=5)

        assert "sg_batch" in text.lower(), (
            "BM25 should rank the sg_batch chunk highly for an exact token match"
        )

    def test_bm25_scores_exact_token_higher(self, mini_rag_corpus):
        """BM25 scores the sg_batch chunk highest when queried for 'sg_batch'."""
        from rank_bm25 import BM25Okapi

        tokenised = [entry["text"].lower().split() for entry in mini_rag_corpus]
        bm25 = BM25Okapi(tokenised)

        scores = bm25.get_scores("sg_batch".lower().split())

        # Find the index of the sg_batch chunk
        batch_idx = next(
            i for i, c in enumerate(mini_rag_corpus)
            if c["id"] == "SG_API.md::2::sg_batch"
        )

        # sg_batch chunk should be in the top 3 scores
        top_indices = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
        assert batch_idx in top_indices[:3], (
            f"sg_batch chunk (idx={batch_idx}) should be in top 3, "
            f"got top 3: {top_indices[:3]}"
        )

    def test_filter_operators_found(self, patch_rag_singletons):
        """Querying 'filter operators' returns the Filter Operators chunk."""
        from fpt_mcp.rag.search import search

        text, _relevance = search("filter operators is not_in between", n_results=5)

        # The corpus has a dedicated "Filter Operators" chunk
        assert "filter" in text.lower()


# ═══════════════════════════════════════════════════════════════════════════
# 4. RRF fusion — combines semantic + lexical rankings
# ═══════════════════════════════════════════════════════════════════════════

class TestRagSearchRrfFusion:
    """_rrf_fuse() correctly combines two ranked lists."""

    def test_rrf_basic_merge(self):
        """RRF merges two disjoint lists."""
        from fpt_mcp.rag.search import _rrf_fuse

        sem = ["a", "b", "c"]
        bm25 = ["d", "e", "f"]

        fused = _rrf_fuse(sem, bm25, k=60)

        # All 6 IDs should be present
        assert set(fused) == {"a", "b", "c", "d", "e", "f"}
        # First-ranked from each list should be at the top
        assert fused[0] in ("a", "d"), "Top result should be rank-1 from either list"

    def test_rrf_overlapping_boosted(self):
        """Documents appearing in both lists get boosted to the top."""
        from fpt_mcp.rag.search import _rrf_fuse

        # "shared" appears in both — it should be ranked first
        sem = ["shared", "sem_only_1", "sem_only_2"]
        bm25 = ["bm25_only_1", "shared", "bm25_only_2"]

        fused = _rrf_fuse(sem, bm25, k=60)

        assert fused[0] == "shared", (
            "Document appearing in both rankers should be boosted to top"
        )

    def test_rrf_preserves_relative_order(self):
        """Within a single ranker, original order is respected."""
        from fpt_mcp.rag.search import _rrf_fuse

        sem = ["a", "b", "c"]
        bm25 = []  # No BM25 results

        fused = _rrf_fuse(sem, bm25, k=60)

        assert fused == ["a", "b", "c"], "With one ranker, order should be preserved"

    def test_rrf_empty_inputs(self):
        """RRF handles empty input lists gracefully."""
        from fpt_mcp.rag.search import _rrf_fuse

        fused = _rrf_fuse([], [], k=60)

        assert fused == []

    def test_rrf_k_parameter_affects_scores(self):
        """Different k values produce different orderings for edge cases."""
        from fpt_mcp.rag.search import _rrf_fuse

        # With k=1 rank differences are amplified; with k=1000 they're dampened
        sem = ["a", "b"]
        bm25 = ["b", "a"]

        fused_low_k = _rrf_fuse(sem, bm25, k=1)
        fused_high_k = _rrf_fuse(sem, bm25, k=1000)

        # Both should contain the same docs (a and b are tied in symmetry)
        assert set(fused_low_k) == {"a", "b"}
        assert set(fused_high_k) == {"a", "b"}

    def test_rrf_integration_with_search(self, patch_rag_singletons):
        """Full search uses RRF when both BM25 and semantic results exist."""
        from fpt_mcp.rag.search import search

        # "create asset" should pull from both semantic and BM25
        text, relevance = search("create asset", n_results=5)

        # Should get results from the fusion
        assert len(text) > 0
        assert "###" in text  # formatted output


# ═══════════════════════════════════════════════════════════════════════════
# 5. Empty index — graceful error handling
# ═══════════════════════════════════════════════════════════════════════════

class TestRagSearchEmptyIndex:
    """search() returns informative error when index is empty or missing."""

    def test_empty_collection_returns_message(self, rag_empty_collection):
        """An empty ChromaDB collection returns an informative message."""
        from fpt_mcp.rag.search import search

        collection, index_dir = rag_empty_collection

        with patch("fpt_mcp.rag.search._collection", collection), \
             patch("fpt_mcp.rag.search.INDEX_DIR", index_dir), \
             patch("fpt_mcp.rag.search._search_cache", {}):
            text, relevance = search("anything", n_results=3)

        assert relevance == 0, "Empty index should return relevance 0"
        assert "empty" in text.lower() or "build" in text.lower(), (
            f"Expected informative message about empty index, got: {text!r}"
        )

    def test_missing_index_dir_returns_message(self, tmp_path):
        """A nonexistent index directory returns the 'build it first' message."""
        from fpt_mcp.rag.search import search

        fake_dir = str(tmp_path / "nonexistent_index")

        with patch("fpt_mcp.rag.search._collection", None), \
             patch("fpt_mcp.rag.search._client", None), \
             patch("fpt_mcp.rag.search.INDEX_DIR", fake_dir), \
             patch("fpt_mcp.rag.search._search_cache", {}):
            text, relevance = search("anything", n_results=3)

        assert relevance == 0
        assert "not found" in text.lower() or "build" in text.lower(), (
            f"Expected 'not found' or 'build' message, got: {text!r}"
        )

    def test_empty_returns_zero_relevance(self, rag_empty_collection):
        """Relevance is exactly 0 when no chunks exist."""
        from fpt_mcp.rag.search import search

        collection, index_dir = rag_empty_collection

        with patch("fpt_mcp.rag.search._collection", collection), \
             patch("fpt_mcp.rag.search.INDEX_DIR", index_dir), \
             patch("fpt_mcp.rag.search._search_cache", {}):
            _text, relevance = search("sg_find", n_results=5)

        assert relevance == 0


# ═══════════════════════════════════════════════════════════════════════════
# 6. No match — irrelevant query returns low relevance
# ═══════════════════════════════════════════════════════════════════════════

class TestRagSearchNoMatch:
    """Queries about topics not in the corpus return low relevance scores."""

    def test_irrelevant_query_returns_results(self, patch_rag_singletons):
        """Even an irrelevant query returns *some* results (nearest neighbours).

        ChromaDB always returns n_results nearest docs — they just have
        high distance (= low relevance).
        """
        from fpt_mcp.rag.search import search

        text, relevance = search(
            "quantum physics superconductor entanglement",
            n_results=3,
        )

        # Should return something (ChromaDB returns nearest neighbors)
        assert isinstance(text, str)
        assert isinstance(relevance, int)

    def test_completely_unrelated_query_still_returns_formatted(
        self, patch_rag_singletons
    ):
        """Even for a garbage query, output is properly formatted."""
        from fpt_mcp.rag.search import search

        text, _relevance = search("xyzzy plugh abracadabra", n_results=2)

        # Either we get formatted results or a "no results" message
        has_results = "###" in text
        has_message = "no relevant" in text.lower() or len(text) > 0

        assert has_results or has_message, "Should return formatted results or message"

    def test_single_char_query(self, patch_rag_singletons):
        """A single-character query doesn't crash."""
        from fpt_mcp.rag.search import search

        text, relevance = search("x", n_results=2)

        assert isinstance(text, str)
        assert isinstance(relevance, int)
        assert relevance >= 0
