"""Tests for the provider module — escrow verification and skill extraction."""

from __future__ import annotations

from unittest.mock import patch, MagicMock
from types import SimpleNamespace

import pytest

from adk_a2a_settlement.provider import verify_escrow, _extract_skills


class TestVerifyEscrow:

    def test_verifies_valid_escrow(self):
        """Should return escrow dict when escrow is valid and held."""
        agent = SimpleNamespace(
            name="test_agent",
            _settlement_exchange=MagicMock(),
            _settlement_account_id="prov-001",
        )
        agent._settlement_exchange.get_escrow.return_value = {
            "id": "esc-001",
            "status": "held",
            "provider_id": "prov-001",
            "amount": 500,
        }

        message = {
            "metadata": {
                "a2a-se": {
                    "escrowId": "esc-001",
                    "amount": 500,
                }
            }
        }

        result = verify_escrow(agent, message)
        assert result is not None
        assert result["id"] == "esc-001"
        assert result["status"] == "held"

    def test_returns_none_for_no_metadata(self):
        """Should return None if message has no settlement metadata."""
        agent = SimpleNamespace(name="test")
        result = verify_escrow(agent, {"metadata": {}})
        assert result is None

    def test_returns_none_for_no_escrow_id(self):
        """Should return None if settlement block has no escrowId."""
        agent = SimpleNamespace(name="test")
        message = {"metadata": {"a2a-se": {"amount": 500}}}
        result = verify_escrow(agent, message)
        assert result is None

    def test_returns_none_for_wrong_provider(self):
        """Should reject escrow assigned to a different provider."""
        agent = SimpleNamespace(
            name="test_agent",
            _settlement_exchange=MagicMock(),
            _settlement_account_id="prov-001",
        )
        agent._settlement_exchange.get_escrow.return_value = {
            "id": "esc-001",
            "status": "held",
            "provider_id": "prov-002",  # Wrong provider
        }

        message = {"metadata": {"a2a-se": {"escrowId": "esc-001"}}}
        result = verify_escrow(agent, message)
        assert result is None

    def test_returns_none_for_non_held_escrow(self):
        """Should reject escrow that's not in 'held' status."""
        agent = SimpleNamespace(
            name="test_agent",
            _settlement_exchange=MagicMock(),
            _settlement_account_id="prov-001",
        )
        agent._settlement_exchange.get_escrow.return_value = {
            "id": "esc-001",
            "status": "released",  # Already released
            "provider_id": "prov-001",
        }

        message = {"metadata": {"a2a-se": {"escrowId": "esc-001"}}}
        result = verify_escrow(agent, message)
        assert result is None

    def test_returns_none_when_no_exchange_client(self):
        """Should return None if agent has no exchange client."""
        agent = SimpleNamespace(name="test")
        message = {"metadata": {"a2a-se": {"escrowId": "esc-001"}}}
        result = verify_escrow(agent, message)
        assert result is None


class TestExtractSkills:

    def test_extracts_from_tools(self):
        """Should extract tool names as skills."""
        def analyze(): pass
        def summarize(): pass
        agent = SimpleNamespace(tools=[analyze, summarize], sub_agents=[])
        skills = _extract_skills(agent)
        assert "analyze" in skills
        assert "summarize" in skills

    def test_extracts_from_sub_agents(self):
        """Should extract sub_agent names as skills."""
        sub1 = SimpleNamespace(name="researcher")
        sub2 = SimpleNamespace(name="writer")
        agent = SimpleNamespace(tools=[], sub_agents=[sub1, sub2])
        skills = _extract_skills(agent)
        assert "researcher" in skills
        assert "writer" in skills

    def test_handles_no_tools_or_agents(self):
        """Should return empty list when agent has no tools or sub_agents."""
        agent = SimpleNamespace(tools=None, sub_agents=None)
        skills = _extract_skills(agent)
        assert skills == []
