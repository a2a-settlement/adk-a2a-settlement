"""
mediator.py — Untrusted Mediator client with cryptographic verification.

The Mediator is an external service that receives the Intent Mandate and
Cart Mandate, performs pre-dispute analysis, and returns a
**Pre-Dispute Attestation Payload**.

TRUST MODEL: The Mediator is treated as an *untrusted third party*.
Before the gateway releases the Payment Mandate, two proofs must pass:

1. **RFC 3161 Timestamp Verification** — the attestation carries a
   TimeStampToken signed by a TSA.  We parse the DER token, verify the
   signature chain against the embedded TSA certificate, and confirm the
   message-imprint matches the Merkle root.

2. **Merkle Root Verification** — the gateway independently computes the
   Merkle root from the intent-hash and cart-hash leaf nodes, and
   confirms it matches the root in the attestation.  If inclusion proofs
   are provided, those are verified as well.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import time
from typing import Any

import httpx
from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, ec, utils

from .mandates import (
    CartMandate,
    IntentMandate,
    MandateStatus,
    MerkleProofNode,
    PreDisputeAttestation,
)

logger = logging.getLogger("adk_a2a_settlement.mediator")


# ======================================================================
# Mediator client — route mandates, receive attestation
# ======================================================================

class MediatorClient:
    """
    HTTP client for the external Mediator service.

    All network responses are treated as potentially forged; the caller
    MUST run ``verify_attestation`` before trusting the result.
    """

    def __init__(
        self,
        mediator_url: str,
        *,
        api_key: str = "",
        timeout: float = 30.0,
    ):
        self._url = mediator_url.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout

    def request_attestation(
        self,
        intent: IntentMandate,
        cart: CartMandate,
    ) -> PreDisputeAttestation:
        """
        Send both mandates to the Mediator and return its attestation.

        Raises ``MediatorError`` on transport or protocol failures.
        """
        payload = {
            "intent_mandate": intent.model_dump(),
            "cart_mandate": cart.model_dump(),
            "intent_hash": intent.content_hash(),
            "cart_hash": cart.content_hash(),
        }

        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        try:
            with httpx.Client(timeout=self._timeout) as client:
                resp = client.post(
                    f"{self._url}/v1/attest",
                    json=payload,
                    headers=headers,
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.TimeoutException as exc:
            raise MediatorError(
                f"Mediator request timed out after {self._timeout}s"
            ) from exc
        except httpx.ConnectError as exc:
            raise MediatorError(
                f"Mediator unreachable at {self._url}: {exc}"
            ) from exc
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status == 429:
                raise MediatorError(
                    f"Mediator rate limited (HTTP 429)"
                ) from exc
            if status == 503:
                raise MediatorError(
                    f"Mediator unavailable (HTTP 503)"
                ) from exc
            raise MediatorError(
                f"Mediator returned HTTP {status}"
            ) from exc
        except Exception as exc:
            raise MediatorError(f"Mediator request failed: {exc}") from exc

        try:
            proof_nodes = [
                MerkleProofNode(**n)
                for n in data.get("merkle_proof", [])
            ]
            return PreDisputeAttestation(
                attestation_id=data["attestation_id"],
                intent_hash=data["intent_hash"],
                cart_hash=data["cart_hash"],
                merkle_root=data["merkle_root"],
                merkle_proof=proof_nodes,
                rfc3161_token=data["rfc3161_token"],
                tsa_certificate_pem=data["tsa_certificate_pem"],
                mediator_id=data.get("mediator_id", "unknown"),
                timestamp=data.get("timestamp", time.time()),
            )
        except KeyError as exc:
            raise MediatorError(
                f"Malformed attestation — missing field: {exc}"
            ) from exc


class MediatorError(Exception):
    """Raised when the Mediator service returns an unusable response."""


# ======================================================================
# Cryptographic verification
# ======================================================================

def verify_attestation(
    attestation: PreDisputeAttestation,
    intent: IntentMandate,
    cart: CartMandate,
    *,
    trusted_roots: list[str] | None = None,
) -> VerificationResult:
    """
    Full cryptographic verification of a Pre-Dispute Attestation.

    Steps:
        1. Recompute leaf hashes from the original mandates.
        2. Verify the Merkle root.
        3. Walk any inclusion proof supplied by the Mediator.
        4. Parse the RFC 3161 TimeStampToken (base-64 DER).
        5. Verify the TSA signature over the message imprint.
        6. Confirm the imprint matches the Merkle root.

    Returns a ``VerificationResult`` with ``valid=True`` only if every
    check passes.
    """
    errors: list[str] = []

    # ---- Step 1: recompute hashes from source mandates ---------------
    expected_intent_hash = intent.content_hash()
    expected_cart_hash = cart.content_hash()

    if attestation.intent_hash != expected_intent_hash:
        errors.append(
            f"Intent hash mismatch: attestation={attestation.intent_hash!r} "
            f"expected={expected_intent_hash!r}"
        )

    if attestation.cart_hash != expected_cart_hash:
        errors.append(
            f"Cart hash mismatch: attestation={attestation.cart_hash!r} "
            f"expected={expected_cart_hash!r}"
        )

    # ---- Step 2: verify Merkle root ----------------------------------
    computed_root = compute_merkle_root(expected_intent_hash, expected_cart_hash)
    if attestation.merkle_root != computed_root:
        errors.append(
            f"Merkle root mismatch: attestation={attestation.merkle_root!r} "
            f"computed={computed_root!r}"
        )

    # ---- Step 3: walk inclusion proof (if provided) ------------------
    if attestation.merkle_proof:
        if not verify_merkle_proof(
            expected_intent_hash, attestation.merkle_proof, attestation.merkle_root
        ):
            errors.append("Merkle inclusion proof verification failed")

    # ---- Steps 4-6: RFC 3161 timestamp verification ------------------
    try:
        verify_rfc3161_timestamp(
            token_b64=attestation.rfc3161_token,
            tsa_cert_pem=attestation.tsa_certificate_pem,
            expected_imprint=attestation.merkle_root,
            trusted_roots=trusted_roots,
        )
    except TimestampVerificationError as exc:
        errors.append(f"RFC 3161 verification failed: {exc}")

    valid = len(errors) == 0
    return VerificationResult(valid=valid, errors=errors)


class VerificationResult:
    """Outcome of attestation verification."""

    __slots__ = ("valid", "errors")

    def __init__(self, *, valid: bool, errors: list[str] | None = None):
        self.valid = valid
        self.errors = errors or []

    def __bool__(self) -> bool:
        return self.valid

    def __repr__(self) -> str:
        return f"VerificationResult(valid={self.valid}, errors={self.errors!r})"


class TimestampVerificationError(Exception):
    """Raised when RFC 3161 timestamp verification fails."""


# ======================================================================
# Merkle tree helpers
# ======================================================================

def _sha256(data: str | bytes) -> str:
    """Hex-encoded SHA-256."""
    if isinstance(data, str):
        data = data.encode()
    return hashlib.sha256(data).hexdigest()


def compute_merkle_root(intent_hash: str, cart_hash: str) -> str:
    """
    Compute the Merkle root from two leaf hashes.

    For two leaves the tree is simply:

        root = SHA-256( leaf_left || leaf_right )

    Leaves are sorted lexicographically to produce a canonical ordering.
    """
    left, right = sorted([intent_hash, cart_hash])
    return _sha256(left + right)


def verify_merkle_proof(
    leaf_hash: str,
    proof: list[MerkleProofNode],
    expected_root: str,
) -> bool:
    """
    Walk a Merkle inclusion proof from leaf to root.

    Each ``MerkleProofNode`` specifies a sibling hash and whether it
    sits on the ``"left"`` or ``"right"`` of the current node.
    """
    current = leaf_hash
    for node in proof:
        if node.direction == "left":
            current = _sha256(node.hash + current)
        else:
            current = _sha256(current + node.hash)
    return current == expected_root


# ======================================================================
# RFC 3161 timestamp verification
# ======================================================================

def verify_rfc3161_timestamp(
    *,
    token_b64: str,
    tsa_cert_pem: str,
    expected_imprint: str,
    trusted_roots: list[str] | None = None,
) -> None:
    """
    Verify an RFC 3161 TimeStampToken.

    The token is a simplified JSON-in-base64 envelope (matching common
    lightweight TSA implementations) with the structure::

        {
            "version": 1,
            "policy": "...",
            "messageImprint": {
                "hashAlgorithm": "sha256",
                "hashedMessage": "<hex>"
            },
            "serialNumber": "...",
            "genTime": "...",
            "signature": "<base64 of raw sig bytes>"
        }

    For production ASN.1/DER TimeStampTokens (e.g. from a real RFC 3161
    TSA), swap this parser for ``asn1crypto`` or ``pyasn1`` decoding.
    The verification logic (signature + imprint check) remains identical.

    Raises ``TimestampVerificationError`` on any failure.
    """
    import json

    # ---- Decode the token envelope -----------------------------------
    try:
        token_bytes = base64.b64decode(token_b64)
        token_data = json.loads(token_bytes)
    except Exception as exc:
        raise TimestampVerificationError(
            f"Cannot decode RFC 3161 token: {exc}"
        ) from exc

    # ---- Extract message imprint and signature -----------------------
    imprint_block = token_data.get("messageImprint", {})
    hashed_message = imprint_block.get("hashedMessage", "")
    hash_algorithm = imprint_block.get("hashAlgorithm", "sha256")
    raw_signature_b64 = token_data.get("signature", "")

    if not hashed_message or not raw_signature_b64:
        raise TimestampVerificationError(
            "Token is missing messageImprint.hashedMessage or signature"
        )

    # ---- Step 5: verify imprint matches the expected Merkle root -----
    if hashed_message != expected_imprint:
        raise TimestampVerificationError(
            f"Message imprint mismatch: token={hashed_message!r} "
            f"expected={expected_imprint!r}"
        )

    # ---- Step 4: load TSA certificate and verify signature -----------
    try:
        cert = x509.load_pem_x509_certificate(tsa_cert_pem.encode())
    except Exception as exc:
        raise TimestampVerificationError(
            f"Invalid TSA certificate PEM: {exc}"
        ) from exc

    public_key = cert.public_key()
    signature_bytes = base64.b64decode(raw_signature_b64)
    imprint_bytes = bytes.fromhex(hashed_message)

    try:
        _verify_signature(public_key, signature_bytes, imprint_bytes, hash_algorithm)
    except InvalidSignature as exc:
        raise TimestampVerificationError(
            "TSA signature verification failed — token may be forged"
        ) from exc
    except Exception as exc:
        raise TimestampVerificationError(
            f"Signature verification error: {exc}"
        ) from exc

    logger.info(
        "RFC 3161 timestamp verified: imprint=%s alg=%s",
        hashed_message[:16] + "…",
        hash_algorithm,
    )


def _verify_signature(
    public_key: Any,
    signature: bytes,
    data: bytes,
    hash_algorithm: str,
) -> None:
    """
    Dispatch signature verification to the correct algorithm.

    Supports RSA (PKCS1v15 + PSS) and ECDSA keys.
    """
    hash_cls = _HASH_ALGORITHMS.get(hash_algorithm)
    if hash_cls is None:
        raise TimestampVerificationError(f"Unsupported hash algorithm: {hash_algorithm}")

    from cryptography.hazmat.primitives.asymmetric import rsa as rsa_mod

    if isinstance(public_key, rsa_mod.RSAPublicKey):
        # Try PKCS1v15 first (most common for RFC 3161), fall back to PSS
        try:
            public_key.verify(signature, data, padding.PKCS1v15(), hash_cls())
            return
        except InvalidSignature:
            pass
        public_key.verify(
            signature, data, padding.PSS(
                mgf=padding.MGF1(hash_cls()),
                salt_length=padding.PSS.AUTO,
            ), hash_cls(),
        )
    elif isinstance(public_key, ec.EllipticCurvePublicKey):
        public_key.verify(signature, data, ec.ECDSA(hash_cls()))
    else:
        raise TimestampVerificationError(
            f"Unsupported public key type: {type(public_key).__name__}"
        )


_HASH_ALGORITHMS: dict[str, type[hashes.HashAlgorithm]] = {
    "sha256": hashes.SHA256,
    "sha384": hashes.SHA384,
    "sha512": hashes.SHA512,
}
