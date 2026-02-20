"""
provider.py — Settlement-aware agent exposure for ADK.

Wraps Google ADK's `to_a2a()` to add settlement capabilities:
  - Advertises settlement extension in the AgentCard
  - Verifies escrow before executing tasks
  - Updates settlement status in task metadata

Usage:
    from google.adk.agents import Agent
    from adk_a2a_settlement import SettlementConfig, to_settled_a2a

    my_agent = Agent(name="analyst", model="gemini-2.5-flash", ...)

    app = to_settled_a2a(
        agent=my_agent,
        config=SettlementConfig(),
        pricing={"analysis": {"baseTokens": 100}},
    )
    # Run with: uvicorn provider:app --port 8001
"""

from __future__ import annotations

import logging
from typing import Any

from a2a_settlement.agentcard import build_settlement_extension
from a2a_settlement.client import SettlementExchangeClient
from a2a_settlement.metadata import get_settlement_block

from .config import SettlementConfig
from .errors import SettlementError, SettlementErrorCode, classify_exchange_error

logger = logging.getLogger("adk_a2a_settlement.provider")


def to_settled_a2a(
    agent: Any,
    *,
    config: SettlementConfig | None = None,
    account_id: str | None = None,
    pricing: dict[str, Any] | None = None,
    reputation: float | None = None,
    availability: float | None = None,
    required: bool = True,
    port: int = 8001,
    **to_a2a_kwargs: Any,
) -> Any:
    """
    Wrap an ADK Agent as a settlement-aware A2A server.

    This is the provider-side equivalent of ``to_a2a(root_agent)``.
    It auto-generates the settlement extension in the AgentCard and
    returns a FastAPI/Starlette app you can serve with uvicorn.

    Args:
        agent:         The ADK Agent to expose.
        config:        Settlement configuration (or from env vars).
        account_id:    Your exchange account ID. If None, registers at startup.
        pricing:       Pricing dict keyed by skill ID.
        reputation:    Reputation score to advertise (0.0–1.0).
        availability:  Availability score to advertise (0.0–1.0).
        required:      Whether settlement is required to use this agent.
        port:          Port for the A2A server (default 8001).
        **to_a2a_kwargs: Extra kwargs passed to ADK's to_a2a().

    Returns:
        A FastAPI/Starlette application ready to serve via uvicorn.
    """
    from google.adk.a2a.utils.agent_to_a2a import to_a2a

    cfg = config or SettlementConfig()
    exchange = SettlementExchangeClient(base_url=cfg.exchange_url, api_key=cfg.api_key)

    # Auto-register if no account_id provided
    resolved_account_id = account_id
    if not resolved_account_id:
        try:
            reg = exchange.register_account(
                bot_name=agent.name,
                developer_id=f"adk-{agent.name}",
                developer_name=agent.name,
                contact_email=f"{agent.name}@adk-a2a-settlement.dev",
                description=getattr(agent, "description", None) or f"ADK agent: {agent.name}",
                skills=_extract_skills(agent),
            )
            resolved_account_id = reg["account"]["id"]
            logger.info("Agent %s registered with exchange: %s", agent.name, resolved_account_id)
        except Exception as exc:
            logger.warning("Failed to register agent %s: %s", agent.name, exc)
            resolved_account_id = "unregistered"

    # Build the settlement extension for the AgentCard
    settlement_ext = build_settlement_extension(
        exchange_urls=f"{cfg.exchange_url}/v1",
        account_ids=resolved_account_id,
        pricing=pricing,
        reputation=reputation,
        availability=availability,
        required=required,
    )

    # Store settlement context on the agent for callbacks to access
    agent._settlement_config = cfg
    agent._settlement_exchange = exchange
    agent._settlement_account_id = resolved_account_id
    agent._settlement_extension = settlement_ext

    # Call ADK's to_a2a() to get the base app
    a2a_app = to_a2a(agent, **to_a2a_kwargs)

    # Attach settlement extension metadata to the agent card endpoint
    _patch_agent_card(a2a_app, settlement_ext)

    logger.info(
        "Settlement-aware A2A server ready: agent=%s account=%s exchange=%s",
        agent.name, resolved_account_id, cfg.exchange_url,
    )

    return a2a_app


def verify_escrow(
    agent: Any,
    message: Any,
    *,
    raise_on_error: bool = False,
) -> dict[str, Any] | None:
    """
    Verify that an incoming A2A message has a valid escrow.

    Call this in your agent's execution logic to check settlement
    before doing work. Returns the escrow detail dict, or None when
    ``raise_on_error=False`` (the default, for backward compatibility).

    When ``raise_on_error=True``, raises a ``SettlementError`` with a
    JSON-RPC code that client orchestrators can match programmatically.

    Usage in an ADK agent tool or before_model_callback::

        escrow = verify_escrow(agent, context.message, raise_on_error=True)
    """
    se_block = get_settlement_block(message)
    if not se_block:
        if raise_on_error:
            raise SettlementError(
                SettlementErrorCode.ESCROW_NOT_FOUND,
                "Message contains no settlement metadata",
                data={"escrow_id": None},
            )
        return None

    escrow_id = se_block.get("escrowId")
    if not escrow_id:
        if raise_on_error:
            raise SettlementError(
                SettlementErrorCode.ESCROW_NOT_FOUND,
                "Settlement metadata present but missing escrowId",
                data={"settlement_block": se_block},
            )
        return None

    exchange: SettlementExchangeClient | None = getattr(agent, "_settlement_exchange", None)
    account_id: str | None = getattr(agent, "_settlement_account_id", None)

    if not exchange:
        logger.warning("No exchange client on agent %s", getattr(agent, "name", "?"))
        if raise_on_error:
            raise SettlementError(
                SettlementErrorCode.SETTLEMENT_NOT_ADVERTISED,
                data={"agent": getattr(agent, "name", "?")},
            )
        return None

    try:
        escrow = exchange.get_escrow(escrow_id=escrow_id)
    except Exception as exc:
        logger.warning("Failed to fetch escrow %s: %s", escrow_id, exc)
        if raise_on_error:
            code = classify_exchange_error(exc)
            raise SettlementError(
                code,
                f"Exchange returned an error for escrow {escrow_id}",
                data={"escrow_id": escrow_id, "detail": str(exc)},
            ) from exc
        return None

    status = escrow.get("status")

    if status in ("released", "refunded"):
        logger.warning("Escrow %s already settled (status=%s)", escrow_id, status)
        if raise_on_error:
            raise SettlementError(
                SettlementErrorCode.ESCROW_ALREADY_SETTLED,
                data={"escrow_id": escrow_id, "status": status},
            )
        return None

    if status == "expired":
        logger.warning("Escrow %s has expired", escrow_id)
        if raise_on_error:
            raise SettlementError(
                SettlementErrorCode.ESCROW_EXPIRED,
                data={"escrow_id": escrow_id},
            )
        return None

    if status != "held":
        logger.warning("Escrow %s is not held (status=%s)", escrow_id, status)
        if raise_on_error:
            raise SettlementError(
                SettlementErrorCode.PAYMENT_PENDING,
                f"Escrow {escrow_id} status is '{status}', expected 'held'",
                data={"escrow_id": escrow_id, "status": status},
            )
        return None

    if account_id and escrow.get("provider_id") != account_id:
        logger.warning(
            "Escrow %s provider mismatch: expected=%s actual=%s",
            escrow_id, account_id, escrow.get("provider_id"),
        )
        if raise_on_error:
            raise SettlementError(
                SettlementErrorCode.PROVIDER_MISMATCH,
                data={
                    "escrow_id": escrow_id,
                    "expected_provider": account_id,
                    "actual_provider": escrow.get("provider_id"),
                },
            )
        return None

    logger.info("Escrow %s verified: amount=%s", escrow_id, escrow.get("amount"))
    return escrow


def _extract_skills(agent: Any) -> list[str]:
    """Extract skill names from an ADK agent's tools and sub_agents."""
    skills: list[str] = []
    for tool in getattr(agent, "tools", []) or []:
        name = getattr(tool, "name", None) or getattr(tool, "__name__", None)
        if name:
            skills.append(name)
    for sub in getattr(agent, "sub_agents", []) or []:
        name = getattr(sub, "name", None)
        if name:
            skills.append(name)
    return skills


def _patch_agent_card(a2a_app: Any, settlement_ext: dict[str, Any]) -> None:
    """
    Patch the A2A app's agent card endpoint to include the settlement extension.

    The to_a2a() function auto-generates the agent card. We inject the
    settlement extension into the capabilities.extensions array.
    """
    # The to_a2a() app stores the agent card; we patch it via middleware
    # or by monkey-patching the card generation. For now, store it as
    # app state so examples can access it.
    if hasattr(a2a_app, "state"):
        a2a_app.state.settlement_extension = settlement_ext
    logger.debug("Settlement extension attached to A2A app state")
