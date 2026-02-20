"""ADK callback and agent wrappers for A2A Settlement Exchange escrow-based task settlement."""

from __future__ import annotations

__version__ = "0.1.0"

from .callbacks import SettlementCallbacks
from .config import SettlementConfig
from .gateway import EdgeGateway
from .interceptors import MandateInterceptors
from .mandates import (
    CartItem,
    CartMandate,
    IntentMandate,
    MandateStatus,
    MerkleProofNode,
    PaymentMandate,
    PreDisputeAttestation,
)
from .mediator import (
    MediatorClient,
    MediatorError,
    TimestampVerificationError,
    VerificationResult,
    compute_merkle_root,
    verify_attestation,
    verify_merkle_proof,
    verify_rfc3161_timestamp,
)
from .provider import to_settled_a2a, verify_escrow
from .requester import SettledRemoteAgent, SettlementInfo, discover_settlement
from .tools import create_settlement_tools

__all__ = [
    "__version__",
    # Config
    "SettlementConfig",
    # Edge gateway
    "EdgeGateway",
    "MandateInterceptors",
    # Mandate models
    "IntentMandate",
    "CartMandate",
    "CartItem",
    "PaymentMandate",
    "PreDisputeAttestation",
    "MerkleProofNode",
    "MandateStatus",
    # Mediator + verification
    "MediatorClient",
    "MediatorError",
    "TimestampVerificationError",
    "VerificationResult",
    "verify_attestation",
    "verify_rfc3161_timestamp",
    "verify_merkle_proof",
    "compute_merkle_root",
    # Provider
    "to_settled_a2a",
    "verify_escrow",
    # Requester
    "SettledRemoteAgent",
    "SettlementInfo",
    "discover_settlement",
    # Tools & callbacks
    "create_settlement_tools",
    "SettlementCallbacks",
]
