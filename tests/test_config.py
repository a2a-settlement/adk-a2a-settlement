"""Tests for the settlement configuration."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from adk_a2a_settlement.config import SettlementConfig


class TestSettlementConfig:

    def test_defaults(self):
        with patch.dict(os.environ, {}, clear=True):
            cfg = SettlementConfig(api_key="test")
            assert cfg.exchange_url == "https://sandbox.a2a-settlement.org"
            assert cfg.network == "sandbox"
            assert cfg.timeout_seconds == 30
            assert cfg.auto_escrow is True
            assert cfg.auto_settle is True
            assert cfg.default_ttl_minutes == 60

    def test_explicit_overrides(self):
        """Config can be set via constructor args (env defaults at import time)."""
        cfg = SettlementConfig(
            exchange_url="http://custom:3000",
            api_key="key123",
            network="mainnet",
            timeout_seconds=60,
            auto_escrow=False,
            default_ttl_minutes=120,
        )
        assert cfg.exchange_url == "http://custom:3000"
        assert cfg.api_key == "key123"
        assert cfg.network == "mainnet"
        assert cfg.timeout_seconds == 60
        assert cfg.auto_escrow is False
        assert cfg.default_ttl_minutes == 120

    def test_invalid_network(self):
        with pytest.raises(ValueError, match="network must be one of"):
            SettlementConfig(api_key="test", network="invalid")

    def test_invalid_timeout(self):
        with pytest.raises(ValueError, match="timeout_seconds must be between"):
            SettlementConfig(api_key="test", timeout_seconds=0)

        with pytest.raises(ValueError, match="timeout_seconds must be between"):
            SettlementConfig(api_key="test", timeout_seconds=999)

    def test_state_store_defaults(self):
        with patch.dict(os.environ, {}, clear=True):
            cfg = SettlementConfig(api_key="test")
            assert cfg.state_store_type == "memory"
            assert cfg.redis_url == "redis://localhost:6379/0"
            assert cfg.redis_prefix == "a2ase"

    def test_state_store_redis(self):
        cfg = SettlementConfig(
            api_key="test",
            state_store_type="redis",
            redis_url="redis://prod:6379/1",
            redis_prefix="myapp",
        )
        assert cfg.state_store_type == "redis"
        assert cfg.redis_url == "redis://prod:6379/1"
        assert cfg.redis_prefix == "myapp"

    def test_invalid_state_store_type(self):
        with pytest.raises(ValueError, match="state_store_type"):
            SettlementConfig(api_key="test", state_store_type="postgres")
