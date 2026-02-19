"""ADK callback and agent wrappers for A2A Settlement Exchange escrow-based task settlement."""

from __future__ import annotations

__version__ = "0.1.0"

from .callbacks import SettlementCallbacks
from .config import SettlementConfig
from .provider import to_settled_a2a, verify_escrow
from .requester import SettledRemoteAgent, SettlementInfo, discover_settlement
from .tools import create_settlement_tools

__all__ = [
    "__version__",
    "SettlementConfig",
    "to_settled_a2a",
    "verify_escrow",
    "SettledRemoteAgent",
    "SettlementInfo",
    "discover_settlement",
    "create_settlement_tools",
    "SettlementCallbacks",
]
