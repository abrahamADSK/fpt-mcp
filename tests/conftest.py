"""
conftest.py
===========
Shared pytest fixtures for fpt-mcp test suite.

Adds src/ to sys.path and provides reusable mock infrastructure for:
  - Phase 3.1: Mock ShotGrid API (sg_find, sg_create, sg_update, sg_batch, sg_delete)
  - Phase 3.2: Toolkit path resolution (TkConfig, templates.yml)
  - Phase 3.3: RAG search (ChromaDB + BM25 + HyDE + RRF)
  - Phase 3.5: tk_publish workflow (Mock SG + TkConfig + tmp_path)
"""

import hashlib
import json
import sys
import types as _types
import pathlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── ulimit check ──────────────────────────────────────────────────────────────
import resource
_soft, _hard = resource.getrlimit(resource.RLIMIT_NOFILE)
if _soft < 4096:
    import warnings
    warnings.warn(
        f"Low file descriptor limit ({_soft}). ChromaDB may crash. "
        f"Run: ulimit -n 4096",
        stacklevel=1,
    )
import yaml

# fpt-mcp/src  →  lets `import fpt_mcp` resolve correctly from tests/
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "src"))


# ---------------------------------------------------------------------------
# MCP SDK stub — installed before any test module is collected
# ---------------------------------------------------------------------------
# fpt_mcp.server imports `from mcp.server.fastmcp import FastMCP` at module
# level. We install a minimal stub here so `from fpt_mcp.server import ...`
# works in tests without the full MCP SDK being installed in the sandbox.

# Stub for shotgun_api3 and python-dotenv — not installed in sandbox CI.
# The real packages are installed in the project venv on Mac for live runs.
try:
    import shotgun_api3  # noqa: F401
except ImportError:
    _sg_mod = _types.ModuleType("shotgun_api3")

    class _StubShotgun:
        def __init__(self, *a, **kw):
            pass

    _sg_mod.Shotgun = _StubShotgun
    sys.modules["shotgun_api3"] = _sg_mod

try:
    import dotenv  # noqa: F401
except ImportError:
    _dotenv_mod = _types.ModuleType("dotenv")
    _dotenv_mod.load_dotenv = lambda *a, **kw: None
    sys.modules["dotenv"] = _dotenv_mod


try:
    import mcp  # noqa: F401
except ImportError:
    _mcp_pkg = _types.ModuleType("mcp")
    _mcp_server_mod = _types.ModuleType("mcp.server")
    _mcp_fastmcp = _types.ModuleType("mcp.server.fastmcp")

    class _StubFastMCP:
        """Minimal FastMCP stand-in: captures @mcp.tool() decorators."""
        def __init__(self, *a, **kw):
            pass

        def tool(self, **kw):
            def decorator(fn):
                return fn
            return decorator

    _mcp_fastmcp.FastMCP = _StubFastMCP
    _mcp_pkg.server = _mcp_server_mod
    _mcp_server_mod.fastmcp = _mcp_fastmcp

    sys.modules["mcp"] = _mcp_pkg
    sys.modules["mcp.server"] = _mcp_server_mod
    sys.modules["mcp.server.fastmcp"] = _mcp_fastmcp


# ---------------------------------------------------------------------------
# Phase 3.1 — Mock ShotGrid API fixtures
# ---------------------------------------------------------------------------

# Sample entity data used across multiple test phases
SAMPLE_ASSETS = [
    {"type": "Asset", "id": 1001, "code": "hero_robot", "sg_asset_type": "Character",
     "sg_status_list": "ip", "project": {"type": "Project", "id": 123}},
    {"type": "Asset", "id": 1002, "code": "bg_city", "sg_asset_type": "Environment",
     "sg_status_list": "wtg", "project": {"type": "Project", "id": 123}},
    {"type": "Asset", "id": 1003, "code": "prop_sword", "sg_asset_type": "Prop",
     "sg_status_list": "cmpt", "project": {"type": "Project", "id": 123}},
]

SAMPLE_SHOTS = [
    {"type": "Shot", "id": 2001, "code": "SH010", "sg_status_list": "ip",
     "project": {"type": "Project", "id": 123},
     "sg_sequence": {"type": "Sequence", "id": 3001, "name": "SEQ01"}},
    {"type": "Shot", "id": 2002, "code": "SH020", "sg_status_list": "wtg",
     "project": {"type": "Project", "id": 123},
     "sg_sequence": {"type": "Sequence", "id": 3001, "name": "SEQ01"}},
]

SAMPLE_TASKS = [
    {"type": "Task", "id": 4001, "content": "Model", "sg_status_list": "ip",
     "entity": {"type": "Asset", "id": 1001, "name": "hero_robot"},
     "step": {"type": "Step", "id": 5001, "name": "model"},
     "project": {"type": "Project", "id": 123}},
    {"type": "Task", "id": 4002, "content": "Rig", "sg_status_list": "wtg",
     "entity": {"type": "Asset", "id": 1001, "name": "hero_robot"},
     "step": {"type": "Step", "id": 5002, "name": "rig"},
     "project": {"type": "Project", "id": 123}},
]

SAMPLE_PUBLISHED_FILE_TYPES = [
    {"type": "PublishedFileType", "id": 6001, "code": "Maya Scene"},
    {"type": "PublishedFileType", "id": 6002, "code": "Alembic Cache"},
]

SAMPLE_PROJECT = {"type": "Project", "id": 123, "name": "mcp_project_abraham"}


@pytest.fixture
def sample_assets():
    """Return a copy of sample Asset entities."""
    import copy
    return copy.deepcopy(SAMPLE_ASSETS)


@pytest.fixture
def sample_shots():
    """Return a copy of sample Shot entities."""
    import copy
    return copy.deepcopy(SAMPLE_SHOTS)


@pytest.fixture
def sample_tasks():
    """Return a copy of sample Task entities."""
    import copy
    return copy.deepcopy(SAMPLE_TASKS)


@pytest.fixture
def sample_published_file_types():
    """Return a copy of sample PublishedFileType entities."""
    import copy
    return copy.deepcopy(SAMPLE_PUBLISHED_FILE_TYPES)


@pytest.fixture
def sample_project():
    """Return a copy of sample Project entity."""
    import copy
    return copy.deepcopy(SAMPLE_PROJECT)


@pytest.fixture
def mock_sg():
    """Create a fully-configured mock shotgun_api3.Shotgun instance.

    The mock's methods (find, create, update, delete, batch) return
    sensible defaults and can be further configured per-test.

    Reusable by Phase 3.1 (SG operations), 3.2 (Toolkit), and 3.5 (tk_publish).
    """
    sg = MagicMock(name="MockShotgun")

    # Default: find returns empty list; override in individual tests
    sg.find.return_value = []
    sg.find_one.return_value = None

    # Default: create returns dict with id and type
    sg.create.side_effect = lambda entity_type, data: {
        "type": entity_type,
        "id": 9999,
        **data,
    }

    # Default: update returns entity dict with updated fields
    sg.update.side_effect = lambda entity_type, entity_id, data: {
        "type": entity_type,
        "id": entity_id,
        **data,
    }

    # Default: delete returns True
    sg.delete.return_value = True

    # Default: batch returns list of dicts matching requests
    sg.batch.side_effect = lambda requests: [
        {"type": r.get("entity_type", "Unknown"), "id": 9900 + i, **r.get("data", {})}
        for i, r in enumerate(requests)
    ]

    return sg


@pytest.fixture
def patch_sg_client(mock_sg):
    """Patch fpt_mcp.client so that get_sg() returns mock_sg and
    environment variables are set for PROJECT_ID.

    This patches:
      - fpt_mcp.client._sg_instance to the mock
      - fpt_mcp.client.PROJECT_ID to 123
      - fpt_mcp.server.PROJECT_ID to 123

    Returns the mock_sg for further assertion / configuration.
    Reusable by Phase 3.1, 3.2, and 3.5.
    """
    with patch("fpt_mcp.client._sg_instance", mock_sg), \
         patch("fpt_mcp.client.PROJECT_ID", 123), \
         patch("fpt_mcp.server.PROJECT_ID", 123):
        yield mock_sg


# ---------------------------------------------------------------------------
# Phase 3.2 — Toolkit config fixtures
# ---------------------------------------------------------------------------

FIXTURES_DIR = pathlib.Path(__file__).parent / "fixtures"


@pytest.fixture
def templates_yml_path():
    """Return the path to the fixture templates.yml file."""
    return FIXTURES_DIR / "templates.yml"


@pytest.fixture
def templates_yml_raw(templates_yml_path):
    """Return the parsed YAML dict from the fixture templates.yml."""
    with open(templates_yml_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


@pytest.fixture
def tk_config(tmp_path, templates_yml_raw):
    """Build a TkConfig instance from the fixture templates.yml.

    Uses tmp_path as the project_root so that next_version tests
    can create real files on disk without side effects.
    """
    from fpt_mcp.tk_config import TkConfig

    project_root = tmp_path / "mcp_project"
    project_root.mkdir()

    config_path = tmp_path / "setup"
    config_path.mkdir()

    return TkConfig(
        project_root=project_root,
        config_path=config_path,
        templates_raw=templates_yml_raw,
        keys_raw=templates_yml_raw.get("keys", {}),
    )


@pytest.fixture
def mock_pipeline_config():
    """Return a mock PipelineConfiguration entity as SG would return it.

    Uses /tmp/mock_pipeline as a synthetic path for all three platforms
    so that platform-dependent tests can work anywhere.
    """
    return {
        "type": "PipelineConfiguration",
        "id": 7001,
        "code": "Primary",
        "mac_path": "/tmp/mock_pipeline",
        "linux_path": "/tmp/mock_pipeline",
        "windows_path": "C:\\tmp\\mock_pipeline",
        "descriptor": None,
        "project": {"type": "Project", "id": 123},
    }


# ---------------------------------------------------------------------------
# Phase 3.3 — RAG search fixtures
# ---------------------------------------------------------------------------

# Mini corpus: 12 chunks covering all 3 API domains (shotgun_api3, toolkit,
# rest_api) plus edge cases.  Small enough for fast tests, rich enough to
# exercise HyDE domain detection, BM25 exact matching, and RRF fusion.

MINI_RAG_CORPUS = [
    # ── shotgun_api3 domain (4 chunks) ────────────────────────────────────
    {
        "id": "SG_API.md::0::sg_find",
        "text": (
            "## sg_find\n\n"
            "- `Shotgun.find(entity_type, filters, fields=None, order=None, "
            "filter_operator=None, limit=0, retired_only=False, page=0, "
            "include_archived_projects=True, additional_filter_presets=None)`\n\n"
            "Find entities matching the given filters.\n\n"
            "**Parameters:**\n"
            "- entity_type (str): The entity type to search for.\n"
            "- filters (list): A list of filter conditions.\n"
            "- fields (list): Fields to return for each entity.\n\n"
            "**Example:**\n"
            "```python\n"
            "sg.find('Asset', [['sg_status_list', 'is', 'ip']], ['code', 'sg_asset_type'])\n"
            "```"
        ),
        "metadata": {"source": "SG_API.md", "section": "sg_find", "api": "shotgun_api3"},
    },
    {
        "id": "SG_API.md::1::sg_create",
        "text": (
            "## sg_create\n\n"
            "- `Shotgun.create(entity_type, data, return_fields=None)`\n\n"
            "Create a new entity of the given type.\n\n"
            "**Parameters:**\n"
            "- entity_type (str): The entity type to create.\n"
            "- data (dict): A dictionary of field:value pairs.\n"
            "- return_fields (list): Fields to return.\n\n"
            "**Example:**\n"
            "```python\n"
            "sg.create('Asset', {'code': 'hero', 'sg_asset_type': 'Character', "
            "'project': {'type': 'Project', 'id': 123}})\n"
            "```"
        ),
        "metadata": {"source": "SG_API.md", "section": "sg_create", "api": "shotgun_api3"},
    },
    {
        "id": "SG_API.md::2::sg_batch",
        "text": (
            "## sg_batch\n\n"
            "- `Shotgun.batch(requests)`\n\n"
            "Execute multiple operations in a single API call for sg_batch.\n\n"
            "Each request is a dict with keys: request_type, entity_type, data.\n"
            "request_type can be 'create', 'update', or 'delete'.\n\n"
            "**Example:**\n"
            "```python\n"
            "batch = [\n"
            "    {'request_type': 'create', 'entity_type': 'Shot', "
            "'data': {'code': 'SH010'}},\n"
            "    {'request_type': 'update', 'entity_type': 'Shot', "
            "'entity_id': 2001, 'data': {'sg_status_list': 'cmpt'}},\n"
            "]\n"
            "sg.batch(batch)\n"
            "```"
        ),
        "metadata": {"source": "SG_API.md", "section": "sg_batch", "api": "shotgun_api3"},
    },
    {
        "id": "SG_API.md::3::Filter operators",
        "text": (
            "## Filter Operators\n\n"
            "ShotGrid supports the following filter operators:\n"
            "- `is` / `is_not` — exact match\n"
            "- `contains` / `not_contains` — substring match\n"
            "- `starts_with` / `ends_with` — string prefix/suffix\n"
            "- `in` / `not_in` — membership in list\n"
            "- `between` — range (for dates and numbers)\n"
            "- `greater_than` / `less_than` — numeric comparison\n"
            "- `type_is` / `type_is_not` — entity type check\n"
            "- `name_contains` — deep link name search\n\n"
            "Filters are passed as lists of lists."
        ),
        "metadata": {"source": "SG_API.md", "section": "Filter Operators", "api": "shotgun_api3"},
    },
    # ── toolkit domain (4 chunks) ────────────────────────────────────────
    {
        "id": "TK_API.md::0::Templates overview",
        "text": (
            "## Templates Overview\n\n"
            "Toolkit templates define the file path structure for a project.\n"
            "Templates are defined in templates.yml and contain keys like\n"
            "{Asset}, {Step}, {version}, {name}.\n\n"
            "Use `sgtk.platform.current_engine().sgtk.templates` to access\n"
            "all templates defined for the current pipeline configuration.\n\n"
            "```python\n"
            "import sgtk\n"
            "tk = sgtk.platform.current_engine().sgtk\n"
            "template = tk.templates['maya_asset_work']\n"
            "path = template.apply_fields(fields)\n"
            "```"
        ),
        "metadata": {"source": "TK_API.md", "section": "Templates overview", "api": "toolkit"},
    },
    {
        "id": "TK_API.md::1::PipelineConfiguration",
        "text": (
            "## PipelineConfiguration\n\n"
            "A PipelineConfiguration entity in ShotGrid defines the Toolkit\n"
            "setup for a project. It contains the path to the pipeline config\n"
            "directory, which holds templates.yml, roots.yml, and env/ configs.\n\n"
            "Fields: mac_path, linux_path, windows_path, descriptor.\n"
            "Mode 1: centralized config with explicit paths.\n"
            "Mode 2: distributed config using descriptors (app_store, git)."
        ),
        "metadata": {"source": "TK_API.md", "section": "PipelineConfiguration", "api": "toolkit"},
    },
    {
        "id": "TK_API.md::2::publish path resolution",
        "text": (
            "## Publish Path Resolution\n\n"
            "To resolve a publish path, use the template with context fields:\n\n"
            "```python\n"
            "template = tk.templates['maya_asset_publish']\n"
            "fields = {'Asset': 'hero_robot', 'Step': 'model', "
            "'version': 3, 'name': 'hero_robot'}\n"
            "path = template.apply_fields(fields)\n"
            "```\n\n"
            "The publish path is used by tk_publish to register the file\n"
            "in ShotGrid as a PublishedFile entity."
        ),
        "metadata": {"source": "TK_API.md", "section": "publish path resolution", "api": "toolkit"},
    },
    {
        "id": "TK_API.md::3::Hooks and context",
        "text": (
            "## Hooks and Context\n\n"
            "Toolkit hooks are Python scripts that run at specific points in\n"
            "the pipeline. The context object provides information about the\n"
            "current work area: project, entity, step, task, user.\n\n"
            "```python\n"
            "ctx = engine.context\n"
            "project = ctx.project\n"
            "entity = ctx.entity  # Asset or Shot\n"
            "```"
        ),
        "metadata": {"source": "TK_API.md", "section": "Hooks and context", "api": "toolkit"},
    },
    # ── rest_api domain (3 chunks) ───────────────────────────────────────
    {
        "id": "REST_API.md::0::Authentication",
        "text": (
            "## REST API Authentication\n\n"
            "The ShotGrid REST API uses OAuth 2.0 for authentication.\n"
            "Obtain an access token using the /api/v1/auth/access_token endpoint.\n\n"
            "```bash\n"
            "curl -X POST https://site.shotgridstudio.com/api/v1/auth/access_token \\\n"
            "  -d 'grant_type=client_credentials&client_id=SCRIPT&client_secret=KEY'\n"
            "```\n\n"
            "Include the bearer token in all subsequent requests:\n"
            "```\n"
            "Authorization: Bearer <access_token>\n"
            "```"
        ),
        "metadata": {"source": "REST_API.md", "section": "Authentication", "api": "rest_api"},
    },
    {
        "id": "REST_API.md::1::Entity CRUD",
        "text": (
            "## Entity CRUD via REST\n\n"
            "GET /api/v1/entity/{entity_type} — list/search entities\n"
            "POST /api/v1/entity/{entity_type} — create entity\n"
            "PUT /api/v1/entity/{entity_type}/{id} — update entity\n"
            "DELETE /api/v1/entity/{entity_type}/{id} — delete entity\n\n"
            "All responses follow the JSONAPI specification with data,\n"
            "links, and pagination cursors."
        ),
        "metadata": {"source": "REST_API.md", "section": "Entity CRUD", "api": "rest_api"},
    },
    {
        "id": "REST_API.md::2::Pagination",
        "text": (
            "## Pagination\n\n"
            "The REST API supports cursor-based pagination.\n"
            "Use the page[number] and page[size] query parameters.\n\n"
            "```\n"
            "GET /api/v1/entity/Asset?page[number]=2&page[size]=50\n"
            "```\n\n"
            "The response includes links.next and links.prev for navigation.\n"
            "Maximum page size is 500 entities per request."
        ),
        "metadata": {"source": "REST_API.md", "section": "Pagination", "api": "rest_api"},
    },
    # ── Irrelevant filler chunk (for no-match tests) ─────────────────────
    {
        "id": "SG_API.md::99::Changelog",
        "text": (
            "## Changelog\n\n"
            "- v3.5.0: Added support for computed fields\n"
            "- v3.4.0: Added activity_stream_read\n"
            "- v3.3.0: Improved error messages\n"
            "- v3.2.0: Python 3 support"
        ),
        "metadata": {"source": "SG_API.md", "section": "Changelog", "api": "shotgun_api3"},
    },
]


def _make_deterministic_embedding_fn():
    """Build a ChromaDB-compatible deterministic embedding function.

    Generates 64-dimensional vectors from a SHA-256 hash of the input text.
    No model download required — fast and reproducible.  Semantically similar
    texts will NOT produce similar vectors (this is a hash, not a learned
    embedding), but that's fine for testing the search *plumbing*: indexing,
    BM25, RRF fusion, formatting, and error handling.

    Uses the chromadb.EmbeddingFunction base class so that embed_query,
    embed_with_retries, is_legacy, etc. are all properly inherited.
    """
    import chromadb

    class _DetEF(chromadb.EmbeddingFunction):
        def __call__(self, input: list[str]) -> list[list[float]]:  # noqa: A002
            vectors = []
            for text in input:
                digest = hashlib.sha256(text.encode("utf-8")).digest()
                vec = [(b / 127.5) - 1.0 for b in digest]
                vec = (vec * 2)[:64]
                vectors.append(vec)
            return vectors

        @staticmethod
        def name() -> str:
            return "deterministic_test"

        def build_from_config(self, config):
            return _DetEF()

        def get_config(self):
            return {}

    return _DetEF()


@pytest.fixture
def mini_rag_corpus():
    """Return a copy of the mini RAG corpus (12 chunks, 3 API domains)."""
    import copy
    return copy.deepcopy(MINI_RAG_CORPUS)


@pytest.fixture
def rag_chroma_collection(tmp_path, mini_rag_corpus):
    """Build a temporary ChromaDB collection from the mini corpus.

    Returns (collection, index_dir) where index_dir is a str path to the
    temporary ChromaDB persistent directory.
    """
    import chromadb

    index_dir = str(tmp_path / "rag_index")
    client = chromadb.PersistentClient(path=index_dir)

    embedding_fn = _make_deterministic_embedding_fn()
    collection = client.create_collection(
        name="sg_docs",
        embedding_function=embedding_fn,
        metadata={"hnsw:space": "cosine"},
    )

    collection.add(
        ids=[c["id"] for c in mini_rag_corpus],
        documents=[c["text"] for c in mini_rag_corpus],
        metadatas=[c["metadata"] for c in mini_rag_corpus],
    )

    return collection, index_dir


@pytest.fixture
def rag_corpus_json(tmp_path, mini_rag_corpus):
    """Write the mini corpus as corpus.json for BM25 and return the path."""
    corpus_path = tmp_path / "corpus.json"
    corpus_path.write_text(
        json.dumps(mini_rag_corpus, ensure_ascii=False),
        encoding="utf-8",
    )
    return str(corpus_path)


@pytest.fixture
def rag_empty_collection(tmp_path):
    """Build an empty ChromaDB collection (0 chunks) for edge-case tests.

    Returns (collection, index_dir).
    """
    import chromadb

    index_dir = str(tmp_path / "empty_index")
    client = chromadb.PersistentClient(path=index_dir)

    embedding_fn = _make_deterministic_embedding_fn()
    collection = client.create_collection(
        name="sg_docs",
        embedding_function=embedding_fn,
        metadata={"hnsw:space": "cosine"},
    )

    return collection, index_dir


@pytest.fixture
def patch_rag_singletons(rag_chroma_collection, rag_corpus_json):
    """Patch search.py module-level singletons to use the test index.

    Replaces:
      - _collection → test ChromaDB collection
      - _bm25 / _bm25_docs → BM25 built from mini corpus
      - INDEX_DIR → test index directory
      - CORPUS_PATH → test corpus.json
      - _search_cache → fresh empty dict

    Yields (collection, bm25, bm25_docs) for assertions.
    """
    from rank_bm25 import BM25Okapi

    collection, index_dir = rag_chroma_collection

    # Build BM25 from the corpus file
    with open(rag_corpus_json, "r", encoding="utf-8") as f:
        corpus = json.load(f)
    tokenised = [entry["text"].lower().split() for entry in corpus]
    bm25 = BM25Okapi(tokenised)

    with patch("fpt_mcp.rag.search._collection", collection), \
         patch("fpt_mcp.rag.search._bm25", bm25), \
         patch("fpt_mcp.rag.search._bm25_docs", corpus), \
         patch("fpt_mcp.rag.search.INDEX_DIR", index_dir), \
         patch("fpt_mcp.rag.search.CORPUS_PATH", rag_corpus_json), \
         patch("fpt_mcp.rag.search._search_cache", {}):
        yield collection, bm25, corpus
