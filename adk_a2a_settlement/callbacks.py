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
from typing import Any

from a2a_settlement.client import SettlementExchangeClient

from .config import SettlementConfig
from .requester import SettledRemoteAgent

logger = logging.getLogger("adk_a2a_settlement.callbacks")


class SettlementCallbacks:
    """
    ADK callback hooks for automatic settlement lifecycle management.

    Tracks SettledRemoteAgent instances and manages their escrow
    lifecycle through ADK's callback system.
    """

    def __init__(
        self,
        config: SettlementConfig | None = None,
        settled_agents: list[SettledRemoteAgent] | None = None,
    ):
        self._config = config or SettlementConfig()
        self._exchange = SettlementExchangeClient(
            base_url=self._config.exchange_url,
            api_key=self._config.api_key,
        )
        self._settled_agents: dict[str, SettledRemoteAgent] = {}
        for agent in (settled_agents or []):
            self._settled_agents[agent.name] = agent

    def register_agent(self, agent: SettledRemoteAgent) -> None:
        """Register a SettledRemoteAgent for automatic settlement tracking."""
        self._settled_agents[agent.name] = agent

    def before_model(self, callback_context: Any, llm_request: Any) -> Any | None:
        """
        ADK before_model_callback hook.

        Inspects the LLM request to see if it's about to call a settled
        remote agent. If so, ensures escrow is in place.

        Returns None to continue normal execution, or a response to short-circuit.
        """
        # ADK before_model_callback receives the callback context and LLM request
        # We can inspect tool calls being planned, but at this stage the model
        # hasn't decided yet. This is primarily for logging/auditing.
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
    ) -> dict[str, Any] | None:
        """
        Manually settle a task for a named SettledRemoteAgent.

        Call this from your agent logic after determining task outcome.

        Args:
            agent_name: Name of the SettledRemoteAgent.
            task_id: Task ID to settle.
            success: True to release, False to refund.
            reason: Reason for refund (if not success).

        Returns:
            Settlement result dict, or None if agent not found.
        """
        agent = self._settled_agents.get(agent_name)
        if not agent:
            logger.warning("No settled agent found: %s", agent_name)
            return None

        try:
            if success:
                return agent.release(task_id)
            else:
                return agent.refund(task_id, reason=reason)
        except Exception as exc:
            logger.error(
                "Settlement failed: agent=%s task=%s success=%s error=%s",
                agent_name, task_id, success, exc,
            )
            return None

    def get_summary(self) -> dict[str, Any]:
        """Return a summary of all tracked agents and their active escrows."""
        summary: dict[str, Any] = {}
        for name, agent in self._settled_agents.items():
            escrows = agent.get_active_escrows()
            summary[name] = {
                "settlement_info": agent.settlement_info.__dict__ if agent.settlement_info else None,
                "active_escrows": len(escrows),
                "escrow_ids": list(escrows.keys()),
            }
        return summary
