"""rag_tools.py — bodies of search_sg_docs and learn_pattern.

Extracted from server.py in Bucket F Phase 2e. session_stats stays in
server.py (it's a wafer-thin report over module-level state; extracting
it would save ~30 lines and cost an extra layer of lazy imports).

Impls lazy-import mutable state (`_stats`, `_last_rag_score`,
`_rag_called_this_session`, `_FULL_DOC_TOKENS`) and helpers
(`_model_can_write`, `_get_current_model`, `_tok`, `_SERVER_DIR`) via
`fpt_mcp.server` so existing test patches keep intercepting.

Note on _stats updates:
  test_telemetry exempts RAG tools from the tokens_in / tokens_out
  coverage check, and the wrapper in server.py still bumps `exec_calls`
  + tokens_in/out for consistency. The RAG-specific counters
  (rag_calls, tokens_saved, patterns_learned, patterns_staged) stay
  inside the impl because the wrapper cannot observe cache hits vs
  misses or know which branch (direct-write vs staged) ran.
"""

from __future__ import annotations

import datetime
import json

from fpt_mcp.models import LearnPatternInput, SearchSgDocsInput


async def search_sg_docs_impl(params: SearchSgDocsInput) -> str:
    """Body of search_sg_docs_tool. See server.py for user-facing docstring."""
    import fpt_mcp.server as srv

    try:
        from fpt_mcp.rag.search import search
        text, relevance = search(params.query, n_results=params.n_results)
    except ImportError:
        return json.dumps({
            "error": "RAG dependencies not installed. Run: pip install chromadb sentence-transformers rank-bm25",
            "fallback": "Proceed with caution — no documentation verification available.",
        })
    except Exception as e:
        return json.dumps({"error": f"RAG search failed: {e}"})

    srv._stats["rag_calls"] += 1
    srv._stats["tokens_saved"] += srv._FULL_DOC_TOKENS - srv._tok(text)
    srv._last_rag_score = relevance
    srv._rag_called_this_session = True

    result = {
        "documentation": text,
        "max_relevance": relevance,
        "chunks_returned": params.n_results,
    }

    if relevance < 60:
        result["warning"] = (
            f"Low relevance ({relevance}%) — this query may cover an undocumented area. "
            "Proceed carefully. If your approach works, call learn_pattern to save it."
        )

    return json.dumps(result, default=str)


async def learn_pattern_impl(params: LearnPatternInput) -> str:
    """Body of learn_pattern_tool. See server.py for user-facing docstring."""
    import fpt_mcp.server as srv

    if srv._model_can_write():
        # Direct write to docs
        api_file_map = {
            "shotgun_api3": "SG_API.md",
            "toolkit": "TK_API.md",
            "rest_api": "REST_API.md",
        }
        doc_file = api_file_map.get(params.api, "SG_API.md")
        doc_path = srv._SERVER_DIR / "docs" / doc_file

        try:
            entry = (
                f"\n\n## Learned: {params.description}\n\n"
                f"```python\n{params.code}\n```\n"
            )
            with open(doc_path, "a", encoding="utf-8") as f:
                f.write(entry)
            srv._stats["patterns_learned"] += 1

            # Clear RAG cache so new pattern is found on next search
            try:
                from fpt_mcp.rag.search import clear_cache
                clear_cache()
            except ImportError:
                pass

            return json.dumps({
                "status": "learned",
                "description": params.description,
                "file": doc_file,
                "note": "Pattern appended to docs. Run build_index to include in RAG.",
            })
        except Exception as e:
            return json.dumps({"error": f"Failed to write pattern: {e}"})
    else:
        # Stage candidate for review
        candidates_path = srv._SERVER_DIR / "rag" / "candidates.json"
        try:
            candidates = json.loads(candidates_path.read_text()) if candidates_path.exists() else []
        except Exception:
            candidates = []

        candidates.append({
            "description": params.description,
            "code": params.code,
            "api": params.api,
            "model": srv._get_current_model(),
            "timestamp": datetime.datetime.now().isoformat(),
        })

        try:
            candidates_path.parent.mkdir(parents=True, exist_ok=True)
            candidates_path.write_text(json.dumps(candidates, indent=2, ensure_ascii=False))
        except Exception:
            pass

        srv._stats["patterns_staged"] += 1

        return json.dumps({
            "status": "staged",
            "description": params.description,
            "note": f"Model '{srv._get_current_model()}' is read-only. Pattern staged for review.",
        })
