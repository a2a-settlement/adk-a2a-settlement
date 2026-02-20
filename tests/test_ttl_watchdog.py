"""Tests for the EscrowTTLWatchdog and settlement_ttl_minutes config."""

from __future__ import annotations

import os
import time
import threading
from unittest.mock import MagicMock, patch

import pytest

from adk_a2a_settlement.config import SettlementConfig
from adk_a2a_settlement.errors import SettlementError, SettlementErrorCode
from adk_a2a_settlement.requester import EscrowTTLWatchdog


class TestSettlementTTLConfig:

    def test_default_ttl(self):
        with patch.dict(os.environ, {}, clear=True):
            cfg = SettlementConfig(api_key="test")
            assert cfg.settlement_ttl_minutes == 15

    def test_custom_ttl(self):
        cfg = SettlementConfig(api_key="test", settlement_ttl_minutes=5)
        assert cfg.settlement_ttl_minutes == 5

    def test_disabled_ttl(self):
        cfg = SettlementConfig(api_key="test", settlement_ttl_minutes=0)
        assert cfg.settlement_ttl_minutes == 0

    def test_invalid_negative_ttl(self):
        with pytest.raises(ValueError, match="settlement_ttl_minutes"):
            SettlementConfig(api_key="test", settlement_ttl_minutes=-1)

    def test_invalid_over_max_ttl(self):
        with pytest.raises(ValueError, match="settlement_ttl_minutes"):
            SettlementConfig(api_key="test", settlement_ttl_minutes=1441)

    def test_explicit_override(self):
        """Env var defaults are captured at import time; explicit args always work."""
        cfg = SettlementConfig(api_key="test", settlement_ttl_minutes=10)
        assert cfg.settlement_ttl_minutes == 10


class TestEscrowTTLWatchdog:

    def test_start_and_stop(self):
        exchange = MagicMock()
        watchdog = EscrowTTLWatchdog(exchange, ttl_minutes=1, poll_interval_seconds=0.1)

        watchdog.start()
        assert watchdog.is_running
        watchdog.stop()
        assert not watchdog.is_running

    def test_start_is_idempotent(self):
        exchange = MagicMock()
        watchdog = EscrowTTLWatchdog(exchange, ttl_minutes=1, poll_interval_seconds=0.1)
        watchdog.start()
        thread1 = watchdog._thread
        watchdog.start()
        assert watchdog._thread is thread1
        watchdog.stop()

    def test_track_and_untrack(self):
        exchange = MagicMock()
        watchdog = EscrowTTLWatchdog(exchange, ttl_minutes=10, poll_interval_seconds=60)

        escrow = {"escrow_id": "esc-001", "amount": 100}
        watchdog.track("task-1", escrow)
        assert watchdog.tracked_count() == 1

        watchdog.untrack("task-1")
        assert watchdog.tracked_count() == 0

    def test_auto_refund_on_expiry(self):
        """Escrows exceeding TTL are auto-refunded."""
        exchange = MagicMock()
        expired_tasks: list[str] = []

        def on_expired(task_id, escrow_id, escrow):
            expired_tasks.append(task_id)

        # Use a very short TTL for testing (0.05 min = 3 seconds)
        # but we'll monkey-patch the created_at to make it already expired
        watchdog = EscrowTTLWatchdog(
            exchange,
            ttl_minutes=1,
            poll_interval_seconds=0.05,
            on_expired=on_expired,
        )

        escrow = {"escrow_id": "esc-expired", "amount": 200}
        watchdog.track("task-expired", escrow)

        # Backdate the entry so it appears expired
        with watchdog._lock:
            entry = watchdog._tracked["task-expired"]
            entry.created_at = time.monotonic() - 120  # 2 minutes ago

        watchdog.start()
        time.sleep(0.3)
        watchdog.stop()

        exchange.refund_escrow.assert_called_once_with(
            escrow_id="esc-expired",
            reason="Settlement TTL exceeded — auto-released by watchdog",
        )
        assert "task-expired" in expired_tasks
        assert watchdog.tracked_count() == 0

    def test_normal_escrows_not_expired(self):
        """Escrows within TTL are not touched."""
        exchange = MagicMock()

        watchdog = EscrowTTLWatchdog(
            exchange, ttl_minutes=60, poll_interval_seconds=0.05
        )

        escrow = {"escrow_id": "esc-active", "amount": 100}
        watchdog.track("task-active", escrow)

        watchdog.start()
        time.sleep(0.2)
        watchdog.stop()

        exchange.refund_escrow.assert_not_called()
        assert watchdog.tracked_count() == 1

    def test_refund_failure_does_not_crash(self):
        """Watchdog survives exchange failures during refund."""
        exchange = MagicMock()
        exchange.refund_escrow.side_effect = Exception("Network error")

        watchdog = EscrowTTLWatchdog(
            exchange, ttl_minutes=1, poll_interval_seconds=0.05
        )

        escrow = {"escrow_id": "esc-fail", "amount": 50}
        watchdog.track("task-fail", escrow)

        with watchdog._lock:
            entry = watchdog._tracked["task-fail"]
            entry.created_at = time.monotonic() - 120

        watchdog.start()
        time.sleep(0.3)
        watchdog.stop()

        exchange.refund_escrow.assert_called_once()

    def test_callback_failure_does_not_crash(self):
        """Watchdog survives on_expired callback failures."""
        exchange = MagicMock()

        def bad_callback(task_id, escrow_id, escrow):
            raise RuntimeError("callback boom")

        watchdog = EscrowTTLWatchdog(
            exchange, ttl_minutes=1, poll_interval_seconds=0.05,
            on_expired=bad_callback,
        )

        escrow = {"escrow_id": "esc-cb-fail", "amount": 30}
        watchdog.track("task-cb-fail", escrow)

        with watchdog._lock:
            entry = watchdog._tracked["task-cb-fail"]
            entry.created_at = time.monotonic() - 120

        watchdog.start()
        time.sleep(0.3)
        watchdog.stop()

        exchange.refund_escrow.assert_called_once()


class TestProviderVerifyEscrowErrors:
    """Test that verify_escrow raises structured errors when raise_on_error=True."""

    def test_raises_on_no_metadata(self):
        from adk_a2a_settlement.provider import verify_escrow
        from types import SimpleNamespace

        agent = SimpleNamespace(name="test")
        with pytest.raises(SettlementError) as exc_info:
            verify_escrow(agent, {"metadata": {}}, raise_on_error=True)
        assert exc_info.value.code == SettlementErrorCode.ESCROW_NOT_FOUND

    def test_raises_on_provider_mismatch(self):
        from adk_a2a_settlement.provider import verify_escrow
        from types import SimpleNamespace

        agent = SimpleNamespace(
            name="test_agent",
            _settlement_exchange=MagicMock(),
            _settlement_account_id="prov-001",
        )
        agent._settlement_exchange.get_escrow.return_value = {
            "id": "esc-001", "status": "held", "provider_id": "prov-other",
        }
        message = {"metadata": {"a2a-se": {"escrowId": "esc-001"}}}

        with pytest.raises(SettlementError) as exc_info:
            verify_escrow(agent, message, raise_on_error=True)
        assert exc_info.value.code == SettlementErrorCode.PROVIDER_MISMATCH

    def test_raises_on_already_settled(self):
        from adk_a2a_settlement.provider import verify_escrow
        from types import SimpleNamespace

        agent = SimpleNamespace(
            name="test_agent",
            _settlement_exchange=MagicMock(),
            _settlement_account_id="prov-001",
        )
        agent._settlement_exchange.get_escrow.return_value = {
            "id": "esc-001", "status": "released", "provider_id": "prov-001",
        }
        message = {"metadata": {"a2a-se": {"escrowId": "esc-001"}}}

        with pytest.raises(SettlementError) as exc_info:
            verify_escrow(agent, message, raise_on_error=True)
        assert exc_info.value.code == SettlementErrorCode.ESCROW_ALREADY_SETTLED

    def test_raises_on_expired(self):
        from adk_a2a_settlement.provider import verify_escrow
        from types import SimpleNamespace

        agent = SimpleNamespace(
            name="test_agent",
            _settlement_exchange=MagicMock(),
            _settlement_account_id="prov-001",
        )
        agent._settlement_exchange.get_escrow.return_value = {
            "id": "esc-001", "status": "expired", "provider_id": "prov-001",
        }
        message = {"metadata": {"a2a-se": {"escrowId": "esc-001"}}}

        with pytest.raises(SettlementError) as exc_info:
            verify_escrow(agent, message, raise_on_error=True)
        assert exc_info.value.code == SettlementErrorCode.ESCROW_EXPIRED

    def test_backward_compat_returns_none(self):
        """Without raise_on_error, verify_escrow still returns None."""
        from adk_a2a_settlement.provider import verify_escrow
        from types import SimpleNamespace

        agent = SimpleNamespace(name="test")
        result = verify_escrow(agent, {"metadata": {}})
        assert result is None


class TestRequesterStructuredErrors:

    def test_create_escrow_raises_settlement_not_advertised(self):
        """Should raise SETTLEMENT_NOT_ADVERTISED when agent has no settlement info."""
        from adk_a2a_settlement.requester import SettledRemoteAgent

        with patch("adk_a2a_settlement.requester.RemoteA2aAgent", create=True):
            with patch("adk_a2a_settlement.requester.SettlementExchangeClient"):
                with patch("adk_a2a_settlement.requester.discover_settlement", return_value=None):
                    agent = SettledRemoteAgent.__new__(SettledRemoteAgent)
                    agent.name = "test"
                    agent._config = SettlementConfig(api_key="k", settlement_ttl_minutes=0)
                    agent._settlement_info = None
                    agent._active_escrows = {}
                    agent._escrow_lock = threading.Lock()
                    agent._exchange = MagicMock()
                    agent._watchdog = None

        with pytest.raises(SettlementError) as exc_info:
            agent.create_escrow(task_id="task-1")
        assert exc_info.value.code == SettlementErrorCode.SETTLEMENT_NOT_ADVERTISED

    def test_release_raises_escrow_not_found(self):
        from adk_a2a_settlement.requester import SettledRemoteAgent

        agent = SettledRemoteAgent.__new__(SettledRemoteAgent)
        agent.name = "test"
        agent._active_escrows = {}
        agent._escrow_lock = threading.Lock()
        agent._watchdog = None

        with pytest.raises(SettlementError) as exc_info:
            agent.release("nonexistent-task")
        assert exc_info.value.code == SettlementErrorCode.ESCROW_NOT_FOUND
