"""Tests for ADK settlement tools."""

from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest

from adk_a2a_settlement.config import SettlementConfig
from adk_a2a_settlement.tools import create_settlement_tools


@pytest.fixture
def tools(config):
    with patch("adk_a2a_settlement.tools.SettlementExchangeClient") as MockClient:
        mock_exchange = MagicMock()
        MockClient.return_value = mock_exchange
        tool_list = create_settlement_tools(config)
        yield tool_list, mock_exchange


class TestSettlementTools:

    def test_creates_seven_tools(self, tools):
        tool_list, _ = tools
        assert len(tool_list) == 7
        names = {t.__name__ for t in tool_list}
        assert names == {
            "check_balance",
            "create_escrow",
            "release_escrow",
            "refund_escrow",
            "dispute_escrow",
            "lookup_agent",
            "get_escrow_status",
        }

    def test_all_tools_have_docstrings(self, tools):
        """ADK uses docstrings as tool descriptions."""
        tool_list, _ = tools
        for tool in tool_list:
            assert tool.__doc__ is not None
            assert len(tool.__doc__) > 10

    def test_check_balance_success(self, tools):
        tool_list, mock_exchange = tools
        check_balance = tool_list[0]

        mock_exchange.get_balance.return_value = {
            "bot_name": "TestBot",
            "available": 1000,
            "held_in_escrow": 200,
            "total_earned": 5000,
            "total_spent": 3800,
            "reputation": 0.85,
        }

        result = check_balance()
        assert "1000" in result
        assert "200" in result
        assert "0.85" in result

    def test_check_balance_failure(self, tools):
        tool_list, mock_exchange = tools
        check_balance = tool_list[0]

        mock_exchange.get_balance.side_effect = Exception("Connection refused")

        result = check_balance()
        assert "Failed" in result

    def test_create_escrow_success(self, tools):
        tool_list, mock_exchange = tools
        create_escrow = tool_list[1]

        mock_exchange.create_escrow.return_value = {
            "escrow_id": "esc-001",
            "amount": 500,
            "fee_amount": 2,
            "status": "held",
            "expires_at": "2026-02-20T00:00:00Z",
        }

        result = create_escrow("provider-001", 500, "task-001", "analysis", 60)
        assert "esc-001" in result
        assert "500" in result
        mock_exchange.create_escrow.assert_called_once()

    def test_release_escrow_success(self, tools):
        tool_list, mock_exchange = tools
        release_escrow = tool_list[2]

        mock_exchange.release_escrow.return_value = {
            "escrow_id": "esc-001",
            "amount_paid": 500,
            "provider_id": "provider-001",
        }

        result = release_escrow("esc-001")
        assert "released" in result.lower() or "500" in result

    def test_dispute_escrow_success(self, tools):
        tool_list, mock_exchange = tools
        dispute_escrow = tool_list[4]

        mock_exchange.dispute_escrow.return_value = {
            "escrow_id": "esc-001",
            "status": "disputed",
            "reason": "Incomplete deliverable",
        }

        result = dispute_escrow("esc-001", "Incomplete deliverable")
        assert "disputed" in result.lower()
        assert "mediator" in result.lower()

    def test_lookup_agent_with_results(self, tools):
        tool_list, mock_exchange = tools
        lookup_agent = tool_list[5]

        mock_exchange.directory.return_value = {
            "bots": [
                {
                    "id": "bot-001",
                    "bot_name": "AnalystBot",
                    "reputation": 0.9,
                    "skills": ["sentiment-analysis"],
                }
            ],
            "count": 1,
        }

        result = lookup_agent("sentiment-analysis")
        assert "AnalystBot" in result
        assert "0.9" in result

    def test_lookup_agent_no_results(self, tools):
        tool_list, mock_exchange = tools
        lookup_agent = tool_list[5]

        mock_exchange.directory.return_value = {"bots": [], "count": 0}

        result = lookup_agent("nonexistent-skill")
        assert "No agents found" in result
