"""Tests for mediator client and cryptographic verification.

Generates real EC keys, signs real RFC 3161-style tokens, and verifies
the full chain: Merkle root → timestamp imprint → TSA signature.
"""

from __future__ import annotations

import base64
import hashlib
import json
import time

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from adk_a2a_settlement.mandates import (
    CartItem,
    CartMandate,
    IntentMandate,
    MerkleProofNode,
    PreDisputeAttestation,
)
from adk_a2a_settlement.mediator import (
    MediatorError,
    TimestampVerificationError,
    VerificationResult,
    compute_merkle_root,
    verify_attestation,
    verify_merkle_proof,
    verify_rfc3161_timestamp,
)


# ======================================================================
# Fixtures — generate a real ECDSA key pair and sign tokens
# ======================================================================

def _generate_ec_key():
    return ec.generate_private_key(ec.SECP256R1())


def _self_signed_cert_pem(private_key) -> str:
    """Build a self-signed X.509 cert for the TSA key."""
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
    """Create a signed RFC 3161-style JSON token (base-64 encoded)."""
    imprint_bytes = bytes.fromhex(merkle_root)
    signature = private_key.sign(imprint_bytes, ec.ECDSA(hashes.SHA256()))

    token = {
        "version": 1,
        "policy": "1.2.3.4.1",
        "messageImprint": {
            "hashAlgorithm": "sha256",
            "hashedMessage": merkle_root,
        },
        "serialNumber": "123456",
        "genTime": time.strftime("%Y%m%d%H%M%SZ", time.gmtime()),
        "signature": base64.b64encode(signature).decode(),
    }
    return base64.b64encode(json.dumps(token).encode()).decode()


def _build_test_mandates():
    intent = IntentMandate(
        user_id="user-1",
        session_id="sess-1",
        intent_description="Buy sentiment analysis",
        max_budget_tokens=500,
        timestamp=1700000000.0,
    )
    cart = CartMandate(
        intent_mandate_id=intent.mandate_id,
        items=[
            CartItem(skill_id="sentiment", provider_id="prov-001", amount_tokens=100),
        ],
        total_tokens=100,
        timestamp=1700000001.0,
    )
    return intent, cart


# ======================================================================
# Merkle tree tests
# ======================================================================

class TestMerkleRoot:

    def test_deterministic(self):
        """Same inputs should always produce the same root."""
        a = hashlib.sha256(b"leaf-a").hexdigest()
        b = hashlib.sha256(b"leaf-b").hexdigest()
        assert compute_merkle_root(a, b) == compute_merkle_root(a, b)

    def test_canonical_order(self):
        """Root must be independent of argument order (sorted internally)."""
        a = hashlib.sha256(b"leaf-a").hexdigest()
        b = hashlib.sha256(b"leaf-b").hexdigest()
        assert compute_merkle_root(a, b) == compute_merkle_root(b, a)

    def test_different_inputs_differ(self):
        a = hashlib.sha256(b"leaf-a").hexdigest()
        b = hashlib.sha256(b"leaf-b").hexdigest()
        c = hashlib.sha256(b"leaf-c").hexdigest()
        assert compute_merkle_root(a, b) != compute_merkle_root(a, c)


class TestMerkleProof:

    def test_valid_proof(self):
        """A correct inclusion proof should verify."""
        leaf = hashlib.sha256(b"leaf-a").hexdigest()
        sibling = hashlib.sha256(b"leaf-b").hexdigest()

        left, right = sorted([leaf, sibling])
        root = hashlib.sha256((left + right).encode()).hexdigest()

        if leaf == left:
            proof = [MerkleProofNode(hash=sibling, direction="right")]
        else:
            proof = [MerkleProofNode(hash=sibling, direction="left")]

        assert verify_merkle_proof(leaf, proof, root) is True

    def test_invalid_proof_rejects(self):
        """A proof with a wrong sibling should fail."""
        leaf = hashlib.sha256(b"leaf-a").hexdigest()
        bad_sibling = hashlib.sha256(b"fake").hexdigest()
        proof = [MerkleProofNode(hash=bad_sibling, direction="right")]
        assert verify_merkle_proof(leaf, proof, "0" * 64) is False


# ======================================================================
# RFC 3161 timestamp tests
# ======================================================================

class TestRFC3161Verification:

    def test_valid_token_passes(self):
        """A correctly signed token with matching imprint should pass."""
        key = _generate_ec_key()
        cert_pem = _self_signed_cert_pem(key)
        merkle_root = hashlib.sha256(b"test-root").hexdigest()
        token_b64 = _make_rfc3161_token(merkle_root, key)

        # Should not raise
        verify_rfc3161_timestamp(
            token_b64=token_b64,
            tsa_cert_pem=cert_pem,
            expected_imprint=merkle_root,
        )

    def test_wrong_imprint_rejected(self):
        """Token with a different imprint should be rejected."""
        key = _generate_ec_key()
        cert_pem = _self_signed_cert_pem(key)
        merkle_root = hashlib.sha256(b"real-root").hexdigest()
        token_b64 = _make_rfc3161_token(merkle_root, key)

        with pytest.raises(TimestampVerificationError, match="imprint mismatch"):
            verify_rfc3161_timestamp(
                token_b64=token_b64,
                tsa_cert_pem=cert_pem,
                expected_imprint="0" * 64,
            )

    def test_forged_signature_rejected(self):
        """Token signed by a different key should be rejected."""
        real_key = _generate_ec_key()
        fake_key = _generate_ec_key()
        cert_pem = _self_signed_cert_pem(real_key)  # cert for real key
        merkle_root = hashlib.sha256(b"root").hexdigest()
        token_b64 = _make_rfc3161_token(merkle_root, fake_key)  # signed by fake

        with pytest.raises(TimestampVerificationError, match="signature"):
            verify_rfc3161_timestamp(
                token_b64=token_b64,
                tsa_cert_pem=cert_pem,
                expected_imprint=merkle_root,
            )

    def test_malformed_token_rejected(self):
        """Garbage base-64 should raise a clear error."""
        with pytest.raises(TimestampVerificationError, match="Cannot decode"):
            verify_rfc3161_timestamp(
                token_b64="!!!not-base64!!!",
                tsa_cert_pem="not-a-cert",
                expected_imprint="abc",
            )

    def test_missing_fields_rejected(self):
        """Token without signature or imprint should be caught."""
        token_data = {"version": 1, "messageImprint": {}}
        token_b64 = base64.b64encode(json.dumps(token_data).encode()).decode()

        with pytest.raises(TimestampVerificationError, match="missing"):
            verify_rfc3161_timestamp(
                token_b64=token_b64,
                tsa_cert_pem="not-used",
                expected_imprint="abc",
            )


# ======================================================================
# Full attestation verification
# ======================================================================

class TestVerifyAttestation:

    def test_valid_attestation_passes(self):
        """End-to-end: valid hashes + merkle + RFC 3161 → verified."""
        intent, cart = _build_test_mandates()
        key = _generate_ec_key()
        cert_pem = _self_signed_cert_pem(key)

        intent_hash = intent.content_hash()
        cart_hash = cart.content_hash()
        merkle_root = compute_merkle_root(intent_hash, cart_hash)
        token_b64 = _make_rfc3161_token(merkle_root, key)

        attestation = PreDisputeAttestation(
            attestation_id="att-001",
            intent_hash=intent_hash,
            cart_hash=cart_hash,
            merkle_root=merkle_root,
            rfc3161_token=token_b64,
            tsa_certificate_pem=cert_pem,
            mediator_id="mediator-1",
        )

        result = verify_attestation(attestation, intent, cart)
        assert result.valid is True
        assert result.errors == []

    def test_tampered_intent_hash_rejected(self):
        """Attestation with wrong intent hash should fail."""
        intent, cart = _build_test_mandates()
        key = _generate_ec_key()
        cert_pem = _self_signed_cert_pem(key)

        # Use correct cart hash but wrong intent hash
        cart_hash = cart.content_hash()
        fake_intent_hash = "0" * 64
        fake_root = compute_merkle_root(fake_intent_hash, cart_hash)
        token_b64 = _make_rfc3161_token(fake_root, key)

        attestation = PreDisputeAttestation(
            attestation_id="att-002",
            intent_hash=fake_intent_hash,
            cart_hash=cart_hash,
            merkle_root=fake_root,
            rfc3161_token=token_b64,
            tsa_certificate_pem=cert_pem,
            mediator_id="mediator-1",
        )

        result = verify_attestation(attestation, intent, cart)
        assert result.valid is False
        assert any("Intent hash mismatch" in e for e in result.errors)

    def test_tampered_merkle_root_rejected(self):
        """Attestation with wrong Merkle root should fail."""
        intent, cart = _build_test_mandates()
        key = _generate_ec_key()
        cert_pem = _self_signed_cert_pem(key)

        intent_hash = intent.content_hash()
        cart_hash = cart.content_hash()
        real_root = compute_merkle_root(intent_hash, cart_hash)
        fake_root = "f" * 64
        token_b64 = _make_rfc3161_token(fake_root, key)

        attestation = PreDisputeAttestation(
            attestation_id="att-003",
            intent_hash=intent_hash,
            cart_hash=cart_hash,
            merkle_root=fake_root,
            rfc3161_token=token_b64,
            tsa_certificate_pem=cert_pem,
            mediator_id="mediator-1",
        )

        result = verify_attestation(attestation, intent, cart)
        assert result.valid is False
        assert any("Merkle root mismatch" in e for e in result.errors)

    def test_forged_tsa_signature_rejected(self):
        """Attestation signed by wrong key should fail."""
        intent, cart = _build_test_mandates()
        real_key = _generate_ec_key()
        fake_key = _generate_ec_key()
        cert_pem = _self_signed_cert_pem(real_key)

        intent_hash = intent.content_hash()
        cart_hash = cart.content_hash()
        merkle_root = compute_merkle_root(intent_hash, cart_hash)
        token_b64 = _make_rfc3161_token(merkle_root, fake_key)

        attestation = PreDisputeAttestation(
            attestation_id="att-004",
            intent_hash=intent_hash,
            cart_hash=cart_hash,
            merkle_root=merkle_root,
            rfc3161_token=token_b64,
            tsa_certificate_pem=cert_pem,
            mediator_id="mediator-1",
        )

        result = verify_attestation(attestation, intent, cart)
        assert result.valid is False
        assert any("RFC 3161" in e for e in result.errors)
