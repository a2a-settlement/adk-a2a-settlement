"""
callbacks.py — ADK callback hooks for automatic settlement.

ADK Agents support `before_model_callback` and `after_model_callback`
hooks. These callbacks automate escrow creation and settlement around
model invocations when calling settled remote agents.

Usage:
    from google.adk.agents import Agent
    from adk_a2a_settlement.callbacks import SettlementCallbacks

    callbacks = SettlementCallbacks(config=SettlementConfig())

    agent = Agent(
        name="orchestrator",
        model="gemini-2.5-flash",
        before_model_callback=callbacks.before_model,
        after_model_callback=callbacks.after_model,
        ...
    )
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from a2a_settlement.client import SettlementExchangeClient

from .config import SettlementConfig
from .errors import SettlementError, SettlementErrorCode
from .requester import SettledRemoteAgent
from .state import AbstractStateStore, create_state_store

logger = logging.getLogger("adk_a2a_settlement.callbacks")


class SettlementCallbacks:
    """
    ADK callback hooks for automatic settlement lifecycle management.

    Tracks SettledRemoteAgent instances and manages their escrow
    lifecycle through ADK's callback system.

    When ``settlement_ttl_minutes > 0`` in the config, every registered
    ``SettledRemoteAgent`` will have its own ``EscrowTTLWatchdog`` that
    auto-refunds escrows exceeding the TTL. The ``on_escrow_expired``
    callback fires whenever a watchdog expires an escrow, giving
    higher-level orchestrators a chance to cancel in-flight work.
    """

    def __init__(
        self,
        config: SettlementConfig | None = None,
        settled_agents: list[SettledRemoteAgent] | None = None,
        on_escrow_expired: Callable[[str, str, str, dict[str, Any]], None] | None = None,
        *,
        state_store: AbstractStateStore | None = None,
    ):
        self._config = config or SettlementConfig()
        self._exchange = SettlementExchangeClient(
            base_url=self._config.exchange_url,
            api_key=self._config.api_key,
        )
        self._on_escrow_expired = on_escrow_expired
        self._store = state_store or create_state_store(self._config)

        # Live agent references (not serializable to Redis)
        self._settled_agents: dict[str, SettledRemoteAgent] = {}
        for agent in (settled_agents or []):
            self._settled_agents[agent.name] = agent
            if agent.settlement_info:
                self._store.set_agent(agent.name, agent.settlement_info.__dict__)

    def register_agent(self, agent: SettledRemoteAgent) -> None:
        """Register a SettledRemoteAgent for automatic settlement tracking."""
        self._settled_agents[agent.name] = agent
        if agent.settlement_info:
            self._store.set_agent(agent.name, agent.settlement_info.__dict__)

    def before_model(self, callback_context: Any, llm_request: Any) -> Any | None:
        """
        ADK before_model_callback hook.

        Inspects the LLM request to see if it's about to call a settled
        remote agent. If so, ensures escrow is in place.

        Returns None to continue normal execution, or a response to short-circuit.
        """
        logger.debug("Before model callback invoked")
        return None

    def after_model(self, callback_context: Any, llm_response: Any) -> Any | None:
        """
        ADK after_model_callback hook.

        After the model responds, checks if any settled remote agent
        tasks completed or failed, and settles accordingly.

        Returns None to continue normal execution.
        """
        logger.debug("After model callback invoked")
        return None

    def settle_task(
        self,
        agent_name: str,
        task_id: str,
        success: bool,
        reason: str = "",
    ) -> dict[str, Any]:
        """
        Manually settle a task for a named SettledRemoteAgent.

        Call this from your agent logic after determining task outcome.

        Args:
            agent_name: Name of the SettledRemoteAgent.
            task_id: Task ID to settle.
            success: True to release, False to refund.
            reason: Reason for refund (if not success).

        Returns:
            Settlement result dict.

        Raises:
            SettlementError(ESCROW_NOT_FOUND) when agent or escrow is missing.
        """
        agent = self._settled_agents.get(agent_name)
        if not agent:
            raise SettlementError(
                SettlementErrorCode.ESCROW_NOT_FOUND,
                f"No settled agent registered with name '{agent_name}'",
                data={"agent_name": agent_name, "registered": list(self._settled_agents.keys())},
            )

        if success:
            return agent.release(task_id)
        else:
            return agent.refund(task_id, reason=reason)

    def get_summary(self) -> dict[str, Any]:
        """Return a summary of all tracked agents and their active escrows."""
        summary: dict[str, Any] = {}
        for name, agent in self._settled_agents.items():
            escrows = agent.get_active_escrows()
            watchdog_active = agent.watchdog.is_running if agent.watchdog else False
            summary[name] = {
                "settlement_info": agent.settlement_info.__dict__ if agent.settlement_info else None,
                "active_escrows": len(escrows),
                "escrow_ids": list(escrows.keys()),
                "ttl_watchdog_active": watchdog_active,
            }
        return summary

    def shutdown(self) -> None:
        """Stop all TTL watchdogs. Call this on application shutdown."""
        for agent in self._settled_agents.values():
            agent.shutdown()
        logger.info("All settlement watchdogs stopped")
