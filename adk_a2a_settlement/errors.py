"""
errors.py — Standardized JSON-RPC error codes for settlement operations.

Uses the JSON-RPC 2.0 server-defined error range (-32000 to -32099) so
client-side orchestrators can react programmatically — e.g. prompting a
user to top up a wallet on INSUFFICIENT_FUNDS, or retrying on
PAYMENT_PENDING.

Usage::

    from adk_a2a_settlement.errors import SettlementError, SettlementErrorCode

    raise SettlementError(
        SettlementErrorCode.INSUFFICIENT_FUNDS,
        data={"required": 500, "available": 120},
    )

Wire format (mirrors JSON-RPC 2.0 error object)::

    {
        "code": -32002,
        "message": "Insufficient funds to create escrow",
        "data": {"required": 500, "available": 120}
    }
"""

from __future__ import annotations

from enum import IntEnum
from typing import Any


class SettlementErrorCode(IntEnum):
    """
    JSON-RPC 2.0 server-defined error codes for A2A settlement.

    Range -32000 to -32099 is reserved by the spec for implementation-
    defined server errors.  Each code maps to a specific failure mode
    that client orchestrators can match on without string parsing.
    """

    PAYMENT_FAILED = -32000
    PAYMENT_PENDING = -32001
    INSUFFICIENT_FUNDS = -32002
    ESCROW_NOT_FOUND = -32003
    ESCROW_EXPIRED = -32004
    ESCROW_ALREADY_SETTLED = -32005
    PROVIDER_MISMATCH = -32006
    SETTLEMENT_NOT_ADVERTISED = -32007
    ATTESTATION_FAILED = -32008
    VERIFICATION_FAILED = -32009
    SETTLEMENT_TTL_EXCEEDED = -32010


_DEFAULT_MESSAGES: dict[SettlementErrorCode, str] = {
    SettlementErrorCode.PAYMENT_FAILED: "Payment processing failed",
    SettlementErrorCode.PAYMENT_PENDING: "Payment is pending confirmation",
    SettlementErrorCode.INSUFFICIENT_FUNDS: "Insufficient funds to create escrow",
    SettlementErrorCode.ESCROW_NOT_FOUND: "No escrow found for the given identifier",
    SettlementErrorCode.ESCROW_EXPIRED: "Escrow has expired",
    SettlementErrorCode.ESCROW_ALREADY_SETTLED: "Escrow was already released or refunded",
    SettlementErrorCode.PROVIDER_MISMATCH: "Escrow is assigned to a different provider",
    SettlementErrorCode.SETTLEMENT_NOT_ADVERTISED: "Agent does not advertise settlement capabilities",
    SettlementErrorCode.ATTESTATION_FAILED: "Mediator attestation request failed",
    SettlementErrorCode.VERIFICATION_FAILED: "Cryptographic verification of attestation failed",
    SettlementErrorCode.SETTLEMENT_TTL_EXCEEDED: "Settlement time-to-live exceeded; task auto-released",
}


class SettlementError(Exception):
    """
    Structured settlement error carrying a JSON-RPC 2.0 compatible
    error code, human-readable message, and optional machine-readable data.

    Attributes:
        code:    A ``SettlementErrorCode`` (int in -32000 … -32099).
        message: Human-readable description.
        data:    Optional dict with contextual details for programmatic use.
    """

    def __init__(
        self,
        code: SettlementErrorCode,
        message: str | None = None,
        *,
        data: dict[str, Any] | None = None,
    ):
        self.code = code
        self.message = message or _DEFAULT_MESSAGES.get(code, "Settlement error")
        self.data = data or {}
        super().__init__(self.message)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-RPC 2.0 error object."""
        result: dict[str, Any] = {
            "code": int(self.code),
            "message": self.message,
        }
        if self.data:
            result["data"] = self.data
        return result

    def __repr__(self) -> str:
        return (
            f"SettlementError(code={self.code!r}, "
            f"message={self.message!r}, data={self.data!r})"
        )
