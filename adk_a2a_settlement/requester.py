"""
requester.py — Settlement-aware remote agent consumption for ADK.

Wraps Google ADK's `RemoteA2aAgent` to add automatic settlement:
  - Reads settlement extension from the provider's AgentCard
  - Creates escrow before sending tasks
  - Releases escrow on success, refunds on failure
  - Attaches settlement metadata to A2A messages

Usage:
    from adk_a2a_settlement import SettledRemoteAgent, SettlementConfig

    analyst = SettledRemoteAgent(
        name="analyst",
        description="Remote sentiment analysis agent",
        agent_card="http://localhost:8001/.well-known/agent.json",
        config=SettlementConfig(),
    )

    root = Agent(
        name="orchestrator",
        model="gemini-2.5-flash",
        sub_agents=[analyst],
        ...
    )
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import httpx

from a2a_settlement.client import SettlementExchangeClient
from a2a_settlement.metadata import build_settlement_metadata

from .config import SettlementConfig
from .errors import SettlementError, SettlementErrorCode, classify_exchange_error
from .state import AbstractStateStore, create_state_store

logger = logging.getLogger("adk_a2a_settlement.requester")

# Extension URI from the A2A-SE spec
A2A_SE_EXTENSION_URI = "https://a2a-settlement.org/extensions/settlement/v1"


@dataclass
class SettlementInfo:
    """Parsed settlement info from a provider's AgentCard."""

    exchange_url: str
    account_id: str
    pricing: dict[str, Any] = field(default_factory=dict)
    reputation: float = 0.5
    availability: float = 1.0
    required: bool = False


def discover_settlement(agent_card_url: str, *, timeout: float = 10.0) -> SettlementInfo | None:
    """
    Fetch an agent card and extract settlement extension info.

    Returns SettlementInfo if the agent advertises A2A-SE, None otherwise.
    """
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.get(agent_card_url)
            resp.raise_for_status()
            card = resp.json()
    except Exception as exc:
        logger.warning("Failed to fetch agent card from %s: %s", agent_card_url, exc)
        return None

    extensions = (
        card.get("capabilities", {}).get("extensions")
        or card.get("extensions")
        or []
    )

    for ext in extensions:
        uri = ext.get("uri", "")
        if uri == A2A_SE_EXTENSION_URI:
            params = ext.get("params", {})

            # Handle v0.5 multi-exchange format
            exchange_urls = params.get("exchangeUrls", [])
            preferred = params.get("preferredExchange", "")
            account_ids = params.get("accountIds", {})

            if isinstance(exchange_urls, list) and exchange_urls:
                exchange_url = preferred or exchange_urls[0]
            elif isinstance(exchange_urls, str):
                exchange_url = exchange_urls
            else:
                # Fallback to v0.2 single-exchange format
                exchange_url = params.get("exchangeUrl", "")

            if isinstance(account_ids, dict):
                account_id = account_ids.get(exchange_url, "")
            else:
                account_id = str(account_ids) if account_ids else ""

            return SettlementInfo(
                exchange_url=exchange_url,
                account_id=account_id,
                pricing=params.get("pricing", {}),
                reputation=float(params.get("reputation", 0.5)),
                availability=float(params.get("availability", 1.0)),
                required=ext.get("required", False),
            )

    return None


class EscrowTTLWatchdog:
    """
    Background watchdog that auto-refunds escrows exceeding the local
    settlement TTL, freeing worker threads blocked on payment wait-states.

    The watchdog runs a single daemon thread that wakes periodically
    (every ``poll_interval_seconds``) and checks for expired entries.
    Expired escrows are refunded via the exchange and an optional
    ``on_expired`` callback is fired so higher-level orchestrators can
    react (e.g. cancel an ADK task).
    """

    def __init__(
        self,
        exchange: SettlementExchangeClient,
        ttl_minutes: int = 15,
        poll_interval_seconds: float = 30.0,
        on_expired: Callable[[str, str, dict[str, Any]], None] | None = None,
        *,
        state_store: AbstractStateStore | None = None,
    ):
        self._exchange = exchange
        self._ttl_seconds = ttl_minutes * 60
        self._poll_interval = poll_interval_seconds
        self._on_expired = on_expired
        self._store = state_store

        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Start the background watchdog thread (idempotent)."""
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run, name="escrow-ttl-watchdog", daemon=True
        )
        self._thread.start()
        logger.debug("TTL watchdog started (ttl=%ds, poll=%ds)", self._ttl_seconds, self._poll_interval)

    def stop(self) -> None:
        """Signal the watchdog to stop and wait for it to finish."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=self._poll_interval + 2)
            self._thread = None
        logger.debug("TTL watchdog stopped")

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def track(self, task_id: str, escrow: dict[str, Any]) -> None:
        """Register an escrow for TTL monitoring."""
        entry = {
            "task_id": task_id,
            "escrow_id": escrow["escrow_id"],
            "escrow": escrow,
            "created_at": time.monotonic(),
        }
        if self._store:
            self._store.set_tracked(task_id, entry, ttl_seconds=self._ttl_seconds)
        else:
            self._tracked[task_id] = _TrackedEscrow(
                task_id=task_id,
                escrow_id=escrow["escrow_id"],
                escrow=escrow,
                created_at=time.monotonic(),
            )

    def untrack(self, task_id: str) -> None:
        """Remove a task from monitoring (called on normal release/refund)."""
        if self._store:
            self._store.delete_tracked(task_id)
        else:
            self._tracked.pop(task_id, None)

    def tracked_count(self) -> int:
        if self._store:
            return len(self._store.list_tracked())
        return len(self._tracked)

    @property
    def _tracked(self) -> dict[str, _TrackedEscrow]:
        """Fallback in-memory dict used when no state store is provided."""
        if not hasattr(self, "_tracked_dict"):
            self._tracked_dict: dict[str, _TrackedEscrow] = {}
        return self._tracked_dict

    @_tracked.setter
    def _tracked(self, value: dict[str, _TrackedEscrow]) -> None:
        self._tracked_dict = value

    def _run(self) -> None:
        while not self._stop_event.is_set():
            self._sweep()
            self._stop_event.wait(timeout=self._poll_interval)

    def _sweep(self) -> None:
        now = time.monotonic()
        expired: list[tuple[str, str, dict[str, Any]]] = []

        if self._store:
            all_tracked = self._store.list_tracked()
            for tid, entry in all_tracked.items():
                if (now - entry["created_at"]) >= self._ttl_seconds:
                    expired.append((tid, entry["escrow_id"], entry["escrow"]))
                    self._store.delete_tracked(tid)
        else:
            for tid, entry in list(self._tracked.items()):
                if (now - entry.created_at) >= self._ttl_seconds:
                    expired.append((tid, entry.escrow_id, entry.escrow))
                    del self._tracked[tid]

        for task_id, escrow_id, escrow in expired:
            self._expire(task_id, escrow_id, escrow)

    def _expire(self, task_id: str, escrow_id: str, escrow: dict[str, Any]) -> None:
        logger.warning(
            "Settlement TTL exceeded — auto-refunding escrow %s for task %s",
            escrow_id, task_id,
        )
        try:
            self._exchange.refund_escrow(
                escrow_id=escrow_id,
                reason="Settlement TTL exceeded — auto-released by watchdog",
            )
        except Exception as exc:
            logger.error("Watchdog refund failed for %s: %s", escrow_id, exc)

        if self._on_expired:
            try:
                self._on_expired(task_id, escrow_id, escrow)
            except Exception as exc:
                logger.error("on_expired callback failed for %s: %s", task_id, exc)


@dataclass
class _TrackedEscrow:
    task_id: str
    escrow_id: str
    escrow: dict[str, Any]
    created_at: float


class SettledRemoteAgent:
    """
    A settlement-aware wrapper for ADK's RemoteA2aAgent.

    On construction, reads the settlement extension from the provider's
    AgentCard. Provides methods to create escrow, release, and refund
    around remote agent calls.

    When ``settlement_ttl_minutes`` is set (via config or constructor),
    a background ``EscrowTTLWatchdog`` auto-refunds escrows that exceed
    the TTL and frees blocked resources.

    Can be used as a sub_agent in an ADK Agent by accessing the
    inner `.remote_agent` property, which is the real RemoteA2aAgent.
    """

    def __init__(
        self,
        name: str,
        description: str,
        agent_card: str,
        *,
        config: SettlementConfig | None = None,
        timeout: float = 300.0,
        httpx_client: Any | None = None,
        on_escrow_expired: Callable[[str, str, dict[str, Any]], None] | None = None,
        state_store: AbstractStateStore | None = None,
    ):
        from google.adk.agents.remote_a2a_agent import RemoteA2aAgent

        self.name = name
        self.description = description
        self.agent_card_url = agent_card
        self._config = config or SettlementConfig()

        self._store = state_store or create_state_store(self._config)

        self._remote_agent = RemoteA2aAgent(
            name=name,
            description=description,
            agent_card=agent_card,
            timeout=timeout,
            httpx_client=httpx_client,
        )

        self._exchange = SettlementExchangeClient(
            base_url=self._config.exchange_url,
            api_key=self._config.api_key,
        )

        self._settlement_info: SettlementInfo | None = None
        if self._config.auto_escrow:
            self._settlement_info = discover_settlement(agent_card)
            if self._settlement_info:
                logger.info(
                    "Settlement discovered for %s: exchange=%s account=%s pricing=%s",
                    name,
                    self._settlement_info.exchange_url,
                    self._settlement_info.account_id,
                    list(self._settlement_info.pricing.keys()),
                )
            else:
                logger.info("No settlement extension found for %s", name)

        # TTL watchdog
        self._watchdog: EscrowTTLWatchdog | None = None
        ttl = self._config.settlement_ttl_minutes
        if ttl and ttl > 0:
            self._watchdog = EscrowTTLWatchdog(
                exchange=self._exchange,
                ttl_minutes=ttl,
                on_expired=self._on_ttl_expired,
                state_store=self._store,
            )
            self._watchdog.start()

        self._on_escrow_expired = on_escrow_expired

    def _on_ttl_expired(self, task_id: str, escrow_id: str, escrow: dict[str, Any]) -> None:
        """Internal callback when the watchdog expires an escrow."""
        self._store.delete_escrow(task_id)
        logger.warning(
            "Task %s auto-released due to settlement TTL (%d min)",
            task_id, self._config.settlement_ttl_minutes,
        )
        if self._on_escrow_expired:
            self._on_escrow_expired(task_id, escrow_id, escrow)

    @property
    def remote_agent(self) -> Any:
        """The underlying ADK RemoteA2aAgent for use as a sub_agent."""
        return self._remote_agent

    @property
    def settlement_info(self) -> SettlementInfo | None:
        """Parsed settlement info from the provider's AgentCard."""
        return self._settlement_info

    @property
    def watchdog(self) -> EscrowTTLWatchdog | None:
        """The TTL watchdog, if active."""
        return self._watchdog

    def create_escrow(
        self,
        *,
        task_id: str,
        task_type: str | None = None,
        amount: int | None = None,
        ttl_minutes: int | None = None,
        deliverables: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """
        Create an escrow for a task on this remote agent.

        If amount is None, looks up pricing from the agent card.
        Returns the escrow response dict with escrow_id.

        Raises:
            SettlementError(SETTLEMENT_NOT_ADVERTISED) if the agent
                doesn't have settlement capabilities.
            SettlementError(INSUFFICIENT_FUNDS) when the exchange
                rejects the escrow for balance reasons.
            SettlementError(INTERNAL_ERROR) on other exchange errors.
        """
        if not self._settlement_info:
            raise SettlementError(
                SettlementErrorCode.SETTLEMENT_NOT_ADVERTISED,
                data={"agent": self.name},
            )

        if amount is None:
            pricing = self._settlement_info.pricing
            if task_type and task_type in pricing:
                amount = int(pricing[task_type].get("baseTokens", 10))
            else:
                for _, p in pricing.items():
                    amount = int(p.get("baseTokens", 10))
                    break
                else:
                    amount = 10

        try:
            escrow = self._exchange.create_escrow(
                provider_id=self._settlement_info.account_id,
                amount=amount,
                task_id=task_id,
                task_type=task_type,
                ttl_minutes=ttl_minutes or self._config.default_ttl_minutes,
                deliverables=deliverables,
            )
        except Exception as exc:
            code = classify_exchange_error(exc)
            raise SettlementError(
                code,
                data={"agent": self.name, "requested_amount": amount, "detail": str(exc)},
            ) from exc

        escrow_id = escrow["escrow_id"]
        self._store.set_escrow(task_id, escrow)

        if self._watchdog:
            self._watchdog.track(task_id, escrow)

        logger.info(
            "Escrow created: id=%s agent=%s amount=%d task=%s",
            escrow_id, self.name, amount, task_id,
        )

        return escrow

    def build_metadata(self, escrow: dict[str, Any]) -> dict[str, Any]:
        """Build A2A message metadata from an escrow response."""
        return build_settlement_metadata(
            escrow_id=escrow["escrow_id"],
            amount=escrow["amount"],
            fee_amount=escrow["fee_amount"],
            exchange_url=self._settlement_info.exchange_url if self._settlement_info else self._config.exchange_url,
            expires_at=escrow["expires_at"],
        )

    def release(self, task_id: str) -> dict[str, Any]:
        """Release escrow for a completed task.

        Raises:
            SettlementError(ESCROW_NOT_FOUND) if no active escrow for task.
        """
        escrow = self._store.get_escrow(task_id)
        if not escrow:
            raise SettlementError(
                SettlementErrorCode.ESCROW_NOT_FOUND,
                f"No active escrow for task {task_id}",
                data={"task_id": task_id, "agent": self.name},
            )

        result = self._exchange.release_escrow(escrow_id=escrow["escrow_id"])
        self._store.delete_escrow(task_id)
        if self._watchdog:
            self._watchdog.untrack(task_id)

        logger.info("Escrow released: task=%s escrow=%s", task_id, escrow["escrow_id"])
        return result

    def refund(self, task_id: str, reason: str = "") -> dict[str, Any]:
        """Refund escrow for a failed task.

        Raises:
            SettlementError(ESCROW_NOT_FOUND) if no active escrow for task.
        """
        escrow = self._store.get_escrow(task_id)
        if not escrow:
            raise SettlementError(
                SettlementErrorCode.ESCROW_NOT_FOUND,
                f"No active escrow for task {task_id}",
                data={"task_id": task_id, "agent": self.name},
            )

        result = self._exchange.refund_escrow(
            escrow_id=escrow["escrow_id"],
            reason=reason[:256] if reason else "Task failed",
        )
        self._store.delete_escrow(task_id)
        if self._watchdog:
            self._watchdog.untrack(task_id)

        logger.info("Escrow refunded: task=%s escrow=%s reason=%s", task_id, escrow["escrow_id"], reason[:80])
        return result

    def get_active_escrows(self) -> dict[str, dict[str, Any]]:
        """Return all active (unreleased/unrefunded) escrows."""
        return self._store.list_escrows()

    def shutdown(self) -> None:
        """Stop the TTL watchdog. Safe to call multiple times."""
        if self._watchdog:
            self._watchdog.stop()
