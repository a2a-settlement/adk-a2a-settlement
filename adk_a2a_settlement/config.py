"""
config.py — Environment-driven configuration for adk-a2a-settlement.

Follows the same env-driven pattern as litellm-a2a-settlement/config.py,
adapted for Pydantic (already an ADK dependency).
"""

from __future__ import annotations

import os

from pydantic import BaseModel, field_validator


class SettlementConfig(BaseModel):
    """
    Configuration for the A2A Settlement Exchange integration with ADK.

    Reads from environment variables by default:
        A2ASE_EXCHANGE_URL  — exchange base URL (default: sandbox)
        A2ASE_API_KEY       — required, get at sandbox.a2a-se.dev
        A2ASE_NETWORK       — "sandbox" or "mainnet" (default: sandbox)
        A2ASE_TIMEOUT       — HTTP timeout in seconds (default: 30)
        A2ASE_AUTO_ESCROW   — auto-create escrow on remote calls (default: true)
        A2ASE_AUTO_SETTLE   — auto-release/refund on completion (default: true)
        A2ASE_DEFAULT_TTL   — default escrow TTL in minutes (default: 60)
    """

    exchange_url: str = os.getenv(
        "A2ASE_EXCHANGE_URL", "https://sandbox.a2a-se.dev"
    )
    api_key: str = os.getenv("A2ASE_API_KEY", "")
    network: str = os.getenv("A2ASE_NETWORK", "sandbox")
    timeout_seconds: int = int(os.getenv("A2ASE_TIMEOUT", "30"))
    auto_escrow: bool = os.getenv("A2ASE_AUTO_ESCROW", "true").lower() == "true"
    auto_settle: bool = os.getenv("A2ASE_AUTO_SETTLE", "true").lower() == "true"
    default_ttl_minutes: int = int(os.getenv("A2ASE_DEFAULT_TTL", "60"))

    @field_validator("network")
    @classmethod
    def validate_network(cls, v: str) -> str:
        allowed = {"sandbox", "mainnet", "devnet"}
        if v not in allowed:
            raise ValueError(f"network must be one of {allowed}, got '{v}'")
        return v

    @field_validator("timeout_seconds")
    @classmethod
    def validate_timeout(cls, v: int) -> int:
        if v < 1 or v > 300:
            raise ValueError("timeout_seconds must be between 1 and 300")
        return v
