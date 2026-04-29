"""Tests for the telemetry event factories — privacy posture and shape."""

from __future__ import annotations

from bcli.telemetry import events
from bcli.telemetry.events import _sanitise


# ── Schema basics ─────────────────────────────────────────────────────


class TestStartup:
    def test_returns_event_tuple(self):
        name, props = events.startup(profile="prod", environment="Production", command="get vendors")
        assert name == "bcli.startup"
        assert props["profile"] == "prod"
        assert props["environment"] == "Production"
        assert props["command"] == "get vendors"

    def test_includes_runtime_metadata(self):
        _, props = events.startup(profile="x")
        assert "version" in props
        assert "os" in props
        assert "python_version" in props


class TestCommand:
    def test_basic_shape(self):
        name, props = events.command(
            command="get vendors",
            profile="prod",
            duration_ms=412.7,
        )
        assert name == "bcli.command"
        assert props["command"] == "get vendors"
        assert props["duration_ms"] == 412.7
        assert props["status"] == "ok"

    def test_error_status(self):
        _, props = events.command(command="x", profile="p", status="error")
        assert props["status"] == "error"


class TestQuery:
    def test_omits_filter_text_by_default(self):
        _, props = events.query(endpoint="vendors", has_filter=True)
        assert props["has_filter"] is True
        assert "filter_text" not in props

    def test_includes_filter_text_only_when_passed(self):
        _, props = events.query(
            endpoint="vendors", has_filter=True,
            filter_text="displayName eq 'Fabrikam'",
        )
        assert props["filter_text"] == "displayName eq 'Fabrikam'"

    def test_normalises_optional_ints(self):
        _, props = events.query(endpoint="x", has_filter=False)
        # Defaults should be present and integer-typed (not None) — flat-primitive guarantee.
        assert props["top"] == -1
        assert props["skip"] == -1
        assert props["status"] == 0


class TestAuth:
    def test_omits_upn_by_default(self):
        _, props = events.auth(method="device", status="ok")
        assert "user_upn" not in props
        assert props["method"] == "device"
        assert props["status"] == "ok"

    def test_includes_upn_when_passed(self):
        _, props = events.auth(method="device", status="ok", user_upn="x@y.com")
        assert props["user_upn"] == "x@y.com"


class TestError:
    def test_basic_shape(self):
        name, props = events.error(error_class="HTTPError", http_status=403, endpoint="vendors")
        assert name == "bcli.error"
        assert props["error_class"] == "HTTPError"
        assert props["http_status"] == 403
        assert props["endpoint"] == "vendors"

    def test_sanitises_bc_message(self):
        token = "ey" + "A" * 30 + "." + "B" * 10 + "." + "C" * 10
        msg = f"Authorization failed: Bearer {token} expired"
        _, props = events.error(error_class="AuthError", bc_message=msg)
        assert "[REDACTED]" in props["bc_message"]
        assert token not in props["bc_message"]


# ── Sanitiser unit tests ──────────────────────────────────────────────


class TestSanitise:
    def test_redacts_bearer_token(self):
        out = _sanitise("oops Bearer abcdef.ghijkl-mnop dropped")
        assert "Bearer" not in out
        assert "[REDACTED]" in out

    def test_redacts_jwt_shape(self):
        jwt = "ey" + "A" * 30 + "." + "B" * 10 + "." + "C" * 10
        out = _sanitise(f"token={jwt} more")
        assert jwt not in out

    def test_redacts_long_hex(self):
        hex_blob = "deadbeef" * 10  # 80 hex chars
        out = _sanitise(f"key={hex_blob} other")
        assert hex_blob not in out

    def test_redacts_instrumentation_key(self):
        out = _sanitise("InstrumentationKey=abc-def-123 followed")
        assert "InstrumentationKey=abc-def-123" not in out

    def test_caps_message_length(self):
        out = _sanitise("x" * 2000)
        assert len(out) <= 501  # 500 + ellipsis

    def test_empty_passthrough(self):
        assert _sanitise("") == ""
