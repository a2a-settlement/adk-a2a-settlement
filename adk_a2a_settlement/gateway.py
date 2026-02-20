"""
gateway.py — The Edge Gateway: ADK ↔ A2A ↔ Mediator ↔ Payment Processor.

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
    ):
        self._config = config or SettlementConfig()
        self._trusted_roots = trusted_roots

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

        self._last_verification: VerificationResult | None = None
        self._last_payment: PaymentMandate | None = None

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
        the full attest → verify → release pipeline.

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

            mandates → mediator → verify → release / reject

        Returns the resulting Payment Mandate (check its ``status``).
        """
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
            logger.error("Mediator attestation failed: %s", exc)
            payment.status = MandateStatus.REJECTED
            self._last_payment = payment
            return payment

        payment.attestation_id = attestation.attestation_id

        # ---- Cryptographic verification ------------------------------
        verification = verify_attestation(
            attestation, intent, cart,
            trusted_roots=self._trusted_roots,
        )
        self._last_verification = verification

        if not verification.valid:
            logger.warning(
                "Attestation REJECTED — errors: %s",
                "; ".join(verification.errors),
            )
            payment.status = MandateStatus.REJECTED
            self._last_payment = payment

            if self._on_payment_rejected:
                self._on_payment_rejected(payment, verification)

            return payment

        # ---- Release Payment Mandate ---------------------------------
        payment.status = MandateStatus.RELEASED
        payment.released_at = time.time()
        self._last_payment = payment

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
        return payment, self._last_verification

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
        return self._last_verification

    @property
    def last_payment(self) -> PaymentMandate | None:
        return self._last_payment
