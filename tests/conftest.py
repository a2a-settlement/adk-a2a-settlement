"""Shared test fixtures for the ADK settlement integration."""

from __future__ import annotations

import pytest

from adk_a2a_settlement.config import SettlementConfig


@pytest.fixture
def config():
    """Test config with sandbox defaults."""
    return SettlementConfig(
        exchange_url="http://localhost:3000",
        api_key="test_api_key",
        network="sandbox",
        auto_escrow=True,
        auto_settle=True,
        default_ttl_minutes=60,
    )


@pytest.fixture
def sample_agent_card():
    """A sample agent card with settlement extension."""
    return {
        "name": "SentimentBot",
        "version": "0.1.0",
        "description": "Sentiment analysis agent",
        "url": "http://localhost:8001",
        "defaultInputModes": ["text/plain"],
        "defaultOutputModes": ["text/plain"],
        "capabilities": {
            "streaming": True,
            "extensions": [
                {
                    "uri": "https://a2a-settlement.org/extensions/settlement/v1",
                    "description": "Accepts token-based payment via A2A Settlement Exchange",
                    "required": True,
                    "params": {
                        "exchangeUrls": ["http://localhost:3000/v1"],
                        "preferredExchange": "http://localhost:3000/v1",
                        "accountIds": {
                            "http://localhost:3000/v1": "prov-agent-001"
                        },
                        "currency": "ATE",
                        "pricing": {
                            "sentiment-analysis": {
                                "baseTokens": 100,
                                "model": "per-request",
                                "currency": "ATE",
                            }
                        },
                        "reputation": 0.85,
                        "availability": 0.95,
                    },
                }
            ],
        },
        "skills": [
            {
                "id": "sentiment-analysis",
                "name": "Sentiment Analysis",
                "description": "Analyzes text sentiment",
            }
        ],
    }


@pytest.fixture
def sample_agent_card_no_settlement():
    """An agent card without settlement extension."""
    return {
        "name": "FreeBot",
        "version": "0.1.0",
        "description": "Free agent",
        "url": "http://localhost:8002",
        "capabilities": {
            "extensions": [],
        },
    }
