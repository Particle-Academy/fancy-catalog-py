"""``Catalog`` -- the unified surface, the port of PHP ``CatalogManager``.

Stores plus Stripe sync plus checkout, with terse authoring helpers on top.
Stripe is injected; persistence is behind the store protocols; nothing here
knows what a request or a user is.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any

from .money import currency_exponent, to_minor_units
from .stores import (
    InMemoryPriceStore,
    InMemoryProductFeatureStore,
    InMemoryProductStore,
    PriceStore,
    ProductFeatureStore,
    ProductStore,
)
from .stripe_checkout import StripeCheckout
from .stripe_client import StripeLike
from .stripe_sync import CatalogLogger, StripeCatalogSync
from .types import (
    ConnectionTestResult,
    Price,
    PriceType,
    Product,
    ProductFeature,
    ProductFeatureConfig,
    ProductFeatureType,
)
from .ulid import ulid

__all__ = ["Catalog", "create_catalog"]


def _now() -> datetime:
    return datetime.now(UTC)


class Catalog:
    """Products, prices, features, Stripe sync and checkout in one place."""

    def __init__(
        self,
        *,
        stripe: StripeLike,
        products: ProductStore | None = None,
        prices: PriceStore | None = None,
        product_features: ProductFeatureStore | None = None,
        logger: CatalogLogger | None = None,
        on_product_synced: Callable[[str], None] | None = None,
    ) -> None:
        self.products: ProductStore = products or InMemoryProductStore()
        self.prices: PriceStore = prices or InMemoryPriceStore()
        self.product_features: ProductFeatureStore = (
            product_features or InMemoryProductFeatureStore()
        )
        self._on_product_synced = on_product_synced

        self._sync = StripeCatalogSync(
            stripe=stripe, products=self.products, prices=self.prices, logger=logger
        )
        self._checkout = StripeCheckout(stripe)

    # -- Stripe sync -------------------------------------------------------

    def sync_product(self, product: Product) -> Product:
        return self._sync.sync_product(product)

    def sync_price(self, price: Price) -> Price:
        return self._sync.sync_price(price)

    def sync_product_and_prices(self, product: Product) -> Product:
        """Sync a product and its prices, stamp ``last_synced_at``, fire the hook.

        The stamp is what ``is_out_of_sync`` compares against, so it lands here
        rather than inside the sync layer -- a partially-synced product must not
        claim to be current.
        """
        result = self._sync.sync_product_and_prices(product)
        stamped_at = _now()
        result.last_synced_at = stamped_at
        self.products.save(result)
        for price in self.prices.for_product(result.id):
            price.last_synced_at = stamped_at
            self.prices.save(price)
        if self._on_product_synced is not None:
            self._on_product_synced(result.id)
        return result

    def test_connection(self) -> ConnectionTestResult:
        return self._sync.test_connection()

    def is_out_of_sync(self, product: Product) -> bool:
        """Whether the product or any of its prices has changed since its last sync.

        The port of the PHP ``Product::isOutOfSync``, used to surface an
        "out of sync" warning in an admin UI.
        """
        if product.last_synced_at is None:
            return True
        if product.updated_at is not None and product.updated_at > product.last_synced_at:
            return True
        return any(
            price.last_synced_at is None
            or (price.updated_at is not None and price.updated_at > price.last_synced_at)
            for price in self.prices.for_product(product.id)
        )

    # -- Checkout ----------------------------------------------------------

    def subscription_checkout(self, price: Price, **kwargs: Any) -> Any:
        return self._checkout.subscription_checkout(price, **kwargs)

    def one_time_checkout(self, price: Price, **kwargs: Any) -> Any:
        return self._checkout.one_time_checkout(price, **kwargs)

    def subscription_checkout_url(self, price: Price, **kwargs: Any) -> str:
        return self._checkout.subscription_checkout_url(price, **kwargs)

    def one_time_checkout_url(self, price: Price, **kwargs: Any) -> str:
        return self._checkout.one_time_checkout_url(price, **kwargs)

    # -- Authoring helpers -------------------------------------------------

    def create_product(self, name: str, **fields: Any) -> Product:
        product = Product(
            id=fields.pop("id", None) or ulid(),
            name=name,
            created_at=fields.pop("created_at", None) or _now(),
            updated_at=_now(),
            **fields,
        )
        return self.products.save(product)

    def create_price(
        self,
        product_id: str,
        *,
        currency: str,
        unit_amount: int | None = None,
        amount: str | None = None,
        type: PriceType = "recurring",
        **fields: Any,
    ) -> Price:
        """Create a price.

        Give **either** ``unit_amount`` (already whole minor units, the way both
        twins and Stripe store it) **or** ``amount`` (a decimal string, converted
        here through :func:`fancy_catalog.money.to_minor_units` using the
        currency's own exponent).

        ``amount`` exists because the conversion is where money gets lost and
        neither twin owns it, so every consumer writes ``int(price * 100)`` for
        themselves -- which is wrong for 19.99 and wrong for every JPY price.
        """
        if (unit_amount is None) == (amount is None):
            raise TypeError(
                "create_price takes exactly one of unit_amount (whole minor units) or "
                "amount (a decimal string). Passing both invites them to disagree; passing "
                "neither leaves the price with no amount at all."
            )
        if amount is not None:
            unit_amount = to_minor_units(amount, currency_exponent(currency))
        assert unit_amount is not None

        price = Price(
            id=fields.pop("id", None) or ulid(),
            product_id=product_id,
            currency=currency,
            unit_amount=unit_amount,
            type=type,
            created_at=fields.pop("created_at", None) or _now(),
            updated_at=_now(),
            **fields,
        )
        return self.prices.save(price)

    def create_product_feature(
        self,
        key: str,
        name: str,
        *,
        type: ProductFeatureType = "boolean",
        **fields: Any,
    ) -> ProductFeature:
        feature = ProductFeature(
            id=fields.pop("id", None) or ulid(),
            key=key,
            name=name,
            type=type,
            created_at=fields.pop("created_at", None) or _now(),
            updated_at=_now(),
            **fields,
        )
        return self.product_features.save(feature)

    def attach_feature(
        self,
        product_id: str,
        product_feature_id: str,
        *,
        enabled: bool = False,
        included_quantity: int | None = None,
        overage_limit: int | None = None,
        config: Mapping[str, Any] | None = None,
        id: str | None = None,
    ) -> ProductFeatureConfig:
        """Attach a feature to a product with its pivot data."""
        row = ProductFeatureConfig(
            id=id or ulid(),
            product_id=product_id,
            product_feature_id=product_feature_id,
            enabled=enabled,
            included_quantity=included_quantity,
            overage_limit=overage_limit,
            config=dict(config) if config is not None else None,
            created_at=_now(),
            updated_at=_now(),
        )
        return self.product_features.set_config(row)


def create_catalog(
    *,
    stripe: StripeLike,
    products: ProductStore | None = None,
    prices: PriceStore | None = None,
    product_features: ProductFeatureStore | None = None,
    logger: CatalogLogger | None = None,
    on_product_synced: Callable[[str], None] | None = None,
) -> Catalog:
    """Build a :class:`Catalog`. The factory mirror of ``createCatalog()``."""
    return Catalog(
        stripe=stripe,
        products=products,
        prices=prices,
        product_features=product_features,
        logger=logger,
        on_product_synced=on_product_synced,
    )
