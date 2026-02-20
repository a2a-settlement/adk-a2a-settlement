"""
state.py — Pluggable state store for settlement session persistence.

Provides an abstract interface with two implementations:

- ``InMemoryStateStore`` — dict-backed, suitable for local development.
- ``RedisStateStore`` — Redis-backed, suitable for production on Digital
  Ocean or any multi-worker deployment where session state must survive
  restarts.

Usage::

    from adk_a2a_settlement.state import create_state_store
    from adk_a2a_settlement.config import SettlementConfig

    store = create_state_store(SettlementConfig())
"""

from __future__ import annotations

import json
import logging
import threading
from abc import ABC, abstractmethod
from typing import Any

logger = logging.getLogger("adk_a2a_settlement.state")


class AbstractStateStore(ABC):
    """
    Interface for settlement state persistence.

    All values are plain dicts (JSON-serializable).  Implementations
    handle serialization and concurrency internally.
    """

    # -- active escrows (keyed by task_id) --

    @abstractmethod
    def get_escrow(self, task_id: str) -> dict[str, Any] | None: ...

    @abstractmethod
    def set_escrow(self, task_id: str, escrow: dict[str, Any]) -> None: ...

    @abstractmethod
    def delete_escrow(self, task_id: str) -> None: ...

    @abstractmethod
    def list_escrows(self) -> dict[str, dict[str, Any]]: ...

    # -- tracked escrows for TTL watchdog (keyed by task_id) --

    @abstractmethod
    def get_tracked(self, task_id: str) -> dict[str, Any] | None: ...

    @abstractmethod
    def set_tracked(
        self, task_id: str, tracked: dict[str, Any], *, ttl_seconds: int = 0
    ) -> None: ...

    @abstractmethod
    def delete_tracked(self, task_id: str) -> None: ...

    @abstractmethod
    def list_tracked(self) -> dict[str, dict[str, Any]]: ...

    # -- registered agents (keyed by agent name) --

    @abstractmethod
    def get_agent(self, name: str) -> dict[str, Any] | None: ...

    @abstractmethod
    def set_agent(self, name: str, info: dict[str, Any]) -> None: ...

    @abstractmethod
    def list_agents(self) -> dict[str, dict[str, Any]]: ...

    # -- gateway state (arbitrary key/value) --

    @abstractmethod
    def get_gateway_state(self, key: str) -> Any: ...

    @abstractmethod
    def set_gateway_state(self, key: str, value: Any) -> None: ...


# ======================================================================
# In-memory implementation (local dev / single-process)
# ======================================================================


class InMemoryStateStore(AbstractStateStore):
    """Thread-safe, dict-backed state store for single-process deployments."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._escrows: dict[str, dict[str, Any]] = {}
        self._tracked: dict[str, dict[str, Any]] = {}
        self._agents: dict[str, dict[str, Any]] = {}
        self._gateway: dict[str, Any] = {}

    # -- escrows --

    def get_escrow(self, task_id: str) -> dict[str, Any] | None:
        with self._lock:
            return self._escrows.get(task_id)

    def set_escrow(self, task_id: str, escrow: dict[str, Any]) -> None:
        with self._lock:
            self._escrows[task_id] = escrow

    def delete_escrow(self, task_id: str) -> None:
        with self._lock:
            self._escrows.pop(task_id, None)

    def list_escrows(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            return dict(self._escrows)

    # -- tracked --

    def get_tracked(self, task_id: str) -> dict[str, Any] | None:
        with self._lock:
            return self._tracked.get(task_id)

    def set_tracked(
        self, task_id: str, tracked: dict[str, Any], *, ttl_seconds: int = 0
    ) -> None:
        with self._lock:
            self._tracked[task_id] = tracked

    def delete_tracked(self, task_id: str) -> None:
        with self._lock:
            self._tracked.pop(task_id, None)

    def list_tracked(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            return dict(self._tracked)

    # -- agents --

    def get_agent(self, name: str) -> dict[str, Any] | None:
        with self._lock:
            return self._agents.get(name)

    def set_agent(self, name: str, info: dict[str, Any]) -> None:
        with self._lock:
            self._agents[name] = info

    def list_agents(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            return dict(self._agents)

    # -- gateway --

    def get_gateway_state(self, key: str) -> Any:
        with self._lock:
            return self._gateway.get(key)

    def set_gateway_state(self, key: str, value: Any) -> None:
        with self._lock:
            self._gateway[key] = value


# ======================================================================
# Redis implementation (production / multi-worker)
# ======================================================================


class RedisStateStore(AbstractStateStore):
    """
    Redis-backed state store using ``redis-py``.

    Keys are namespaced under *prefix* to allow multiple deployments
    on the same Redis instance.  All values are JSON-serialized.
    Tracked escrows support automatic TTL expiry via Redis ``EXPIRE``.
    """

    def __init__(self, redis_url: str = "redis://localhost:6379/0", prefix: str = "a2ase") -> None:
        try:
            import redis
        except ImportError:
            raise ImportError(
                "redis package is required for RedisStateStore. "
                "Install it with: pip install adk-a2a-settlement[redis]"
            )

        self._redis = redis.Redis.from_url(redis_url, decode_responses=True)
        self._prefix = prefix
        logger.info("RedisStateStore connected: url=%s prefix=%s", redis_url, prefix)

    def _key(self, namespace: str, item_id: str) -> str:
        return f"{self._prefix}:{namespace}:{item_id}"

    def _scan_namespace(self, namespace: str) -> dict[str, dict[str, Any]]:
        pattern = f"{self._prefix}:{namespace}:*"
        prefix_len = len(f"{self._prefix}:{namespace}:")
        result: dict[str, dict[str, Any]] = {}
        for key in self._redis.scan_iter(match=pattern, count=200):
            item_id = key[prefix_len:]
            raw = self._redis.get(key)
            if raw:
                result[item_id] = json.loads(raw)
        return result

    # -- escrows --

    def get_escrow(self, task_id: str) -> dict[str, Any] | None:
        raw = self._redis.get(self._key("escrow", task_id))
        return json.loads(raw) if raw else None

    def set_escrow(self, task_id: str, escrow: dict[str, Any]) -> None:
        self._redis.set(self._key("escrow", task_id), json.dumps(escrow))

    def delete_escrow(self, task_id: str) -> None:
        self._redis.delete(self._key("escrow", task_id))

    def list_escrows(self) -> dict[str, dict[str, Any]]:
        return self._scan_namespace("escrow")

    # -- tracked --

    def get_tracked(self, task_id: str) -> dict[str, Any] | None:
        raw = self._redis.get(self._key("tracked", task_id))
        return json.loads(raw) if raw else None

    def set_tracked(
        self, task_id: str, tracked: dict[str, Any], *, ttl_seconds: int = 0
    ) -> None:
        key = self._key("tracked", task_id)
        self._redis.set(key, json.dumps(tracked))
        if ttl_seconds > 0:
            self._redis.expire(key, ttl_seconds)

    def delete_tracked(self, task_id: str) -> None:
        self._redis.delete(self._key("tracked", task_id))

    def list_tracked(self) -> dict[str, dict[str, Any]]:
        return self._scan_namespace("tracked")

    # -- agents --

    def get_agent(self, name: str) -> dict[str, Any] | None:
        raw = self._redis.get(self._key("agent", name))
        return json.loads(raw) if raw else None

    def set_agent(self, name: str, info: dict[str, Any]) -> None:
        self._redis.set(self._key("agent", name), json.dumps(info))

    def list_agents(self) -> dict[str, dict[str, Any]]:
        return self._scan_namespace("agent")

    # -- gateway --

    def get_gateway_state(self, key: str) -> Any:
        raw = self._redis.get(self._key("gw", key))
        return json.loads(raw) if raw else None

    def set_gateway_state(self, key: str, value: Any) -> None:
        self._redis.set(self._key("gw", key), json.dumps(value))


# ======================================================================
# Factory
# ======================================================================


def create_state_store(config: Any | None = None) -> AbstractStateStore:
    """
    Instantiate the appropriate state store from configuration.

    Reads ``state_store_type`` from the config (defaults to ``"memory"``).
    """
    if config is None:
        return InMemoryStateStore()

    store_type = getattr(config, "state_store_type", "memory")

    if store_type == "redis":
        redis_url = getattr(config, "redis_url", "redis://localhost:6379/0")
        redis_prefix = getattr(config, "redis_prefix", "a2ase")
        return RedisStateStore(redis_url=redis_url, prefix=redis_prefix)

    return InMemoryStateStore()
