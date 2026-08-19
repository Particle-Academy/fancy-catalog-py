"""``StripeCheckout`` -- the port of PHP ``StripeCheckoutService``.

Builds Stripe Checkout sessions. The PHP twin goes through Cashier's
``Checkout::create($owner, ...)``, where ``$owner`` is a Billable model; this
port takes the **Stripe customer id** the host resolved, exactly as the Node
twin does. A catalog that knew how to turn a user into a customer would be a
billing system, and the host already has one.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .stripe_client import StripeLike, stripe_get
from .types import Price

__all__ = ["StripeCheckout"]


class StripeCheckout:
    """Create Checkout sessions for a subscription or a one-off purchase."""

    def __init__(self, stripe: StripeLike) -> None:
        self._stripe = stripe

    def subscription_checkout(
        self,
        price: Price,
        *,
        success_url: str,
        cancel_url: str,
        customer: str | None = None,
        metadata: Mapping[str, str] | None = None,
        quantity: int = 1,
    ) -> Any:
        """A Checkout session for a recurring price.

        ``customer`` is optional: without it Stripe collects a new customer on
        the hosted page, which is the right behaviour for a signed-out
        storefront.
        """
        stripe_price_id = self._require_synced(price)
        if not price.is_recurring:
            raise ValueError(
                f"Price {price.id} is {price.type}, so it cannot open a subscription checkout. "
                "Use one_time_checkout()."
            )

        params: dict[str, Any] = {
            "mode": "subscription",
            "line_items": [{"price": stripe_price_id, "quantity": quantity}],
            "success_url": success_url,
            "cancel_url": cancel_url,
            "subscription_data": {
                "metadata": {
                    "price_id": str(price.id),
                    "product_id": str(price.product_id),
                    **dict(metadata or {}),
                }
            },
        }
        if customer:
            params["customer"] = customer
        if price.recurring_trial_period_days:
            params["subscription_data"]["trial_period_days"] = price.recurring_trial_period_days

        return self._stripe.checkout.sessions.create(params)

    def one_time_checkout(
        self,
        price: Price,
        *,
        quantity: int,
        success_url: str,
        cancel_url: str,
        customer: str | None = None,
        metadata: Mapping[str, str] | None = None,
    ) -> Any:
        """A Checkout session for a one-time price (an add-on purchase)."""
        stripe_price_id = self._require_synced(price)
        if not price.is_one_time:
            raise ValueError(
                f"Price {price.id} is {price.type}, so it cannot open a one-time checkout. "
                "Use subscription_checkout()."
            )
        if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity < 1:
            raise ValueError(
                f"A one-time checkout needs a positive whole quantity; got {quantity!r}."
            )

        base = {"price_id": str(price.id), "product_id": str(price.product_id)}
        params: dict[str, Any] = {
            "mode": "payment",
            "line_items": [{"price": stripe_price_id, "quantity": quantity}],
            "success_url": success_url,
            "cancel_url": cancel_url,
            "payment_intent_data": {"metadata": {**base, **dict(metadata or {})}},
            # The invoice is what makes a one-off purchase auditable next to a
            # subscription's invoices. Both twins enable it unconditionally.
            "invoice_creation": {"enabled": True, "invoice_data": {"metadata": dict(base)}},
        }
        if customer:
            params["customer"] = customer

        return self._stripe.checkout.sessions.create(params)

    def subscription_checkout_url(self, price: Price, **kwargs: Any) -> str:
        return stripe_get(self.subscription_checkout(price, **kwargs), "url") or ""

    def one_time_checkout_url(self, price: Price, **kwargs: Any) -> str:
        return stripe_get(self.one_time_checkout(price, **kwargs), "url") or ""

    @staticmethod
    def _require_synced(price: Price) -> str:
        if not price.external_id:
            raise ValueError(
                f"Price {price.id} has no Stripe price id. Sync the price to Stripe first -- "
                "a checkout session cannot reference a price that does not exist there."
            )
        return price.external_id
