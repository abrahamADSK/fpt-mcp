"""Bucket E — Structural test: all Pydantic input models use _STRICT_CONFIG.

Every Pydantic BaseModel used as a tool input parameter must have:
    model_config = _STRICT_CONFIG

where _STRICT_CONFIG = ConfigDict(extra="forbid", str_strip_whitespace=True).

This prevents LLMs from sending hallucinated keys that silently pass through
to ShotGrid (the most common cause of confusing API errors). The test
introspects the actual model classes to verify the config, rather than
grepping source text.

No ShotGrid connection or MCP SDK required.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from pydantic import BaseModel, ValidationError

# Import server module to get all input models
from fpt_mcp import server


SRC_DIR = Path(__file__).resolve().parent.parent / "src"


def _find_all_input_models() -> list[tuple[str, type]]:
    """Discover all Pydantic BaseModel subclasses in fpt_mcp.server whose name
    ends with 'Input' (the naming convention for tool parameter models).

    Returns list of (name, class) tuples.
    """
    models = []
    for attr_name in dir(server):
        obj = getattr(server, attr_name)
        if (
            isinstance(obj, type)
            and issubclass(obj, BaseModel)
            and obj is not BaseModel
            and attr_name.endswith("Input")
        ):
            models.append((attr_name, obj))
    return sorted(models, key=lambda t: t[0])


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestStrictConfig:
    """Verify all Pydantic input models enforce extra='forbid'."""

    @pytest.fixture(scope="class")
    def input_models(self) -> list[tuple[str, type]]:
        models = _find_all_input_models()
        assert models, "No *Input models found in fpt_mcp.server"
        return models

    def test_all_input_models_discovered(self, input_models):
        """Sanity check: we should find at least the known input models."""
        names = {name for name, _ in input_models}
        expected = {
            "SgFindInput",
            "SgCreateInput",
            "SgUpdateInput",
            "SgDeleteInput",
            "SgSchemaInput",
            "SgUploadInput",
            "SgDownloadInput",
            "TkResolvePathInput",
            "TkPublishInput",
            "BulkDispatchInput",
            "ReportingDispatchInput",
            "FptLaunchAppInput",
            "SearchSgDocsInput",
            "LearnPatternInput",
        }
        missing = expected - names
        assert not missing, f"Expected models not found: {sorted(missing)}"

    def test_all_models_forbid_extra(self, input_models):
        """Every *Input model must have extra='forbid' in model_config."""
        failures = []
        for name, cls in input_models:
            config = cls.model_config
            extra_setting = config.get("extra")
            if extra_setting != "forbid":
                failures.append(
                    f"{name}: extra={extra_setting!r} (expected 'forbid')"
                )
        assert not failures, (
            "The following models do NOT have extra='forbid':\n"
            + "\n".join(f"  - {f}" for f in failures)
        )

    def test_all_models_strip_whitespace(self, input_models):
        """Every *Input model must have str_strip_whitespace=True."""
        failures = []
        for name, cls in input_models:
            config = cls.model_config
            strip_setting = config.get("str_strip_whitespace")
            if strip_setting is not True:
                failures.append(
                    f"{name}: str_strip_whitespace={strip_setting!r} (expected True)"
                )
        assert not failures, (
            "The following models do NOT have str_strip_whitespace=True:\n"
            + "\n".join(f"  - {f}" for f in failures)
        )

    @pytest.mark.parametrize(
        "model_name",
        [
            "SgFindInput",
            "SgCreateInput",
            "SgUpdateInput",
            "BulkDispatchInput",
            "ReportingDispatchInput",
            "FptLaunchAppInput",
            "SearchSgDocsInput",
            "LearnPatternInput",
        ],
    )
    def test_extra_field_rejected(self, model_name):
        """Sending an extra field to a strict model must raise ValidationError."""
        cls = getattr(server, model_name)
        # Build minimal valid data for each model, then add an extra key
        minimal_data = _build_minimal_data(model_name)
        minimal_data["hallucinated_extra_field"] = "should be rejected"

        with pytest.raises(ValidationError) as exc_info:
            cls(**minimal_data)

        # The error should mention the extra field
        error_text = str(exc_info.value)
        assert "hallucinated_extra_field" in error_text or "extra" in error_text.lower()


def _build_minimal_data(model_name: str) -> dict:
    """Return the minimal valid data dict for each known model."""
    data_map = {
        "SgFindInput": {
            "entity_type": "Asset",
        },
        "SgCreateInput": {
            "entity_type": "Asset",
            "data": {"code": "test"},
        },
        "SgUpdateInput": {
            "entity_type": "Asset",
            "entity_id": 1,
            "data": {"code": "test"},
        },
        "BulkDispatchInput": {
            "action": "delete",
            "params": {"entity_type": "Task", "entity_id": 1},
        },
        "ReportingDispatchInput": {
            "action": "text_search",
            "params": {"text": "test", "entity_types": '{"Asset":[]}'},
        },
        "FptLaunchAppInput": {
            "app": "maya",
            "entity_type": "Asset",
            "entity_id": 1,
        },
        "SearchSgDocsInput": {
            "query": "how to filter assets",
        },
        "LearnPatternInput": {
            "description": "test pattern",
            "code": "sg.find('Asset', [])",
        },
    }
    return dict(data_map.get(model_name, {}))


class TestStrictConfigSourceLevel:
    """Source-level verification that _STRICT_CONFIG is defined correctly
    and used by all models (catches copy-paste mistakes where a model
    defines its own ConfigDict instead of using the shared constant)."""

    def test_strict_config_defined(self):
        """_STRICT_CONFIG must be defined in server.py."""
        assert hasattr(server, "_STRICT_CONFIG"), (
            "_STRICT_CONFIG not found in fpt_mcp.server"
        )

    def test_strict_config_value(self):
        """_STRICT_CONFIG must have extra='forbid' and str_strip_whitespace=True."""
        config = server._STRICT_CONFIG
        assert config.get("extra") == "forbid"
        assert config.get("str_strip_whitespace") is True

    def test_no_inline_configdict_in_models(self):
        """No *Input model should define its own ConfigDict inline —
        they should all reference _STRICT_CONFIG for consistency."""
        source_path = SRC_DIR / "fpt_mcp" / "server.py"
        content = source_path.read_text(encoding="utf-8")

        # Find all "model_config = ConfigDict(" occurrences (inline definitions)
        # These are wrong — models should use "model_config = _STRICT_CONFIG"
        inline_configs = re.findall(
            r'class\s+(\w+Input).*?model_config\s*=\s*ConfigDict\(',
            content,
            re.DOTALL,
        )
        assert not inline_configs, (
            f"Models with inline ConfigDict (should use _STRICT_CONFIG): "
            f"{inline_configs}"
        )
