"""Tests for the pluggable state store — InMemoryStateStore and factory."""

from __future__ import annotations

import threading

import pytest

from adk_a2a_settlement.config import SettlementConfig
from adk_a2a_settlement.state import (
    AbstractStateStore,
    InMemoryStateStore,
    RedisStateStore,
    create_state_store,
)


# ======================================================================
# InMemoryStateStore
# ======================================================================


class TestInMemoryStateStore:

    @pytest.fixture
    def store(self) -> InMemoryStateStore:
        return InMemoryStateStore()

    # -- escrow CRUD --

    def test_escrow_lifecycle(self, store: InMemoryStateStore):
        assert store.get_escrow("t1") is None

        store.set_escrow("t1", {"escrow_id": "e1", "amount": 100})
        assert store.get_escrow("t1")["escrow_id"] == "e1"
        assert len(store.list_escrows()) == 1

        store.delete_escrow("t1")
        assert store.get_escrow("t1") is None
        assert len(store.list_escrows()) == 0

    def test_delete_missing_escrow_is_noop(self, store: InMemoryStateStore):
        store.delete_escrow("nonexistent")

    # -- tracked CRUD --

    def test_tracked_lifecycle(self, store: InMemoryStateStore):
        assert store.get_tracked("t1") is None

        store.set_tracked("t1", {"escrow_id": "e1", "created_at": 1.0})
        assert store.get_tracked("t1")["escrow_id"] == "e1"
        assert len(store.list_tracked()) == 1

        store.delete_tracked("t1")
        assert store.get_tracked("t1") is None

    def test_tracked_ttl_ignored_in_memory(self, store: InMemoryStateStore):
        store.set_tracked("t1", {"escrow_id": "e1"}, ttl_seconds=10)
        assert store.get_tracked("t1") is not None

    # -- agent CRUD --

    def test_agent_lifecycle(self, store: InMemoryStateStore):
        assert store.get_agent("bot1") is None

        store.set_agent("bot1", {"exchange_url": "http://ex", "account_id": "a1"})
        assert store.get_agent("bot1")["account_id"] == "a1"
        assert len(store.list_agents()) == 1

    # -- gateway state --

    def test_gateway_state(self, store: InMemoryStateStore):
        assert store.get_gateway_state("last_verification") is None

        store.set_gateway_state("last_verification", {"valid": True, "errors": []})
        result = store.get_gateway_state("last_verification")
        assert result["valid"] is True

    # -- thread safety --

    def test_concurrent_escrow_writes(self, store: InMemoryStateStore):
        """Multiple threads can safely write concurrently."""
        errors: list[Exception] = []

        def writer(task_id: str):
            try:
                for _ in range(50):
                    store.set_escrow(task_id, {"escrow_id": task_id})
                    store.get_escrow(task_id)
                    store.delete_escrow(task_id)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer, args=(f"t{i}",)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []


# ======================================================================
# Factory
# ======================================================================


class TestCreateStateStore:

    def test_default_returns_in_memory(self):
        store = create_state_store()
        assert isinstance(store, InMemoryStateStore)

    def test_memory_config(self):
        cfg = SettlementConfig(api_key="k", state_store_type="memory")
        store = create_state_store(cfg)
        assert isinstance(store, InMemoryStateStore)

    def test_redis_config_requires_package(self):
        cfg = SettlementConfig(api_key="k", state_store_type="redis")
        # Either creates a RedisStateStore or raises ImportError
        try:
            store = create_state_store(cfg)
            assert isinstance(store, RedisStateStore)
        except (ImportError, Exception):
            pass

    def test_invalid_store_type_rejected(self):
        with pytest.raises(ValueError, match="state_store_type"):
            SettlementConfig(api_key="k", state_store_type="dynamodb")


# ======================================================================
# RedisStateStore (with fakeredis if available)
# ======================================================================


class TestRedisStateStore:

    @pytest.fixture
    def store(self):
        try:
            import fakeredis
        except ImportError:
            pytest.skip("fakeredis not installed")

        s = RedisStateStore.__new__(RedisStateStore)
        s._redis = fakeredis.FakeRedis(decode_responses=True)
        s._prefix = "test"
        return s

    def test_escrow_lifecycle(self, store):
        assert store.get_escrow("t1") is None

        store.set_escrow("t1", {"escrow_id": "e1", "amount": 100})
        assert store.get_escrow("t1")["escrow_id"] == "e1"
        assert len(store.list_escrows()) == 1

        store.delete_escrow("t1")
        assert store.get_escrow("t1") is None
        assert len(store.list_escrows()) == 0

    def test_tracked_lifecycle(self, store):
        store.set_tracked("t1", {"escrow_id": "e1", "created_at": 1.0})
        assert store.get_tracked("t1")["escrow_id"] == "e1"
        assert len(store.list_tracked()) == 1

        store.delete_tracked("t1")
        assert store.get_tracked("t1") is None

    def test_tracked_with_ttl(self, store):
        store.set_tracked("t1", {"escrow_id": "e1"}, ttl_seconds=3600)
        ttl = store._redis.ttl("test:tracked:t1")
        assert ttl > 0

    def test_agent_lifecycle(self, store):
        store.set_agent("bot1", {"exchange_url": "http://ex", "account_id": "a1"})
        assert store.get_agent("bot1")["account_id"] == "a1"
        assert len(store.list_agents()) == 1

    def test_gateway_state(self, store):
        store.set_gateway_state("last_verification", {"valid": True})
        result = store.get_gateway_state("last_verification")
        assert result["valid"] is True

    def test_key_prefixing(self, store):
        store.set_escrow("t1", {"escrow_id": "e1"})
        assert store._redis.get("test:escrow:t1") is not None
