"""fancy-catalog -- a headless Stripe catalog: products, prices, plans, checkout.

The Python twin of ``particle-academy/laravel-catalog`` (PHP) and
``@particle-academy/fancy-catalog`` (Node/TypeScript).

Nothing here imports a web framework, an ORM, or the Stripe SDK. Persistence is
behind store protocols with in-memory defaults; the Stripe client is injected
through a narrow protocol (:mod:`fancy_catalog.stripe_client`); money is an
integer in the currency's minor unit and :mod:`fancy_catalog.money` is the only
place a decimal amount becomes one.

    >>> from fancy_catalog import create_catalog
    >>> catalog = create_catalog(stripe=stripe_client)
    >>> product = catalog.create_product("Pro plan")
    >>> price = catalog.create_price(product.id, currency="USD", amount="19.99")
    >>> price.unit_amount
    1999

``fancy_catalog.features`` exposes a catalog as a ``FeatureSource`` for
``fancy-features``. It is a separate import and a separate extra
(``pip install "fancy-catalog[features]"``) so a host that does not gate on
entitlements installs nothing extra -- and it **imports** the shared contract
rather than mirroring it. See that module's docstring for why.
"""

from __future__ import annotations

from .catalog import Catalog, create_catalog
from .live import CATALOG_LIVE, catalog_live_event_names
from .money import (
    THREE_DECIMAL_CURRENCIES,
    ZERO_DECIMAL_CURRENCIES,
    MoneyPrecisionError,
    currency_exponent,
    format_minor_units,
    line_total,
    to_minor_units,
)
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
    BillingScheme,
    ConnectionTestResult,
    Price,
    PriceType,
    PricingModel,
    Product,
    ProductFeature,
    ProductFeatureConfig,
    ProductFeatureType,
    ProductFeatureWithConfig,
    Subscription,
    TiersMode,
)
from .ulid import ulid

__version__ = "0.1.0"

__all__ = [
    # -- The catalog --
    "Catalog",
    "create_catalog",
    # -- Domain model --
    "BillingScheme",
    "ConnectionTestResult",
    "Price",
    "PriceType",
    "PricingModel",
    "Product",
    "ProductFeature",
    "ProductFeatureConfig",
    "ProductFeatureType",
    "ProductFeatureWithConfig",
    "Subscription",
    "TiersMode",
    # -- Money: integer minor units, never a float --
    "MoneyPrecisionError",
    "THREE_DECIMAL_CURRENCIES",
    "ZERO_DECIMAL_CURRENCIES",
    "currency_exponent",
    "format_minor_units",
    "line_total",
    "to_minor_units",
    # -- Persistence --
    "InMemoryPriceStore",
    "InMemoryProductFeatureStore",
    "InMemoryProductStore",
    "PriceStore",
    "ProductFeatureStore",
    "ProductStore",
    # -- Stripe --
    "CatalogLogger",
    "StripeCatalogSync",
    "StripeCheckout",
    "StripeLike",
    # -- Live contract --
    "CATALOG_LIVE",
    "catalog_live_event_names",
    # -- Ids --
    "ulid",
    "__version__",
]
