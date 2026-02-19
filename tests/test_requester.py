"""Tests for the requester module — agent card discovery and settlement info parsing."""

from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest

from adk_a2a_settlement.requester import discover_settlement, SettlementInfo


class TestDiscoverSettlement:

    def test_parses_v05_multi_exchange_format(self, sample_agent_card):
        """Should parse v0.5 multi-exchange agent card format."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = sample_agent_card
        mock_resp.raise_for_status = MagicMock()

        with patch("adk_a2a_settlement.requester.httpx.Client") as MockClient:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.get.return_value = mock_resp
            MockClient.return_value = mock_client

            info = discover_settlement("http://localhost:8001/.well-known/agent.json")

        assert info is not None
        assert info.exchange_url == "http://localhost:3000/v1"
        assert info.account_id == "prov-agent-001"
        assert info.reputation == 0.85
        assert info.availability == 0.95
        assert info.required is True
        assert "sentiment-analysis" in info.pricing
        assert info.pricing["sentiment-analysis"]["baseTokens"] == 100

    def test_returns_none_for_no_settlement(self, sample_agent_card_no_settlement):
        """Should return None when agent card has no settlement extension."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = sample_agent_card_no_settlement
        mock_resp.raise_for_status = MagicMock()

        with patch("adk_a2a_settlement.requester.httpx.Client") as MockClient:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.get.return_value = mock_resp
            MockClient.return_value = mock_client

            info = discover_settlement("http://localhost:8002/.well-known/agent.json")

        assert info is None

    def test_returns_none_on_fetch_failure(self):
        """Should return None if agent card fetch fails."""
        with patch("adk_a2a_settlement.requester.httpx.Client") as MockClient:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.get.side_effect = Exception("Connection refused")
            MockClient.return_value = mock_client

            info = discover_settlement("http://unreachable:9999/agent.json")

        assert info is None

    def test_handles_single_string_account_id(self):
        """Should handle v0.2 format with string account_ids instead of dict."""
        card = {
            "capabilities": {
                "extensions": [
                    {
                        "uri": "https://a2a-settlement.org/extensions/settlement/v1",
                        "required": False,
                        "params": {
                            "exchangeUrls": ["http://exchange:3000/v1"],
                            "preferredExchange": "http://exchange:3000/v1",
                            "accountIds": "simple-id-001",
                            "pricing": {},
                        },
                    }
                ]
            }
        }

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = card
        mock_resp.raise_for_status = MagicMock()

        with patch("adk_a2a_settlement.requester.httpx.Client") as MockClient:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.get.return_value = mock_resp
            MockClient.return_value = mock_client

            info = discover_settlement("http://localhost:8001/agent.json")

        assert info is not None
        assert info.account_id == "simple-id-001"


class TestSettlementInfo:

    def test_dataclass_fields(self):
        info = SettlementInfo(
            exchange_url="http://exchange:3000",
            account_id="acct-001",
            pricing={"task": {"baseTokens": 50}},
            reputation=0.9,
        )
        assert info.exchange_url == "http://exchange:3000"
        assert info.account_id == "acct-001"
        assert info.pricing["task"]["baseTokens"] == 50
        assert info.reputation == 0.9
        assert info.availability == 1.0  # default
        assert info.required is False  # default
