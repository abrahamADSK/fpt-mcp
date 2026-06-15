"""
test_sg_errors.py
=================
Tests for sg_errors.py — translating shotgun_api3 / connection exceptions into
the repo's standard structured-error JSON shape at the tool boundary
(proposals/fpt-sg-fault-to-json.md, Option A).

No live ShotGrid connection is required: every fault is constructed directly
and the decorated impls are exercised by patching the server-level seams the
existing suite already uses (`fpt_mcp.server.sg_find`, `fpt_mcp.server.sg_batch`).

Coverage:
  1. to_structured_error — one case per _RULES row (error_type + retryable +
     non-empty hint) and subclass-before-base ordering.
  2. Secret scrubbing + 300-char truncation (OPSEC).
  3. Re-raise of unrecognised exceptions (a real bug is never swallowed).
  4. Decorated impl: an AuthenticationFault raised mid-call becomes structured
     JSON; a successful call is byte-for-byte unchanged.
  5. Dispatcher turn-counting: a ProtocolError during fpt_bulk is counted as a
     failed turn (the p_fallo undercount fix).
  6. Every SG-touching impl actually carries the decorator.
"""

import asyncio
import json
import socket
import ssl
import urllib.error
from unittest.mock import AsyncMock, patch

import pytest
import shotgun_api3 as sg

from fpt_mcp.sg_errors import (
    _MAX_MSG,
    sg_errors_to_json,
    to_structured_error,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def run_async(coro):
    return asyncio.run(coro)


def _make(exc_cls):
    """Construct an instance of each mapped exception class.

    Most take a single message; ProtocolError needs the (url, errcode, errmsg,
    headers) 4-tuple, and URLError takes a reason.
    """
    if exc_cls is sg.ProtocolError:
        return sg.ProtocolError("https://site.example", 502, "Bad Gateway", {})
    if exc_cls is urllib.error.URLError:
        return urllib.error.URLError("name resolution failed")
    return exc_cls("boom")


# (exception class, expected error_type, expected retryable)
_EXPECTED = [
    (sg.MissingTwoFactorAuthenticationFault, "two_factor_required", False),
    (sg.UserCredentialsNotAllowedForSSOAuthenticationFault, "sso_credentials_rejected", False),
    (sg.AuthenticationFault, "authentication_failed", False),
    (sg.ProtocolError, "protocol_error", True),
    (sg.ResponseError, "malformed_response", True),
    (sg.ShotgunFileDownloadError, "download_failed", True),
    (sg.Fault, "shotgrid_api_fault", False),
    (ssl.SSLError, "ssl_error", False),
    (socket.timeout, "timeout", True),
    (TimeoutError, "timeout", True),
    (urllib.error.URLError, "connection_error", True),
    (ConnectionError, "connection_error", True),
    (EnvironmentError, "config_error", False),
]


# ---------------------------------------------------------------------------
# 1. to_structured_error — one case per rule
# ---------------------------------------------------------------------------

class TestToStructuredError:

    @pytest.mark.parametrize("exc_cls,error_type,retryable", _EXPECTED)
    def test_each_rule_maps_to_expected_shape(self, exc_cls, error_type, retryable):
        result = to_structured_error(_make(exc_cls))
        assert result is not None
        # Exactly the four-key contract, nothing else.
        assert set(result) == {"error", "error_type", "hint", "retryable"}
        assert result["error_type"] == error_type
        assert result["retryable"] is retryable
        assert isinstance(result["hint"], str) and result["hint"]
        assert isinstance(result["error"], str) and result["error"]

    def test_subclass_matched_before_base(self):
        """AuthenticationFault is a Fault subclass; it must map to
        authentication_failed, NOT the base shotgrid_api_fault."""
        assert to_structured_error(sg.AuthenticationFault("x"))["error_type"] == "authentication_failed"
        # And the base still maps to the generic bucket.
        assert to_structured_error(sg.Fault("x"))["error_type"] == "shotgrid_api_fault"

    def test_two_factor_not_swallowed_by_auth_rule(self):
        """MissingTwoFactorAuthenticationFault is a sibling of AuthenticationFault
        (both subclass Fault); it must keep its own dedicated error_type."""
        exc = sg.MissingTwoFactorAuthenticationFault("2fa required")
        assert to_structured_error(exc)["error_type"] == "two_factor_required"

    def test_environment_error_is_config_error_with_message_passthrough(self):
        """EnvironmentError (== OSError) folds into config_error and passes the
        rich _validate_config message through as the hint (empty static hint)."""
        msg = "Missing required environment variables: SHOTGRID_URL. Check your .env file."
        result = to_structured_error(EnvironmentError(msg))
        assert result["error_type"] == "config_error"
        assert result["retryable"] is False
        # hint falls back to the message verbatim (no static credential advice
        # that could mislabel a stray non-config OSError).
        assert result["hint"] == msg
        assert result["error"] == msg


# ---------------------------------------------------------------------------
# 2. Secret scrubbing + truncation (OPSEC)
# ---------------------------------------------------------------------------

class TestSecretScrubbing:

    def test_redacts_script_key_value(self):
        result = to_structured_error(sg.Fault("server text with script_key=ABC123 inside"))
        assert "ABC123" not in result["error"]
        assert "***redacted***" in result["error"]

    def test_redacts_multiple_credential_shapes(self):
        exc = sg.Fault("api_key: XYZ789 token=qqq password='p@ss'")
        scrubbed = to_structured_error(exc)["error"]
        for secret in ("XYZ789", "qqq", "p@ss"):
            assert secret not in scrubbed

    def test_does_not_redact_field_name_only_message(self):
        """A message that merely NAMES a credential field (no key=value) must
        stay intact — this is the common AuthenticationFault string."""
        msg = "API read() invalid script_name or api_key"
        result = to_structured_error(sg.AuthenticationFault(msg))
        assert result["error"] == msg

    def test_truncates_to_max(self):
        result = to_structured_error(sg.Fault("x" * 1000))
        assert len(result["error"]) == _MAX_MSG == 300


# ---------------------------------------------------------------------------
# 3. Re-raise of unrecognised exceptions
# ---------------------------------------------------------------------------

class TestReRaise:

    @pytest.mark.parametrize("exc", [KeyError("k"), TypeError("t"), ValueError("v")])
    def test_unrecognised_returns_none(self, exc):
        assert to_structured_error(exc) is None

    def test_decorator_reraises_unrecognised(self):
        @sg_errors_to_json
        async def boom() -> str:
            raise KeyError("a real bug")

        with pytest.raises(KeyError):
            run_async(boom())

    def test_decorator_maps_recognised(self):
        @sg_errors_to_json
        async def boom() -> str:
            raise sg.AuthenticationFault("bad key")

        out = json.loads(run_async(boom()))
        assert out["error_type"] == "authentication_failed"
        assert out["retryable"] is False

    def test_decorator_passthrough_on_success(self):
        @sg_errors_to_json
        async def ok() -> str:
            return json.dumps({"entities": [], "total": 0})

        out = json.loads(run_async(ok()))
        assert out == {"entities": [], "total": 0}
        assert "error" not in out


# ---------------------------------------------------------------------------
# 4. Decorated impl integration
# ---------------------------------------------------------------------------

class TestDecoratedImplIntegration:

    def test_sg_find_impl_auth_fault_becomes_structured(self, patch_sg_client):
        """An AuthenticationFault raised by the SG layer surfaces as structured
        JSON instead of propagating out of the impl as a raw exception."""
        from fpt_mcp.shotgrid import sg_find_impl
        from fpt_mcp.models import SgFindInput

        fault = sg.AuthenticationFault("API read() invalid script_name or api_key")
        with patch("fpt_mcp.server.sg_find", new=AsyncMock(side_effect=fault)):
            params = SgFindInput(
                entity_type="Asset",
                filters=[["code", "is", "hero"]],
                fields=["id", "code"],
                limit=50,
            )
            out = json.loads(run_async(sg_find_impl(params)))

        assert out["error_type"] == "authentication_failed"
        assert out["retryable"] is False
        assert "error" in out and out["hint"]

    def test_sg_find_impl_success_unchanged(self, patch_sg_client, sample_assets):
        """The decorator is a pure pass-through on success."""
        from fpt_mcp.shotgrid import sg_find_impl
        from fpt_mcp.models import SgFindInput

        patch_sg_client.find.return_value = sample_assets
        params = SgFindInput(
            entity_type="Asset",
            filters=[["code", "is", "hero"]],
            fields=["id", "code"],
            limit=50,
        )
        out = json.loads(run_async(sg_find_impl(params)))

        assert "error" not in out
        assert out["total"] == len(sample_assets)
        assert out["entities"][0]["code"] == "hero_robot"

    def test_connection_error_becomes_retryable_structured(self, patch_sg_client):
        from fpt_mcp.shotgrid import sg_find_impl
        from fpt_mcp.models import SgFindInput

        with patch(
            "fpt_mcp.server.sg_find",
            new=AsyncMock(side_effect=urllib.error.URLError("refused")),
        ):
            params = SgFindInput(
                entity_type="Asset",
                filters=[["code", "is", "hero"]],
                fields=["id"],
                limit=10,
            )
            out = json.loads(run_async(sg_find_impl(params)))

        assert out["error_type"] == "connection_error"
        assert out["retryable"] is True


# ---------------------------------------------------------------------------
# 5. Dispatcher turn-counting (p_fallo undercount fix)
# ---------------------------------------------------------------------------

class TestDispatcherTurnCounting:

    def test_protocol_error_in_batch_counts_failed_turn(self, patch_sg_client):
        """A ProtocolError during fpt_bulk(batch) now returns structured JSON
        AND is counted as a failed turn — previously the raised exception
        bypassed _count_turn and silently understated p_fallo."""
        from fpt_mcp.server import fpt_bulk, _stats
        from fpt_mcp.models import BulkDispatchInput

        before_total = _stats["turns_total"]
        before_failed = _stats["failed_turns"]

        fault = sg.ProtocolError("https://site.example", 502, "Bad Gateway", {})
        batch_requests = [
            {
                "request_type": "create",
                "entity_type": "Shot",
                "data": {"code": "SH010", "project": {"type": "Project", "id": 123}},
            }
        ]
        params = BulkDispatchInput(
            action="batch", params={"requests": json.dumps(batch_requests)}
        )

        with patch("fpt_mcp.server.sg_batch", new=AsyncMock(side_effect=fault)):
            out = json.loads(run_async(fpt_bulk(params)))

        assert out["error_type"] == "protocol_error"
        assert out["retryable"] is True
        assert _stats["turns_total"] == before_total + 1
        assert _stats["failed_turns"] == before_failed + 1


# ---------------------------------------------------------------------------
# 6. Every SG-touching impl carries the decorator
# ---------------------------------------------------------------------------

class TestAllImplsDecorated:

    def test_every_impl_is_wrapped(self):
        """functools.wraps sets __wrapped__ on every decorated function. Guards
        against a new SG tool being added without the decorator (mirrors the
        sg_fault_error_contract concept invariant)."""
        from fpt_mcp import shotgrid, reporting, toolkit_tools, launcher

        decorated = {
            shotgrid: [
                "sg_find_impl", "sg_create_impl", "sg_update_impl",
                "sg_schema_impl", "sg_upload_impl", "sg_download_impl",
                "_do_sg_delete", "_do_sg_batch", "_do_sg_revive",
            ],
            reporting: [
                "_do_sg_text_search", "_do_sg_summarize",
                "_do_sg_note_thread", "_do_sg_activity",
            ],
            toolkit_tools: ["tk_resolve_path_impl", "tk_publish_impl"],
            launcher: ["fpt_launch_app_impl"],
        }

        total = 0
        for module, names in decorated.items():
            for name in names:
                fn = getattr(module, name)
                assert hasattr(fn, "__wrapped__"), f"{module.__name__}.{name} is not decorated"
                total += 1
        assert total == 16
