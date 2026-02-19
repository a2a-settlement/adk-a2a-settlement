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
from dataclasses import dataclass, field
from typing import Any

import httpx

from a2a_settlement.client import SettlementExchangeClient
from a2a_settlement.metadata import build_settlement_metadata

from .config import SettlementConfig

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


class SettledRemoteAgent:
    """
    A settlement-aware wrapper for ADK's RemoteA2aAgent.

    On construction, reads the settlement extension from the provider's
    AgentCard. Provides methods to create escrow, release, and refund
    around remote agent calls.

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
    ):
        from google.adk.agents.remote_a2a_agent import RemoteA2aAgent

        self.name = name
        self.description = description
        self.agent_card_url = agent_card
        self._config = config or SettlementConfig()

        # Create the underlying RemoteA2aAgent
        self._remote_agent = RemoteA2aAgent(
            name=name,
            description=description,
            agent_card=agent_card,
            timeout=timeout,
            httpx_client=httpx_client,
        )

        # Initialize exchange client
        self._exchange = SettlementExchangeClient(
            base_url=self._config.exchange_url,
            api_key=self._config.api_key,
        )

        # Discover settlement info from agent card
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

        # Track active escrows for this agent
        self._active_escrows: dict[str, dict[str, Any]] = {}

    @property
    def remote_agent(self) -> Any:
        """The underlying ADK RemoteA2aAgent for use as a sub_agent."""
        return self._remote_agent

    @property
    def settlement_info(self) -> SettlementInfo | None:
        """Parsed settlement info from the provider's AgentCard."""
        return self._settlement_info

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
        """
        if not self._settlement_info:
            raise RuntimeError(f"Agent {self.name} does not advertise settlement")

        # Determine amount from pricing or parameter
        if amount is None:
            pricing = self._settlement_info.pricing
            if task_type and task_type in pricing:
                amount = int(pricing[task_type].get("baseTokens", 10))
            else:
                # Use first pricing entry or default
                for _, p in pricing.items():
                    amount = int(p.get("baseTokens", 10))
                    break
                else:
                    amount = 10  # fallback

        escrow = self._exchange.create_escrow(
            provider_id=self._settlement_info.account_id,
            amount=amount,
            task_id=task_id,
            task_type=task_type,
            ttl_minutes=ttl_minutes or self._config.default_ttl_minutes,
            deliverables=deliverables,
        )

        escrow_id = escrow["escrow_id"]
        self._active_escrows[task_id] = escrow

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
        """Release escrow for a completed task."""
        escrow = self._active_escrows.get(task_id)
        if not escrow:
            raise ValueError(f"No active escrow for task {task_id}")

        result = self._exchange.release_escrow(escrow_id=escrow["escrow_id"])
        del self._active_escrows[task_id]

        logger.info("Escrow released: task=%s escrow=%s", task_id, escrow["escrow_id"])
        return result

    def refund(self, task_id: str, reason: str = "") -> dict[str, Any]:
        """Refund escrow for a failed task."""
        escrow = self._active_escrows.get(task_id)
        if not escrow:
            raise ValueError(f"No active escrow for task {task_id}")

        result = self._exchange.refund_escrow(
            escrow_id=escrow["escrow_id"],
            reason=reason[:256] if reason else "Task failed",
        )
        del self._active_escrows[task_id]

        logger.info("Escrow refunded: task=%s escrow=%s reason=%s", task_id, escrow["escrow_id"], reason[:80])
        return result

    def get_active_escrows(self) -> dict[str, dict[str, Any]]:
        """Return all active (unreleased/unrefunded) escrows."""
        return dict(self._active_escrows)
