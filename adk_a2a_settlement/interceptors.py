"""
interceptors.py — ADK interceptors that extract AP2 mandates.

Provides ``before_model`` and ``after_model`` callbacks for Google ADK
agents.  The before-hook builds an **Intent Mandate** from the user's
request; the after-hook builds a **Cart Mandate** from the LLM's
generated tool calls / structured output.

Usage::

    from adk_a2a_settlement.interceptors import MandateInterceptors

    interceptors = MandateInterceptors()

    agent = Agent(
        name="trader",
        model="gemini-2.5-flash",
        before_model_callback=interceptors.extract_intent,
        after_model_callback=interceptors.extract_cart,
        ...
    )
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from .mandates import CartItem, CartMandate, IntentMandate

logger = logging.getLogger("adk_a2a_settlement.interceptors")


class MandateInterceptors:
    """
    Stateful interceptor pair that accumulates mandates across a session.

    Attributes:
        pending_intent:  The most recent Intent Mandate extracted.
        pending_cart:    The most recent Cart Mandate extracted.
        on_mandates_ready:  Optional callback fired when both are available.
    """

    def __init__(
        self,
        *,
        default_user_id: str = "anonymous",
        on_mandates_ready: Callable[[IntentMandate, CartMandate], Any] | None = None,
    ):
        self._default_user_id = default_user_id
        self.on_mandates_ready = on_mandates_ready
        self.pending_intent: IntentMandate | None = None
        self.pending_cart: CartMandate | None = None

    # ------------------------------------------------------------------
    # before_model — Intent Mandate extraction
    # ------------------------------------------------------------------

    def extract_intent(
        self,
        callback_context: Any,
        llm_request: Any,
    ) -> Any | None:
        """
        ADK ``before_model_callback``.

        Inspects the pending LLM request to derive the user's intent.
        Stores the result in ``self.pending_intent``.
        """
        user_id = _resolve_user_id(callback_context, self._default_user_id)
        session_id = _resolve_session_id(callback_context)

        description = _extract_intent_text(llm_request, callback_context)
        constraints = _extract_constraints(callback_context)
        budget = _extract_budget(callback_context)

        intent = IntentMandate(
            user_id=user_id,
            session_id=session_id,
            intent_description=description,
            constraints=constraints,
            max_budget_tokens=budget,
        )

        self.pending_intent = intent
        logger.info(
            "Intent Mandate extracted: id=%s user=%s budget=%d",
            intent.mandate_id, user_id, budget,
        )
        return None  # continue normal ADK flow

    # ------------------------------------------------------------------
    # after_model — Cart Mandate extraction
    # ------------------------------------------------------------------

    def extract_cart(
        self,
        callback_context: Any,
        llm_response: Any,
    ) -> Any | None:
        """
        ADK ``after_model_callback``.

        Parses the LLM response for tool calls or structured output that
        describe purchases, building a Cart Mandate.
        """
        if not self.pending_intent:
            logger.debug("No pending intent — skipping cart extraction")
            return None

        items = _extract_cart_items(llm_response)
        if not items:
            logger.debug("No cart items found in LLM response")
            return None

        total = sum(i.amount_tokens for i in items)
        cart = CartMandate(
            intent_mandate_id=self.pending_intent.mandate_id,
            items=items,
            total_tokens=total,
        )

        self.pending_cart = cart
        logger.info(
            "Cart Mandate extracted: id=%s items=%d total=%d",
            cart.mandate_id, len(items), total,
        )

        if self.on_mandates_ready:
            self.on_mandates_ready(self.pending_intent, cart)

        return None  # continue normal ADK flow

    # ------------------------------------------------------------------
    # accessors
    # ------------------------------------------------------------------

    def get_mandates(self) -> tuple[IntentMandate | None, CartMandate | None]:
        """Return the current (intent, cart) mandate pair."""
        return self.pending_intent, self.pending_cart

    def clear(self) -> None:
        """Reset accumulated state."""
        self.pending_intent = None
        self.pending_cart = None


# ======================================================================
# Private helpers — extract structured data from ADK objects
# ======================================================================

def _resolve_user_id(ctx: Any, default: str) -> str:
    """Pull user_id from ADK callback context or session state."""
    if hasattr(ctx, "state") and isinstance(ctx.state, dict):
        return str(ctx.state.get("user_id", default))
    if hasattr(ctx, "user_id"):
        return str(ctx.user_id)
    return default


def _resolve_session_id(ctx: Any) -> str:
    if hasattr(ctx, "session") and hasattr(ctx.session, "id"):
        return str(ctx.session.id)
    if hasattr(ctx, "state") and isinstance(ctx.state, dict):
        return str(ctx.state.get("session_id", ""))
    return ""


def _extract_intent_text(llm_request: Any, ctx: Any) -> str:
    """Best-effort extraction of the user's natural-language intent."""
    # ADK LlmRequest may carry contents / messages
    if hasattr(llm_request, "contents"):
        parts: list[str] = []
        for content in (llm_request.contents or []):
            for part in getattr(content, "parts", []):
                text = getattr(part, "text", None)
                if text:
                    parts.append(text)
        if parts:
            return " ".join(parts)

    if hasattr(ctx, "user_content"):
        return str(ctx.user_content)

    return "unspecified intent"


def _extract_constraints(ctx: Any) -> dict[str, Any]:
    """Pull trading constraints from session state or context."""
    if hasattr(ctx, "state") and isinstance(ctx.state, dict):
        return dict(ctx.state.get("constraints", {}))
    return {}


def _extract_budget(ctx: Any) -> int:
    """Pull maximum budget from session state."""
    if hasattr(ctx, "state") and isinstance(ctx.state, dict):
        return int(ctx.state.get("max_budget_tokens", 0))
    return 0


def _extract_cart_items(llm_response: Any) -> list[CartItem]:
    """
    Parse LLM response for cart-like structures.

    Looks for tool calls whose names or arguments indicate settlement
    purchases (create_escrow, buy, order, etc.) and for structured
    JSON blocks with ``cart`` or ``items`` keys.
    """
    items: list[CartItem] = []

    # Strategy 1: inspect function / tool calls in the response
    candidates = getattr(llm_response, "content", None)
    if candidates is None:
        candidates = getattr(llm_response, "candidates", [])

    parts = []
    if hasattr(candidates, "parts"):
        parts = candidates.parts or []
    elif isinstance(candidates, list):
        for c in candidates:
            content = getattr(c, "content", None)
            if content and hasattr(content, "parts"):
                parts.extend(content.parts or [])

    for part in parts:
        fc = getattr(part, "function_call", None)
        if fc is None:
            continue
        name = getattr(fc, "name", "") or ""
        args = getattr(fc, "args", {}) or {}

        if name in ("create_escrow", "buy", "order", "purchase"):
            items.append(
                CartItem(
                    skill_id=str(args.get("task_type", args.get("skill", name))),
                    provider_id=str(args.get("provider_id", "unknown")),
                    amount_tokens=int(args.get("amount", args.get("amount_tokens", 0))),
                    description=str(args.get("description", "")),
                    metadata=dict(args),
                )
            )

    # Strategy 2: look for a structured "cart" key in args
    for part in parts:
        fc = getattr(part, "function_call", None)
        if fc is None:
            continue
        args = getattr(fc, "args", {}) or {}
        raw_items = args.get("cart", args.get("items", []))
        if isinstance(raw_items, list):
            for raw in raw_items:
                if isinstance(raw, dict) and "provider_id" in raw:
                    items.append(
                        CartItem(
                            skill_id=str(raw.get("skill_id", "unknown")),
                            provider_id=str(raw["provider_id"]),
                            amount_tokens=int(raw.get("amount_tokens", raw.get("amount", 0))),
                            description=str(raw.get("description", "")),
                            metadata=raw,
                        )
                    )

    return items
