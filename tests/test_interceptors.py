"""Tests for mandate interceptors — intent and cart extraction."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from adk_a2a_settlement.interceptors import MandateInterceptors


class TestExtractIntent:

    def test_extracts_intent_from_llm_request(self):
        """Should build an IntentMandate from LLM request contents."""
        interceptors = MandateInterceptors(default_user_id="user-1")

        part = SimpleNamespace(text="Buy 500 tokens of sentiment analysis")
        content = SimpleNamespace(parts=[part])
        llm_request = SimpleNamespace(contents=[content])
        ctx = SimpleNamespace(
            state={"user_id": "user-1", "session_id": "sess-42", "max_budget_tokens": 1000},
            session=SimpleNamespace(id="sess-42"),
        )

        result = interceptors.extract_intent(ctx, llm_request)

        assert result is None  # should not short-circuit
        assert interceptors.pending_intent is not None
        assert interceptors.pending_intent.user_id == "user-1"
        assert "sentiment analysis" in interceptors.pending_intent.intent_description
        assert interceptors.pending_intent.max_budget_tokens == 1000

    def test_uses_default_user_id(self):
        """Should fall back to default_user_id when context has none."""
        interceptors = MandateInterceptors(default_user_id="fallback")

        llm_request = SimpleNamespace(contents=[])
        ctx = SimpleNamespace(state={})

        interceptors.extract_intent(ctx, llm_request)
        assert interceptors.pending_intent.user_id == "fallback"

    def test_generates_unique_mandate_ids(self):
        """Each extraction should produce a unique mandate_id."""
        interceptors = MandateInterceptors()

        llm_request = SimpleNamespace(contents=[])
        ctx = SimpleNamespace(state={})

        interceptors.extract_intent(ctx, llm_request)
        id1 = interceptors.pending_intent.mandate_id

        interceptors.extract_intent(ctx, llm_request)
        id2 = interceptors.pending_intent.mandate_id

        assert id1 != id2


class TestExtractCart:

    def _make_fc_response(self, name: str, args: dict):
        """Build a minimal LLM response with a single function_call."""
        fc = SimpleNamespace(name=name, args=args)
        part = SimpleNamespace(function_call=fc)
        content = SimpleNamespace(parts=[part])
        candidate = SimpleNamespace(content=content)
        return SimpleNamespace(candidates=[candidate])

    def test_extracts_cart_from_create_escrow_call(self):
        """Should build CartMandate from a create_escrow tool call."""
        interceptors = MandateInterceptors()

        # Set up a pending intent first
        llm_request = SimpleNamespace(contents=[])
        ctx = SimpleNamespace(state={"user_id": "u1"})
        interceptors.extract_intent(ctx, llm_request)

        response = self._make_fc_response("create_escrow", {
            "provider_id": "prov-001",
            "amount": 100,
            "task_type": "sentiment",
        })

        result = interceptors.extract_cart(ctx, response)
        assert result is None
        assert interceptors.pending_cart is not None
        assert len(interceptors.pending_cart.items) == 1
        assert interceptors.pending_cart.items[0].provider_id == "prov-001"
        assert interceptors.pending_cart.items[0].amount_tokens == 100
        assert interceptors.pending_cart.total_tokens == 100

    def test_skips_when_no_pending_intent(self):
        """Should not build a cart if no intent was extracted."""
        interceptors = MandateInterceptors()

        response = self._make_fc_response("create_escrow", {"provider_id": "p", "amount": 10})
        ctx = SimpleNamespace(state={})

        interceptors.extract_cart(ctx, response)
        assert interceptors.pending_cart is None

    def test_fires_on_mandates_ready(self):
        """Should call the callback when both mandates are available."""
        callback = MagicMock()
        interceptors = MandateInterceptors(on_mandates_ready=callback)

        ctx = SimpleNamespace(state={"user_id": "u1"})
        llm_request = SimpleNamespace(contents=[])
        interceptors.extract_intent(ctx, llm_request)

        response = self._make_fc_response("create_escrow", {
            "provider_id": "prov-001",
            "amount": 50,
            "task_type": "analysis",
        })
        interceptors.extract_cart(ctx, response)

        callback.assert_called_once()
        args = callback.call_args[0]
        assert args[0] is interceptors.pending_intent
        assert args[1] is interceptors.pending_cart


class TestClear:

    def test_clears_state(self):
        interceptors = MandateInterceptors()
        ctx = SimpleNamespace(state={})
        interceptors.extract_intent(ctx, SimpleNamespace(contents=[]))
        assert interceptors.pending_intent is not None

        interceptors.clear()
        assert interceptors.pending_intent is None
        assert interceptors.pending_cart is None
