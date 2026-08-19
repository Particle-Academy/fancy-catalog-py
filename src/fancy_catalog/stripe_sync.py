"""``StripeCatalogSync`` -- the port of PHP ``StripeCatalogService``.

Syncs products and prices to Stripe through an injected client. The semantics
that matter, and that both twins share:

* **A product is created once and updated thereafter**, keyed by ``external_id``.
* **A price is immutable.** If anything Stripe considers part of the price
  changed, the old price is archived (``active: false``) and a new one created;
  the shared internal ULID rides in ``metadata.price_id`` so the archived price
  and its replacement stay linked.
* **A lookup key is transferred, not re-created.** Without
  ``transfer_lookup_key`` the archive-and-replace above fails with "lookup key
  already exists" the first time anyone changes the price of something that has
  one.

The change detection is the load-bearing part: a field it forgets to compare is
a price that never gets replaced, which means a customer keeps paying the old
amount and nothing anywhere reports it.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, Protocol

from .stores import PriceStore, ProductStore
from .stripe_client import StripeLike, stripe_get
from .types import ConnectionTestResult, Price, Product

__all__ = ["CatalogLogger", "StripeCatalogSync"]


class CatalogLogger(Protocol):
    """A logging sink. Defaults to a no-op; the PHP twin used Laravel's ``Log``."""

    def error(self, message: str, context: Mapping[str, Any] | None = None) -> None: ...


class _NoopLogger:
    def error(self, message: str, context: Mapping[str, Any] | None = None) -> None:
        return None


class StripeCatalogSync:
    """Push products and prices into Stripe."""

    def __init__(
        self,
        *,
        stripe: StripeLike,
        products: ProductStore,
        prices: PriceStore,
        logger: CatalogLogger | None = None,
    ) -> None:
        self._stripe = stripe
        self._products = products
        self._prices = prices
        self._logger: CatalogLogger = logger or _NoopLogger()

    # -- Products ----------------------------------------------------------

    def sync_product(self, product: Product) -> Product:
        """Create or update the Stripe product and capture its id into ``external_id``."""
        try:
            params: dict[str, Any] = {
                "name": product.name,
                "active": product.active,
                "metadata": {
                    **_stringify_metadata(product.metadata),
                    "product_id": product.id,
                    # Stripe products have no native lookup key, so it lives in
                    # metadata. Prices DO have one -- see `_build_price_params`,
                    # where the difference matters.
                    "product_lookup_key": product.lookup_key or "",
                },
            }
            if product.description:
                params["description"] = product.description
            if product.statement_descriptor:
                params["statement_descriptor"] = product.statement_descriptor
            if product.unit_label:
                params["unit_label"] = product.unit_label
            if product.images:
                params["images"] = list(product.images)

            if product.external_id:
                self._stripe.products.update(product.external_id, params)
            else:
                created = self._stripe.products.create(params)
                product.external_id = stripe_get(created, "id")
                self._products.save(product)

            return product
        except Exception as exc:
            self._logger.error(
                "Stripe product sync failed", {"product_id": product.id, "error": str(exc)}
            )
            raise

    # -- Prices ------------------------------------------------------------

    def sync_price(self, price: Price) -> Price:
        """Create, update, or archive-and-replace the Stripe price."""
        try:
            product = self._products.find(price.product_id)
            if product is None:
                raise LookupError(
                    f"Price {price.id} references product {price.product_id}, which is not in "
                    "the store. Sync the product first."
                )
            if not product.external_id:
                self.sync_product(product)

            params = self._build_price_params(price, product)

            if not price.external_id:
                self._create_price(price, params)
                return price

            try:
                existing = self._stripe.prices.retrieve(price.external_id)
            except Exception:
                # The price is gone from Stripe (deleted test data, a restored
                # database, a different account). Recreate rather than fail.
                self._create_price(price, params)
                return price

            if self._pricing_changed(existing, price, params):
                # Prices are immutable: archive, then create the replacement.
                self._stripe.prices.update(price.external_id, {"active": False})
                self._create_price(price, params)
                return price

            updates: dict[str, Any] = {"active": price.active, "metadata": params["metadata"]}
            # Changing ONLY the lookup key leaves pricing untouched, so it lands
            # here rather than in the archive-and-replace branch. Omitting it
            # makes that edit a silent no-op: saved locally, never sent, and a
            # lookup by the new key finds nothing.
            if price.lookup_key:
                updates["lookup_key"] = price.lookup_key
                updates["transfer_lookup_key"] = True
            self._stripe.prices.update(price.external_id, updates)
            return price
        except Exception as exc:
            self._logger.error(
                "Stripe price sync failed", {"price_id": price.id, "error": str(exc)}
            )
            raise

    def sync_product_and_prices(self, product: Product) -> Product:
        """Sync a product and every one of its live prices."""
        self.sync_product(product)
        for price in self._prices.for_product(product.id):
            self.sync_price(price)
        return self._products.find(product.id) or product

    def test_connection(self) -> ConnectionTestResult:
        """List a few products to prove the credentials work.

        On failure the message is deliberately generic. A raw Stripe error can
        carry account identifiers and key prefixes, and this result is built for
        an admin screen.
        """
        try:
            products = self._stripe.products.list({"limit": 10})
            count = len(list(stripe_get(products, "data", []) or []))
            return ConnectionTestResult(
                success=True,
                message=(
                    f"Success! Connected to Stripe. Found {count} product(s) in your "
                    "Stripe account."
                ),
                product_count=count,
            )
        except Exception as exc:
            self._logger.error("catalog.stripe.test_connection_failed", {"message": str(exc)})
            return ConnectionTestResult(
                success=False,
                message="Could not reach Stripe. Check your API credentials and try again.",
            )

    # -- Internals ---------------------------------------------------------

    def _create_price(self, price: Price, params: Mapping[str, Any]) -> None:
        created = self._stripe.prices.create(dict(params))
        price.external_id = stripe_get(created, "id")
        self._prices.save(price)

    def _build_price_params(self, price: Price, product: Product) -> dict[str, Any]:
        params: dict[str, Any] = {
            "product": product.external_id,
            "currency": price.currency.lower(),
            "active": price.active,
            "metadata": {
                **_stringify_metadata(price.metadata),
                # The shared internal ULID, linking an archived price to the one
                # that replaced it.
                "price_id": price.id,
                "product_id": price.product_id,
                "lookup_key": price.lookup_key or "",
            },
        }

        # Sent only when there IS one. Stripe sets no unit amount on a `tiered`
        # or `custom_unit_amount` price -- the tiers carry the money -- and
        # passing `unit_amount` alongside `tiers` is an API error. Passing 0
        # instead would be worse: a free price, silently.
        if price.unit_amount is not None:
            params["unit_amount"] = price.unit_amount

        # Stripe prices support `lookup_key` NATIVELY, and the metadata copy
        # above is not a substitute: `prices.list(lookup_keys=[...])` reads only
        # the real field, which is the entire reason a lookup key exists. Sent
        # only when set -- passing None would clear a key already on the price.
        if price.lookup_key:
            params["lookup_key"] = price.lookup_key
            params["transfer_lookup_key"] = True

        if price.billing_scheme:
            params["billing_scheme"] = price.billing_scheme
        if price.billing_scheme == "tiered" and price.tiers:
            params["tiers"] = price.tiers
            if price.tiers_mode:
                params["tiers_mode"] = price.tiers_mode
        if price.transform_quantity:
            params["transform_quantity"] = price.transform_quantity
        if price.custom_unit_amount:
            params["custom_unit_amount"] = price.custom_unit_amount

        if price.is_recurring:
            recurring: dict[str, Any] = {
                "interval": price.recurring_interval or "month",
                "interval_count": price.recurring_interval_count or 1,
                "usage_type": (
                    "metered" if price.pricing_model == "usage_recurring" else "licensed"
                ),
            }
            if price.recurring_trial_period_days:
                recurring["trial_period_days"] = price.recurring_trial_period_days
            params["recurring"] = recurring

        return params

    def _pricing_changed(self, existing: Any, price: Price, params: Mapping[str, Any]) -> bool:
        """Whether Stripe would need a NEW price rather than an update.

        Mirrors the PHP comparison field for field: unit_amount, currency, the
        recurring interval / count / usage type, billing scheme, tiers mode,
        tiers, transform_quantity and custom_unit_amount.
        """
        if not same_amount(stripe_get(existing, "unit_amount"), price.unit_amount):
            return True
        if stripe_get(existing, "currency") != price.currency.lower():
            return True

        if price.is_recurring:
            desired = params.get("recurring") or {}
            current = stripe_get(existing, "recurring") or {}
            if stripe_get(current, "interval") != desired.get("interval"):
                return True
            if stripe_get(current, "interval_count") != desired.get("interval_count", 1):
                return True
            if (stripe_get(current, "usage_type") or "licensed") != desired.get(
                "usage_type", "licensed"
            ):
                return True

        if (stripe_get(existing, "billing_scheme") or "per_unit") != params.get(
            "billing_scheme", "per_unit"
        ):
            return True
        if stripe_get(existing, "tiers_mode") != params.get("tiers_mode"):
            return True

        for field, empty in (
            ("tiers", []),
            ("transform_quantity", {}),
            ("custom_unit_amount", {}),
        ):
            if _canonical(stripe_get(existing, field) or empty) != _canonical(
                params.get(field) or empty
            ):
                return True

        return False


def _canonical(value: Any) -> str:
    """A stable string for comparing two nested structures.

    Keys are SORTED, which the twins' ``json_encode`` / ``JSON.stringify``
    comparisons are not: those compare insertion order, so the same tier list
    arriving from Stripe with its keys in a different order reads as a change
    and archives a price that did not need replacing. Cheap to get right here,
    and a silent, billable mistake to get wrong.
    """
    return json.dumps(_plain(value), sort_keys=True, default=str)


def _plain(value: Any) -> Any:
    """Reduce Stripe objects (dict-like or attribute-like) to plain data."""
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_plain(v) for v in value]
    to_dict = getattr(value, "to_dict_recursive", None) or getattr(value, "to_dict", None)
    if callable(to_dict):
        return _plain(to_dict())
    return value


def _stringify_metadata(metadata: Mapping[str, Any] | None) -> dict[str, str]:
    """Stripe metadata values must be strings; coerce whatever the host stored.

    ``None`` values are DROPPED rather than sent as ``"None"``. Stripe treats an
    empty-string metadata value as a deletion, and the string ``"None"`` as a
    literal -- neither is what a null field means.
    """
    if not metadata:
        return {}
    out: dict[str, str] = {}
    for key, value in metadata.items():
        if value is None:
            continue
        out[key] = value if isinstance(value, str) else json.dumps(value, sort_keys=True)
    return out


def same_amount(a: Any, b: Any) -> bool:
    """Do two unit amounts mean the same money?

    ``None`` is a real value here -- a tiered or custom-amount price has no unit
    amount and Stripe returns null for it -- so ``None`` and ``0`` must compare
    as **different**: one means "the tiers carry the money", the other means
    free.

    Everything non-null is compared as an integer. The two sides come from
    different places: a Stripe SDK object hands back an int, a recorded cassette
    or a JSON fixture may hand back a string. Prices are immutable, so a false
    difference archives a live price and creates a replacement -- a churned id
    and orphaned references, silently.
    """
    if a is None or b is None:
        return a is None and b is None
    return int(a) == int(b)
