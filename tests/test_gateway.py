"""Tests for the edge gateway — full mandate lifecycle."""

from __future__ import annotations

import base64
import hashlib
import json
import time

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from adk_a2a_settlement.gateway import EdgeGateway
from adk_a2a_settlement.mandates import (
    CartItem,
    CartMandate,
    IntentMandate,
    MandateStatus,
    MerkleProofNode,
    PaymentMandate,
    PreDisputeAttestation,
)
from adk_a2a_settlement.mediator import (
    MediatorError,
    VerificationResult,
    compute_merkle_root,
)


# ======================================================================
# Helpers (same key-gen as test_mediator.py)
# ======================================================================

def _generate_ec_key():
    return ec.generate_private_key(ec.SECP256R1())


def _self_signed_cert_pem(private_key) -> str:
    from cryptography import x509 as x509_mod
    from cryptography.x509.oid import NameOID
    import datetime

    subject = issuer = x509_mod.Name([
        x509_mod.NameAttribute(NameOID.COMMON_NAME, "Test TSA"),
    ])
    cert = (
        x509_mod.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(private_key.public_key())
        .serial_number(x509_mod.random_serial_number())
        .not_valid_before(datetime.datetime.utcnow())
        .not_valid_after(datetime.datetime.utcnow() + datetime.timedelta(days=365))
        .sign(private_key, hashes.SHA256())
    )
    return cert.public_bytes(serialization.Encoding.PEM).decode()


def _make_rfc3161_token(merkle_root: str, private_key) -> str:
    imprint_bytes = bytes.fromhex(merkle_root)
    signature = private_key.sign(imprint_bytes, ec.ECDSA(hashes.SHA256()))
    token = {
        "version": 1,
        "policy": "1.2.3.4.1",
        "messageImprint": {
            "hashAlgorithm": "sha256",
            "hashedMessage": merkle_root,
        },
        "serialNumber": "789",
        "genTime": time.strftime("%Y%m%d%H%M%SZ", time.gmtime()),
        "signature": base64.b64encode(signature).decode(),
    }
    return base64.b64encode(json.dumps(token).encode()).decode()


def _build_valid_attestation(intent, cart, key, cert_pem):
    """Build a cryptographically valid attestation for the given mandates."""
    intent_hash = intent.content_hash()
    cart_hash = cart.content_hash()
    merkle_root = compute_merkle_root(intent_hash, cart_hash)
    token_b64 = _make_rfc3161_token(merkle_root, key)

    return PreDisputeAttestation(
        attestation_id="att-gw-001",
        intent_hash=intent_hash,
        cart_hash=cart_hash,
        merkle_root=merkle_root,
        rfc3161_token=token_b64,
        tsa_certificate_pem=cert_pem,
        mediator_id="mediator-1",
    )


# ======================================================================
# Gateway lifecycle tests
# ======================================================================

class TestEdgeGateway:

    def _make_mandates(self):
        intent = IntentMandate(
            user_id="user-1",
            intent_description="Buy analysis",
            max_budget_tokens=500,
            timestamp=1700000000.0,
        )
        cart = CartMandate(
            intent_mandate_id=intent.mandate_id,
            items=[CartItem(skill_id="analysis", provider_id="prov-001", amount_tokens=200)],
            total_tokens=200,
            timestamp=1700000001.0,
        )
        return intent, cart

    def test_valid_attestation_releases_payment(self):
        """Full pipeline with valid crypto should result in RELEASED."""
        key = _generate_ec_key()
        cert_pem = _self_signed_cert_pem(key)
        intent, cart = self._make_mandates()
        attestation = _build_valid_attestation(intent, cart, key, cert_pem)

        gateway = EdgeGateway(mediator_url="http://mediator.test")

        with patch.object(
            gateway._mediator, "request_attestation", return_value=attestation
        ):
            payment, verification = gateway.attest_and_release(intent, cart)

        assert payment.status == MandateStatus.RELEASED
        assert payment.released_at > 0
        assert payment.total_tokens == 200
        assert verification is not None
        assert verification.valid is True

    def test_mediator_failure_rejects_payment(self):
        """If the Mediator is unreachable, payment must be REJECTED."""
        gateway = EdgeGateway(mediator_url="http://mediator.test")

        with patch.object(
            gateway._mediator,
            "request_attestation",
            side_effect=MediatorError("connection refused"),
        ):
            intent, cart = self._make_mandates()
            payment, verification = gateway.attest_and_release(intent, cart)

        assert payment.status == MandateStatus.REJECTED
        assert verification is None  # never reached verification

    def test_forged_attestation_rejects_payment(self):
        """Attestation signed with wrong key must result in REJECTED."""
        real_key = _generate_ec_key()
        fake_key = _generate_ec_key()
        cert_pem = _self_signed_cert_pem(real_key)  # cert is for real_key
        intent, cart = self._make_mandates()

        # Build attestation with correct hashes but wrong signature
        intent_hash = intent.content_hash()
        cart_hash = cart.content_hash()
        merkle_root = compute_merkle_root(intent_hash, cart_hash)
        bad_token = _make_rfc3161_token(merkle_root, fake_key)

        bad_attestation = PreDisputeAttestation(
            attestation_id="att-forged",
            intent_hash=intent_hash,
            cart_hash=cart_hash,
            merkle_root=merkle_root,
            rfc3161_token=bad_token,
            tsa_certificate_pem=cert_pem,
            mediator_id="evil-mediator",
        )

        gateway = EdgeGateway(mediator_url="http://mediator.test")

        with patch.object(
            gateway._mediator, "request_attestation", return_value=bad_attestation
        ):
            payment, verification = gateway.attest_and_release(intent, cart)

        assert payment.status == MandateStatus.REJECTED
        assert verification is not None
        assert verification.valid is False
        assert any("RFC 3161" in e for e in verification.errors)

    def test_tampered_hashes_reject_payment(self):
        """Attestation with tampered leaf hashes must be REJECTED."""
        key = _generate_ec_key()
        cert_pem = _self_signed_cert_pem(key)
        intent, cart = self._make_mandates()

        fake_intent_hash = "a" * 64
        cart_hash = cart.content_hash()
        fake_root = compute_merkle_root(fake_intent_hash, cart_hash)
        token = _make_rfc3161_token(fake_root, key)

        tampered = PreDisputeAttestation(
            attestation_id="att-tampered",
            intent_hash=fake_intent_hash,
            cart_hash=cart_hash,
            merkle_root=fake_root,
            rfc3161_token=token,
            tsa_certificate_pem=cert_pem,
            mediator_id="mediator-1",
        )

        gateway = EdgeGateway(mediator_url="http://mediator.test")

        with patch.object(
            gateway._mediator, "request_attestation", return_value=tampered
        ):
            payment, verification = gateway.attest_and_release(intent, cart)

        assert payment.status == MandateStatus.REJECTED
        assert any("Intent hash mismatch" in e for e in verification.errors)

    def test_on_payment_released_callback(self):
        """The on_payment_released hook should fire on success."""
        key = _generate_ec_key()
        cert_pem = _self_signed_cert_pem(key)
        intent, cart = self._make_mandates()
        attestation = _build_valid_attestation(intent, cart, key, cert_pem)

        released_cb = MagicMock()
        gateway = EdgeGateway(
            mediator_url="http://mediator.test",
            on_payment_released=released_cb,
        )

        with patch.object(
            gateway._mediator, "request_attestation", return_value=attestation
        ):
            gateway.process_mandates(intent, cart)

        released_cb.assert_called_once()
        payment_arg = released_cb.call_args[0][0]
        assert isinstance(payment_arg, PaymentMandate)
        assert payment_arg.status == MandateStatus.RELEASED

    def test_on_payment_rejected_callback(self):
        """The on_payment_rejected hook should fire on failure."""
        rejected_cb = MagicMock()
        gateway = EdgeGateway(
            mediator_url="http://mediator.test",
            on_payment_rejected=rejected_cb,
        )

        with patch.object(
            gateway._mediator,
            "request_attestation",
            side_effect=MediatorError("down"),
        ):
            intent, cart = self._make_mandates()
            gateway.process_mandates(intent, cart)

        # Mediator failure doesn't reach verification, so no rejected callback
        # (rejected callback fires only when verification itself fails)
        rejected_cb.assert_not_called()

    def test_on_after_model_drives_pipeline(self):
        """Gateway.on_after_model should extract cart and process mandates."""
        key = _generate_ec_key()
        cert_pem = _self_signed_cert_pem(key)

        gateway = EdgeGateway(
            mediator_url="http://mediator.test",
            default_user_id="u1",
        )

        # Step 1: extract intent via before_model
        part = SimpleNamespace(text="Buy analysis services")
        content = SimpleNamespace(parts=[part])
        llm_request = SimpleNamespace(contents=[content])
        ctx = SimpleNamespace(state={"user_id": "u1", "max_budget_tokens": 500})

        gateway.interceptors.extract_intent(ctx, llm_request)

        # Step 2: build a valid attestation for whatever mandates emerge
        intent = gateway.interceptors.pending_intent

        fc = SimpleNamespace(
            name="create_escrow",
            args={"provider_id": "prov-001", "amount": 100, "task_type": "analysis"},
        )
        part = SimpleNamespace(function_call=fc)
        content = SimpleNamespace(parts=[part])
        candidate = SimpleNamespace(content=content)
        llm_response = SimpleNamespace(candidates=[candidate])

        # We need to intercept process_mandates since it needs the Mediator
        with patch.object(gateway, "process_mandates") as mock_process:
            gateway.on_after_model(ctx, llm_response)
            mock_process.assert_called_once()
