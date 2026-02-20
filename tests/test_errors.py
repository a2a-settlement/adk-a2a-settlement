"""Tests for standardized JSON-RPC settlement error codes."""

from __future__ import annotations

import pytest

from adk_a2a_settlement.errors import (
    SettlementError,
    SettlementErrorCode,
    classify_exchange_error,
)


class TestSettlementErrorCode:

    def test_codes_are_in_jsonrpc_range(self):
        """All codes must be in the JSON-RPC server-defined range."""
        for code in SettlementErrorCode:
            assert -32099 <= code <= -32000, f"{code.name}={code} outside range"

    def test_all_codes_unique(self):
        values = [c.value for c in SettlementErrorCode]
        assert len(values) == len(set(values))

    def test_known_codes(self):
        assert SettlementErrorCode.INTERNAL_ERROR == -32000
        assert SettlementErrorCode.INSUFFICIENT_FUNDS == -32001
        assert SettlementErrorCode.PAYMENT_PENDING == -32002
        assert SettlementErrorCode.ESCROW_NOT_FOUND == -32003
        assert SettlementErrorCode.ESCROW_EXPIRED == -32004
        assert SettlementErrorCode.ESCROW_ALREADY_SETTLED == -32005
        assert SettlementErrorCode.PROVIDER_MISMATCH == -32006
        assert SettlementErrorCode.SETTLEMENT_NOT_ADVERTISED == -32007
        assert SettlementErrorCode.ATTESTATION_FAILED == -32008
        assert SettlementErrorCode.VERIFICATION_FAILED == -32009
        assert SettlementErrorCode.SETTLEMENT_TTL_EXCEEDED == -32010

    def test_new_transport_codes(self):
        assert SettlementErrorCode.NETWORK_CONGESTION == -32011
        assert SettlementErrorCode.NETWORK_TIMEOUT == -32012
        assert SettlementErrorCode.RATE_LIMITED == -32013
        assert SettlementErrorCode.EXCHANGE_UNAVAIL == -32014
        assert SettlementErrorCode.MEDIATOR_UNAVAIL == -32015

    def test_new_compliance_code(self):
        assert SettlementErrorCode.COMPLIANCE_REJECT == -32020

    def test_new_mandate_code(self):
        assert SettlementErrorCode.MANDATE_MISMATCH == -32025

    def test_total_code_count(self):
        assert len(SettlementErrorCode) == 18


class TestSettlementError:

    def test_default_message(self):
        err = SettlementError(SettlementErrorCode.INSUFFICIENT_FUNDS)
        assert "balance" in err.message.lower() or "funds" in err.message.lower()
        assert err.code == SettlementErrorCode.INSUFFICIENT_FUNDS

    def test_custom_message(self):
        err = SettlementError(
            SettlementErrorCode.INTERNAL_ERROR, "Wallet offline"
        )
        assert err.message == "Wallet offline"

    def test_data_field(self):
        err = SettlementError(
            SettlementErrorCode.INSUFFICIENT_FUNDS,
            data={"required": 500, "available": 120},
        )
        assert err.data["required"] == 500
        assert err.data["available"] == 120

    def test_to_dict_structure(self):
        err = SettlementError(
            SettlementErrorCode.ESCROW_EXPIRED,
            data={"escrow_id": "esc-001"},
        )
        d = err.to_dict()
        assert d["code"] == -32004
        assert "expired" in d["message"].lower()
        assert d["data"]["escrow_id"] == "esc-001"

    def test_to_dict_omits_empty_data(self):
        err = SettlementError(SettlementErrorCode.INTERNAL_ERROR)
        d = err.to_dict()
        assert "data" not in d

    def test_is_exception(self):
        err = SettlementError(SettlementErrorCode.INTERNAL_ERROR)
        assert isinstance(err, Exception)
        with pytest.raises(SettlementError):
            raise err

    def test_repr(self):
        err = SettlementError(SettlementErrorCode.PROVIDER_MISMATCH)
        r = repr(err)
        assert "PROVIDER_MISMATCH" in r
        assert "-32006" in r

    def test_new_codes_have_default_messages(self):
        for code in SettlementErrorCode:
            err = SettlementError(code)
            assert err.message != "Settlement error", f"{code.name} has no default message"


class TestClassifyExchangeError:

    def test_timeout(self):
        import httpx
        exc = httpx.ReadTimeout("read timed out")
        assert classify_exchange_error(exc) == SettlementErrorCode.NETWORK_TIMEOUT

    def test_connect_error(self):
        import httpx
        exc = httpx.ConnectError("Connection refused")
        assert classify_exchange_error(exc) == SettlementErrorCode.EXCHANGE_UNAVAIL

    def test_insufficient_funds(self):
        exc = Exception("Insufficient balance for escrow")
        assert classify_exchange_error(exc) == SettlementErrorCode.INSUFFICIENT_FUNDS

    def test_rate_limited_string(self):
        exc = Exception("rate limit exceeded")
        assert classify_exchange_error(exc) == SettlementErrorCode.RATE_LIMITED

    def test_congestion_string(self):
        exc = Exception("network congestion detected")
        assert classify_exchange_error(exc) == SettlementErrorCode.NETWORK_CONGESTION

    def test_gas_string(self):
        exc = Exception("gas price too high")
        assert classify_exchange_error(exc) == SettlementErrorCode.NETWORK_CONGESTION

    def test_compliance_string(self):
        exc = Exception("compliance check blocked transaction")
        assert classify_exchange_error(exc) == SettlementErrorCode.COMPLIANCE_REJECT

    def test_not_found_string(self):
        exc = Exception("escrow not found")
        assert classify_exchange_error(exc) == SettlementErrorCode.ESCROW_NOT_FOUND

    def test_already_settled_string(self):
        exc = Exception("escrow already released")
        assert classify_exchange_error(exc) == SettlementErrorCode.ESCROW_ALREADY_SETTLED

    def test_generic_falls_to_internal_error(self):
        exc = Exception("something unexpected")
        assert classify_exchange_error(exc) == SettlementErrorCode.INTERNAL_ERROR

    def test_http_429(self):
        import httpx
        resp = httpx.Response(429, request=httpx.Request("POST", "http://x"))
        exc = httpx.HTTPStatusError("rate limited", request=resp.request, response=resp)
        assert classify_exchange_error(exc) == SettlementErrorCode.RATE_LIMITED

    def test_http_503(self):
        import httpx
        resp = httpx.Response(503, request=httpx.Request("POST", "http://x"))
        exc = httpx.HTTPStatusError("unavailable", request=resp.request, response=resp)
        assert classify_exchange_error(exc) == SettlementErrorCode.EXCHANGE_UNAVAIL
