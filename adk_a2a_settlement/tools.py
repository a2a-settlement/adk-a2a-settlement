"""
tools.py — ADK function tools for A2A Settlement operations.

These tools can be added to any ADK Agent's tool list, enabling the
agent to perform settlement operations during task execution.

Usage:
    from google.adk.agents import Agent
    from adk_a2a_settlement.tools import create_settlement_tools

    tools = create_settlement_tools(config=SettlementConfig())

    agent = Agent(
        name="orchestrator",
        model="gemini-2.5-flash",
        instruction="You manage tasks and payments between agents...",
        tools=tools,
    )
"""

from __future__ import annotations

import logging

from a2a_settlement.client import SettlementExchangeClient

from .config import SettlementConfig
from .errors import SettlementError, classify_exchange_error

logger = logging.getLogger("adk_a2a_settlement.tools")


def _format_tool_error(exc: Exception) -> str:
    """Format an exception as a tool-friendly string.

    For SettlementErrors the JSON-RPC code is included so downstream
    orchestrators can still parse it even when the LLM is in the loop.
    """
    if isinstance(exc, SettlementError):
        parts = [f"[{exc.code.name} ({int(exc.code)})] {exc.message}"]
        if exc.data:
            parts.append(f"  Details: {exc.data}")
        return "\n".join(parts)
    return str(exc)


def create_settlement_tools(
    config: SettlementConfig | None = None,
) -> list:
    """
    Create a list of ADK-compatible function tools for settlement.

    Returns plain functions that ADK can use as tools (ADK wraps
    them automatically via its function tool mechanism).
    """
    cfg = config or SettlementConfig()
    exchange = SettlementExchangeClient(base_url=cfg.exchange_url, api_key=cfg.api_key)

    def check_balance() -> str:
        """Check your current token balance on the settlement exchange.

        Returns a summary of available balance, held in escrow, and totals.
        """
        try:
            bal = exchange.get_balance()
            return (
                f"Balance for {bal.get('bot_name', 'account')}:\n"
                f"  Available: {bal.get('available', 0)} tokens\n"
                f"  Held in escrow: {bal.get('held_in_escrow', 0)} tokens\n"
                f"  Total earned: {bal.get('total_earned', 0)} tokens\n"
                f"  Total spent: {bal.get('total_spent', 0)} tokens\n"
                f"  Reputation: {bal.get('reputation', 0.5)}"
            )
        except Exception as exc:
            return f"Failed to check balance: {_format_tool_error(exc)}"

    def create_escrow(
        provider_id: str,
        amount: int,
        task_id: str,
        task_type: str = "",
        ttl_minutes: int = 60,
        required_attestation_level: str = "",
    ) -> str:
        """Create an escrow to hold tokens for a task before sending it to a provider agent.

        Args:
            provider_id: The provider agent's account ID on the exchange.
            amount: Number of tokens to escrow.
            task_id: Unique identifier for the task.
            task_type: Type of task (e.g., "sentiment-analysis").
            ttl_minutes: Time-to-live in minutes before auto-expiry.
            required_attestation_level: Provenance tier the provider must meet:
                "self_declared", "signed", or "verifiable". Empty for no requirement.

        Returns:
            Escrow details including escrow_id needed for release or refund.
        """
        try:
            result = exchange.create_escrow(
                provider_id=provider_id,
                amount=amount,
                task_id=task_id,
                task_type=task_type or None,
                ttl_minutes=ttl_minutes,
                required_attestation_level=required_attestation_level or None,
            )
            return (
                f"Escrow created successfully:\n"
                f"  Escrow ID: {result.get('escrow_id')}\n"
                f"  Amount: {result.get('amount')} tokens\n"
                f"  Fee: {result.get('fee_amount')} tokens\n"
                f"  Status: {result.get('status')}\n"
                f"  Expires: {result.get('expires_at')}"
            )
        except Exception as exc:
            code = classify_exchange_error(exc)
            se = SettlementError(
                code,
                data={"provider_id": provider_id, "requested_amount": amount},
            )
            return f"Failed to create escrow: {_format_tool_error(se)}"

    def release_escrow(escrow_id: str) -> str:
        """Release an escrow to pay the provider after successful task completion.

        Args:
            escrow_id: The escrow ID to release.

        Returns:
            Release confirmation with amount paid.
        """
        try:
            result = exchange.release_escrow(escrow_id=escrow_id)
            return (
                f"Escrow released:\n"
                f"  Escrow ID: {result.get('escrow_id')}\n"
                f"  Amount paid: {result.get('amount_paid')} tokens\n"
                f"  Provider: {result.get('provider_id')}"
            )
        except Exception as exc:
            code = classify_exchange_error(exc)
            se = SettlementError(code, data={"escrow_id": escrow_id})
            return f"Failed to release escrow: {_format_tool_error(se)}"

    def refund_escrow(escrow_id: str, reason: str = "") -> str:
        """Refund an escrow to return tokens after task failure.

        Args:
            escrow_id: The escrow ID to refund.
            reason: Optional reason for the refund.

        Returns:
            Refund confirmation with amount returned.
        """
        try:
            result = exchange.refund_escrow(escrow_id=escrow_id, reason=reason or None)
            return (
                f"Escrow refunded:\n"
                f"  Escrow ID: {result.get('escrow_id')}\n"
                f"  Amount returned: {result.get('amount_returned')} tokens\n"
                f"  Requester: {result.get('requester_id')}"
            )
        except Exception as exc:
            code = classify_exchange_error(exc)
            se = SettlementError(code, data={"escrow_id": escrow_id})
            return f"Failed to refund escrow: {_format_tool_error(se)}"

    def dispute_escrow(escrow_id: str, reason: str) -> str:
        """Dispute an escrow when the provider's deliverable is unsatisfactory.

        Args:
            escrow_id: The escrow ID to dispute.
            reason: Explanation of why the deliverable is disputed.

        Returns:
            Dispute confirmation. A mediator will evaluate and resolve.
        """
        try:
            result = exchange.dispute_escrow(escrow_id=escrow_id, reason=reason)
            return (
                f"Escrow disputed:\n"
                f"  Escrow ID: {result.get('escrow_id')}\n"
                f"  Status: {result.get('status')}\n"
                f"  Reason: {result.get('reason')}\n"
                f"  A mediator will evaluate and resolve the dispute."
            )
        except Exception as exc:
            return f"Failed to dispute escrow: {_format_tool_error(exc)}"

    def lookup_agent(skill: str = "") -> str:
        """Look up agents in the exchange directory, optionally filtered by skill.

        Args:
            skill: Optional skill to filter by (e.g., "sentiment-analysis").

        Returns:
            List of available agents with their reputation scores.
        """
        try:
            result = exchange.directory(skill=skill or None, limit=10)
            bots = result.get("bots", [])
            if not bots:
                return "No agents found" + (f" with skill '{skill}'" if skill else "")
            lines = [f"Found {len(bots)} agent(s):"]
            for bot in bots:
                lines.append(
                    f"  - {bot.get('bot_name')} (ID: {bot.get('id')})\n"
                    f"    Reputation: {bot.get('reputation', 0.5)}\n"
                    f"    Skills: {', '.join(bot.get('skills', []))}"
                )
            return "\n".join(lines)
        except Exception as exc:
            return f"Failed to lookup agents: {_format_tool_error(exc)}"

    def get_escrow_status(escrow_id: str) -> str:
        """Check the current status of an escrow.

        Args:
            escrow_id: The escrow ID to check.

        Returns:
            Current escrow details including status and deliverables.
        """
        try:
            esc = exchange.get_escrow(escrow_id=escrow_id)
            lines = [
                f"Escrow {esc.get('id')}:",
                f"  Status: {esc.get('status')}",
                f"  Amount: {esc.get('amount')} tokens",
                f"  Requester: {esc.get('requester_id')}",
                f"  Provider: {esc.get('provider_id')}",
                f"  Task: {esc.get('task_id', 'N/A')}",
                f"  Expires: {esc.get('expires_at')}",
            ]
            if esc.get("dispute_reason"):
                lines.append(f"  Dispute: {esc['dispute_reason']}")
            deliverables = esc.get("deliverables") or []
            if deliverables:
                lines.append(f"  Deliverables: {len(deliverables)}")
                for i, d in enumerate(deliverables, 1):
                    lines.append(f"    {i}. {d.get('description', 'N/A')}")
            return "\n".join(lines)
        except Exception as exc:
            code = classify_exchange_error(exc)
            se = SettlementError(code, data={"escrow_id": escrow_id})
            return f"Failed to get escrow status: {_format_tool_error(se)}"

    def deliver_escrow(
        escrow_id: str,
        content: str,
        source_type: str = "",
        attestation_level: str = "",
    ) -> str:
        """Submit a deliverable against a held escrow (provider-side).

        Call this after completing work to record the deliverable and optional
        provenance on the exchange. The AI Mediator uses provenance for
        verification during dispute resolution.

        Args:
            escrow_id: The escrow to deliver against.
            content: The deliverable content.
            source_type: How data was obtained: "api", "database", "web",
                "generated", or "hybrid". Empty to skip provenance.
            attestation_level: Trust tier: "self_declared", "signed", or
                "verifiable". Empty defaults to "self_declared" if source_type
                is provided.

        Returns:
            Delivery confirmation with escrow status.
        """
        try:
            provenance = None
            if source_type:
                provenance = {
                    "source_type": source_type,
                    "source_refs": [],
                    "attestation_level": attestation_level or "self_declared",
                }
            result = exchange.deliver(
                escrow_id=escrow_id,
                content=content,
                provenance=provenance,
            )
            return (
                f"Deliverable submitted:\n"
                f"  Escrow ID: {result.get('escrow_id')}\n"
                f"  Status: {result.get('status')}\n"
                f"  Delivered at: {result.get('delivered_at')}"
            )
        except Exception as exc:
            return f"Failed to deliver: {_format_tool_error(exc)}"

    return [
        check_balance,
        create_escrow,
        deliver_escrow,
        release_escrow,
        refund_escrow,
        dispute_escrow,
        lookup_agent,
        get_escrow_status,
    ]
