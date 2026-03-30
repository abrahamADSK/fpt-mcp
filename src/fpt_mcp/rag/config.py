"""
rag/config.py
=============
Shared constants for the RAG pipeline.

IMPORTANT: EMBEDDING_MODEL must be consistent across build_index.py (write)
and search.py (read). If you change it here, delete rag/index/ and rebuild.
"""

# ── Embedding model selection ─────────────────────────────────────────────────
# IMPORTANT: build and query MUST use the same model. If you change this,
# delete rag/index/ and run python -m fpt_mcp.rag.build_index to rebuild.
#
# Option A — bge-small-en-v1.5 (~130 MB): fast, good for semantic queries
# Option B — bge-large-en-v1.5 (~570 MB): higher accuracy on technical code queries
# Option C — nomic-embed-text-v1.5 (~270 MB): strong code + natural language mix
#
# bge-large for better recall on exact ShotGrid API method names.
EMBEDDING_MODEL = "BAAI/bge-large-en-v1.5"

# ── Hybrid search parameters ─────────────────────────────────────────────────
# BM25_CANDIDATES: how many candidates each retriever fetches before RRF fusion
# RRF_K: RRF damping constant (higher = less aggressive rank compression; 60 is standard)
BM25_CANDIDATES = 20
RRF_K = 60

# ── Collections ───────────────────────────────────────────────────────────────
# Three separate collections for the three ShotGrid APIs.
# Metadata includes 'api' field to identify the source.
COLLECTION_NAME = "sg_docs"

# ── Chunking ──────────────────────────────────────────────────────────────────
METHOD_GROUP_SIZE = 4       # methods per sub-chunk in API docs
METHOD_GROUP_THRESHOLD = 8  # min methods to trigger sub-chunking
CHUNK_SPLIT_THRESHOLD = 700 # min section chars to trigger sub-chunking
MIN_CHUNK_CHARS = 80        # skip chunks shorter than this

# ── Token tracking ────────────────────────────────────────────────────────────
# Combined size of all indexed docs in tokens (baseline for RAG savings display).
# SG_API.md ~5000 + TK_API.md ~4500 + REST_API.md ~2000 + anti-patterns ~1500 = ~13000
FULL_DOC_TOKENS = 13000
