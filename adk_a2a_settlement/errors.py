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
        "code": -32001,
        "message": "Source wallet or account lacks required balance",
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

    Layout:
      -32000          General server-side failure
      -32001          Insufficient funds
      -32002          Payment pending
      -32003 … -32010 Escrow / provider / attestation logic codes
      -32011 … -32015 Transport & rate-limit codes
      -32020          Compliance rejection
      -32025          Mandate mismatch
    """

    # -- General & balance ------------------------------------------------
    INTERNAL_ERROR = -32000
    INSUFFICIENT_FUNDS = -32001
    PAYMENT_PENDING = -32002

    # -- Escrow lifecycle -------------------------------------------------
    ESCROW_NOT_FOUND = -32003
    ESCROW_EXPIRED = -32004
    ESCROW_ALREADY_SETTLED = -32005
    PROVIDER_MISMATCH = -32006
    SETTLEMENT_NOT_ADVERTISED = -32007
    ATTESTATION_FAILED = -32008
    VERIFICATION_FAILED = -32009
    SETTLEMENT_TTL_EXCEEDED = -32010

    # -- Transport / rate-limit -------------------------------------------
    NETWORK_CONGESTION = -32011
    NETWORK_TIMEOUT = -32012
    RATE_LIMITED = -32013
    EXCHANGE_UNAVAIL = -32014
    MEDIATOR_UNAVAIL = -32015

    # -- Compliance -------------------------------------------------------
    COMPLIANCE_REJECT = -32020

    # -- Mandate ----------------------------------------------------------
    MANDATE_MISMATCH = -32025


_DEFAULT_MESSAGES: dict[SettlementErrorCode, str] = {
    SettlementErrorCode.INTERNAL_ERROR: "General server-side failure during settlement",
    SettlementErrorCode.INSUFFICIENT_FUNDS: "Source wallet or account lacks required balance",
    SettlementErrorCode.PAYMENT_PENDING: "Payment is pending confirmation",
    SettlementErrorCode.ESCROW_NOT_FOUND: "No escrow found for the given identifier",
    SettlementErrorCode.ESCROW_EXPIRED: "Escrow has expired",
    SettlementErrorCode.ESCROW_ALREADY_SETTLED: "Escrow was already released or refunded",
    SettlementErrorCode.PROVIDER_MISMATCH: "Escrow is assigned to a different provider",
    SettlementErrorCode.SETTLEMENT_NOT_ADVERTISED: "Agent does not advertise settlement capabilities",
    SettlementErrorCode.ATTESTATION_FAILED: "Mediator attestation request failed",
    SettlementErrorCode.VERIFICATION_FAILED: "Cryptographic verification of attestation failed",
    SettlementErrorCode.SETTLEMENT_TTL_EXCEEDED: "Settlement time-to-live exceeded; task auto-released",
    SettlementErrorCode.NETWORK_CONGESTION: "Settlement delayed by high on-chain traffic or gas",
    SettlementErrorCode.NETWORK_TIMEOUT: "Escrow TTL expired before payment confirmation",
    SettlementErrorCode.RATE_LIMITED: "Requester exceeded payout frequency or spending limits",
    SettlementErrorCode.EXCHANGE_UNAVAIL: "Price discovery or oracle service is currently unreachable",
    SettlementErrorCode.MEDIATOR_UNAVAIL: "Mediation engine failed to respond for evaluation",
    SettlementErrorCode.COMPLIANCE_REJECT: "Blocked by SEC 17a-4 WORM or PII filters",
    SettlementErrorCode.MANDATE_MISMATCH: "AP2 mandate does not align with the active session",
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


def classify_exchange_error(exc: Exception) -> SettlementErrorCode:
    """
    Inspect an exception from the exchange or network layer and return
    the most specific ``SettlementErrorCode``.

    Used across requester, provider, and tools modules to replace
    fragile string-matching with a single classification function.
    """
    import httpx

    if isinstance(exc, httpx.TimeoutException):
        return SettlementErrorCode.NETWORK_TIMEOUT

    if isinstance(exc, httpx.ConnectError):
        return SettlementErrorCode.EXCHANGE_UNAVAIL

    exc_str = str(exc).lower()

    if "insufficient" in exc_str or "balance" in exc_str:
        return SettlementErrorCode.INSUFFICIENT_FUNDS

    if "rate limit" in exc_str or "rate_limit" in exc_str or "too many" in exc_str:
        return SettlementErrorCode.RATE_LIMITED

    if "congestion" in exc_str or "gas" in exc_str:
        return SettlementErrorCode.NETWORK_CONGESTION

    if "compliance" in exc_str or "blocked" in exc_str or "worm" in exc_str:
        return SettlementErrorCode.COMPLIANCE_REJECT

    if "not found" in exc_str:
        return SettlementErrorCode.ESCROW_NOT_FOUND

    if "already" in exc_str and ("released" in exc_str or "refunded" in exc_str or "settled" in exc_str):
        return SettlementErrorCode.ESCROW_ALREADY_SETTLED

    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status == 429:
            return SettlementErrorCode.RATE_LIMITED
        if status == 503:
            return SettlementErrorCode.EXCHANGE_UNAVAIL

    return SettlementErrorCode.INTERNAL_ERROR
