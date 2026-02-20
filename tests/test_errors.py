"""Tests for standardized JSON-RPC settlement error codes."""

from __future__ import annotations

import pytest

from adk_a2a_settlement.errors import SettlementError, SettlementErrorCode


class TestSettlementErrorCode:

    def test_codes_are_in_jsonrpc_range(self):
        """All codes must be in the JSON-RPC server-defined range."""
        for code in SettlementErrorCode:
            assert -32099 <= code <= -32000, f"{code.name}={code} outside range"

    def test_all_codes_unique(self):
        values = [c.value for c in SettlementErrorCode]
        assert len(values) == len(set(values))

    def test_known_codes(self):
        assert SettlementErrorCode.PAYMENT_FAILED == -32000
        assert SettlementErrorCode.PAYMENT_PENDING == -32001
        assert SettlementErrorCode.INSUFFICIENT_FUNDS == -32002
        assert SettlementErrorCode.ESCROW_NOT_FOUND == -32003
        assert SettlementErrorCode.ESCROW_EXPIRED == -32004
        assert SettlementErrorCode.ESCROW_ALREADY_SETTLED == -32005
        assert SettlementErrorCode.PROVIDER_MISMATCH == -32006
        assert SettlementErrorCode.SETTLEMENT_NOT_ADVERTISED == -32007
        assert SettlementErrorCode.ATTESTATION_FAILED == -32008
        assert SettlementErrorCode.VERIFICATION_FAILED == -32009
        assert SettlementErrorCode.SETTLEMENT_TTL_EXCEEDED == -32010


class TestSettlementError:

    def test_default_message(self):
        err = SettlementError(SettlementErrorCode.INSUFFICIENT_FUNDS)
        assert "Insufficient funds" in err.message
        assert err.code == SettlementErrorCode.INSUFFICIENT_FUNDS

    def test_custom_message(self):
        err = SettlementError(
            SettlementErrorCode.PAYMENT_FAILED, "Wallet offline"
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
        err = SettlementError(SettlementErrorCode.PAYMENT_FAILED)
        d = err.to_dict()
        assert "data" not in d

    def test_is_exception(self):
        err = SettlementError(SettlementErrorCode.PAYMENT_FAILED)
        assert isinstance(err, Exception)
        with pytest.raises(SettlementError):
            raise err

    def test_repr(self):
        err = SettlementError(SettlementErrorCode.PROVIDER_MISMATCH)
        r = repr(err)
        assert "PROVIDER_MISMATCH" in r
        assert "-32006" in r
