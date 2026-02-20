"""
gateway.py — The Edge Gateway: ADK <-> A2A <-> Mediator <-> Payment Processor.

Orchestrates the full AP2 mandate lifecycle:

    1. ADK agent receives user request
    2. Interceptors extract Intent Mandate + Cart Mandate
    3. Gateway routes mandates to the (untrusted) Mediator
    4. Mediator returns Pre-Dispute Attestation Payload
    5. Gateway cryptographically verifies the attestation
    6. Only on valid proof: Payment Mandate is released to the processor

Usage::

    from adk_a2a_settlement.gateway import EdgeGateway

    gateway = EdgeGateway(
        mediator_url="https://mediator.example.com",
        config=SettlementConfig(),
    )

    agent = Agent(
        name="trader",
        model="gemini-2.5-flash",
        before_model_callback=gateway.interceptors.extract_intent,
        after_model_callback=gateway.on_after_model,
        ...
    )
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable

from .config import SettlementConfig
from .errors import SettlementError, SettlementErrorCode
from .interceptors import MandateInterceptors
from .mandates import (
    CartMandate,
    IntentMandate,
    MandateStatus,
    PaymentMandate,
)
from .mediator import (
    MediatorClient,
    MediatorError,
    VerificationResult,
    verify_attestation,
)
from .state import AbstractStateStore, create_state_store

logger = logging.getLogger("adk_a2a_settlement.gateway")


class EdgeGateway:
    """
    Edge gateway that wires ADK agents to the AP2 payment protocol.

    Treats the Mediator as an external, untrusted dependency.  The
    gateway only releases a Payment Mandate when the Mediator's
    attestation passes full cryptographic verification (RFC 3161
    timestamp signature + Merkle root hash).
    """

    def __init__(
        self,
        mediator_url: str,
        *,
        config: SettlementConfig | None = None,
        mediator_api_key: str = "",
        mediator_timeout: float = 30.0,
        default_user_id: str = "anonymous",
        trusted_roots: list[str] | None = None,
        on_payment_released: Callable[[PaymentMandate], Any] | None = None,
        on_payment_rejected: Callable[[PaymentMandate, VerificationResult], Any] | None = None,
        state_store: AbstractStateStore | None = None,
    ):
        self._config = config or SettlementConfig()
        self._trusted_roots = trusted_roots

        self._store = state_store or create_state_store(self._config)

        self._mediator = MediatorClient(
            mediator_url,
            api_key=mediator_api_key,
            timeout=mediator_timeout,
        )

        self.interceptors = MandateInterceptors(
            default_user_id=default_user_id,
            on_mandates_ready=self._on_mandates_ready,
        )

        self._on_payment_released = on_payment_released
        self._on_payment_rejected = on_payment_rejected

    # ------------------------------------------------------------------
    # ADK callback — wired as after_model_callback
    # ------------------------------------------------------------------

    def on_after_model(
        self,
        callback_context: Any,
        llm_response: Any,
    ) -> Any | None:
        """
        ADK ``after_model_callback`` that extracts the cart, then drives
        the full attest -> verify -> release pipeline.

        ``extract_cart`` fires ``on_mandates_ready`` (wired to
        ``process_mandates``) automatically when both mandates are
        present, so we only call ``process_mandates`` explicitly if
        the callback was not configured.
        """
        had_cart_before = self.interceptors.pending_cart is not None
        result = self.interceptors.extract_cart(callback_context, llm_response)

        if self.interceptors.on_mandates_ready is None:
            intent, cart = self.interceptors.get_mandates()
            if intent and cart and cart is not (had_cart_before and self.interceptors.pending_cart):
                self.process_mandates(intent, cart)

        return result

    # ------------------------------------------------------------------
    # Core pipeline
    # ------------------------------------------------------------------

    def process_mandates(
        self,
        intent: IntentMandate,
        cart: CartMandate,
    ) -> PaymentMandate:
        """
        Run the full mandate pipeline synchronously:

            mandates -> mediator -> verify -> release / reject

        Returns the resulting Payment Mandate (check its ``status``).
        """
        if cart.intent_mandate_id != intent.mandate_id:
            payment = PaymentMandate(
                attestation_id="",
                intent_mandate_id=intent.mandate_id,
                cart_mandate_id=cart.mandate_id,
                total_tokens=cart.total_tokens,
                status=MandateStatus.REJECTED,
            )
            payment.error = SettlementError(
                SettlementErrorCode.MANDATE_MISMATCH,
                f"Cart references intent {cart.intent_mandate_id!r} "
                f"but active intent is {intent.mandate_id!r}",
                data={
                    "cart_intent_ref": cart.intent_mandate_id,
                    "active_intent": intent.mandate_id,
                },
            ).to_dict()
            self._store.set_gateway_state("last_payment", payment.model_dump() if hasattr(payment, "model_dump") else payment.__dict__)
            return payment

        payment = PaymentMandate(
            attestation_id="",
            intent_mandate_id=intent.mandate_id,
            cart_mandate_id=cart.mandate_id,
            total_tokens=cart.total_tokens,
            status=MandateStatus.PENDING,
        )

        # ---- Route to Mediator (untrusted) ---------------------------
        try:
            attestation = self._mediator.request_attestation(intent, cart)
        except MediatorError as exc:
            exc_lower = str(exc).lower()
            is_transport = any(
                kw in exc_lower
                for kw in ("timeout", "connect", "unreachable", "refused", "unavailable")
            )
            error_code = (
                SettlementErrorCode.MEDIATOR_UNAVAIL if is_transport
                else SettlementErrorCode.ATTESTATION_FAILED
            )
            logger.error("Mediator attestation failed: %s", exc)
            payment.status = MandateStatus.REJECTED
            payment.error = SettlementError(
                error_code,
                f"Mediator attestation failed: {exc}",
                data={
                    "intent_mandate_id": intent.mandate_id,
                    "cart_mandate_id": cart.mandate_id,
                },
            ).to_dict()
            self._store.set_gateway_state("last_payment", payment.model_dump() if hasattr(payment, "model_dump") else payment.__dict__)
            return payment

        payment.attestation_id = attestation.attestation_id

        # ---- Cryptographic verification ------------------------------
        verification = verify_attestation(
            attestation, intent, cart,
            trusted_roots=self._trusted_roots,
        )
        self._store.set_gateway_state(
            "last_verification",
            {"valid": verification.valid, "errors": verification.errors},
        )

        if not verification.valid:
            logger.warning(
                "Attestation REJECTED — errors: %s",
                "; ".join(verification.errors),
            )
            payment.status = MandateStatus.REJECTED
            payment.error = SettlementError(
                SettlementErrorCode.VERIFICATION_FAILED,
                "; ".join(verification.errors),
                data={
                    "attestation_id": attestation.attestation_id,
                    "verification_errors": verification.errors,
                },
            ).to_dict()
            self._store.set_gateway_state("last_payment", payment.model_dump() if hasattr(payment, "model_dump") else payment.__dict__)

            if self._on_payment_rejected:
                self._on_payment_rejected(payment, verification)

            return payment

        # ---- Release Payment Mandate ---------------------------------
        payment.status = MandateStatus.RELEASED
        payment.released_at = time.time()
        self._store.set_gateway_state("last_payment", payment.model_dump() if hasattr(payment, "model_dump") else payment.__dict__)

        logger.info(
            "Payment Mandate RELEASED: id=%s attestation=%s tokens=%d",
            payment.mandate_id,
            attestation.attestation_id,
            payment.total_tokens,
        )

        if self._on_payment_released:
            self._on_payment_released(payment)

        self.interceptors.clear()
        return payment

    # ------------------------------------------------------------------
    # Synchronous convenience for non-callback usage
    # ------------------------------------------------------------------

    def attest_and_release(
        self,
        intent: IntentMandate,
        cart: CartMandate,
    ) -> tuple[PaymentMandate, VerificationResult | None]:
        """
        One-shot: send mandates to Mediator, verify, and return both
        the payment mandate and the verification result.
        """
        payment = self.process_mandates(intent, cart)
        raw = self._store.get_gateway_state("last_verification")
        verification = (
            VerificationResult(valid=raw["valid"], errors=raw.get("errors"))
            if raw else None
        )
        return payment, verification

    # ------------------------------------------------------------------
    # Internal callback wired to MandateInterceptors.on_mandates_ready
    # ------------------------------------------------------------------

    def _on_mandates_ready(
        self,
        intent: IntentMandate,
        cart: CartMandate,
    ) -> None:
        """Automatically process mandates when both are extracted."""
        self.process_mandates(intent, cart)

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    @property
    def last_verification(self) -> VerificationResult | None:
        raw = self._store.get_gateway_state("last_verification")
        if raw:
            return VerificationResult(valid=raw["valid"], errors=raw.get("errors"))
        return None

    @property
    def last_payment(self) -> PaymentMandate | None:
        raw = self._store.get_gateway_state("last_payment")
        if raw and isinstance(raw, dict):
            try:
                return PaymentMandate(**{k: v for k, v in raw.items() if k != "error"})
            except Exception:
                return None
        return None
