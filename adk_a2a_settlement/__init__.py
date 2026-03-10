"""ADK callback and agent wrappers for A2A Settlement Exchange escrow-based task settlement."""

from __future__ import annotations

__version__ = "0.3.0"

from .callbacks import SettlementCallbacks
from .config import SettlementConfig
from .errors import SettlementError, SettlementErrorCode, classify_exchange_error
from .state import (
    AbstractStateStore,
    InMemoryStateStore,
    RedisStateStore,
    create_state_store,
)
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
from .grounding import GroundingResult, build_grounded_provenance, ground_deliverable
from .provider import deliver, ground_and_deliver, to_settled_a2a, verify_escrow
from .requester import (
    EscrowTTLWatchdog,
    SettledRemoteAgent,
    SettlementInfo,
    discover_settlement,
)
from .tools import create_settlement_tools

__all__ = [
    "__version__",
    # Config
    "SettlementConfig",
    # Errors (JSON-RPC -32000 to -32099)
    "SettlementError",
    "SettlementErrorCode",
    "classify_exchange_error",
    # State store
    "AbstractStateStore",
    "InMemoryStateStore",
    "RedisStateStore",
    "create_state_store",
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
    # Grounding
    "GroundingResult",
    "ground_deliverable",
    "build_grounded_provenance",
    # Provider
    "deliver",
    "ground_and_deliver",
    "to_settled_a2a",
    "verify_escrow",
    # Requester + TTL watchdog
    "SettledRemoteAgent",
    "EscrowTTLWatchdog",
    "SettlementInfo",
    "discover_settlement",
    # Tools & callbacks
    "create_settlement_tools",
    "SettlementCallbacks",
]
