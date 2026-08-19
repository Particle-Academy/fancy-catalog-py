"""The catalog domain model.

The headless mirror of ``laravel-catalog``'s Eloquent models (``Product`` /
``Price`` / ``ProductFeature``) and the ``product_feature_configs`` pivot.
Fields and nullability match the migrations:

* ``2024_01_01_000001_create_products_table``
* ``2024_01_01_000002_create_prices_table``
* ``2024_01_01_000003_create_product_features_table``
* ``2024_01_01_000004_create_product_feature_configs_table``
* ``2026_07_27_000001_add_lookup_key_to_catalog_tables``

**Money is an integer in the currency's minor unit**, mirroring Stripe and the
PHP ``unit_amount``. Never a float, and :mod:`fancy_catalog.money` is the only
place a decimal amount becomes one.

Ids are strings; the PHP models use ULIDs and :func:`fancy_catalog.ulid.ulid`
generates the same 26-character Crockford-base32 form.

These are mutable dataclasses on purpose. Syncing a price to Stripe writes the
returned id back onto it, exactly as both twins do; a frozen model would force
every caller to rebuild the object and would make a partially-synced batch
harder to reason about, not easier.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

__all__ = [
    "BillingScheme",
    "ConnectionTestResult",
    "CustomUnitAmount",
    "Price",
    "PriceTier",
    "PriceType",
    "PricingModel",
    "Product",
    "ProductFeature",
    "ProductFeatureConfig",
    "ProductFeatureType",
    "ProductFeatureWithConfig",
    "Subscription",
    "TiersMode",
    "TransformQuantity",
]

#: The PHP ``Price::TYPE_*`` constants.
PriceType = Literal["recurring", "one_time"]

#: The PHP ``Price::PRICING_MODEL_*`` constants.
PricingModel = Literal[
    "flat_recurring",
    "per_seat_recurring",
    "tiered_recurring",
    "usage_recurring",
    "flat_one_time",
    "package_one_time",
    "customer_choice_one_time",
]

#: The PHP ``product_features.type`` column.
ProductFeatureType = Literal["boolean", "resource"]

BillingScheme = Literal["per_unit", "tiered"]
TiersMode = Literal["graduated", "volume"]

#: A Stripe price tier, passed through verbatim.
PriceTier = dict[str, Any]
#: Stripe ``transform_quantity``: ``{"divide_by": int, "round": "up" | "down"}``.
TransformQuantity = dict[str, Any]
#: Stripe ``custom_unit_amount``.
CustomUnitAmount = dict[str, Any]


@dataclass(slots=True)
class Product:
    """Mirrors Stripe's Product and the PHP ``products`` table.

    ``deleted_at`` is the soft-delete timestamp. Soft deletes exist to preserve
    financial history -- an invoice referencing a hard-deleted product is an
    invoice nobody can explain.
    """

    id: str
    name: str
    description: str | None = None
    active: bool = True
    #: Image URLs (the ``images`` json column).
    images: list[str] | None = None
    metadata: dict[str, Any] | None = None
    statement_descriptor: str | None = None
    unit_label: str | None = None
    #: The Stripe product id (the PHP ``external_id``).
    external_id: str | None = None
    #: A stable, human-readable identifier. Unique in the schema.
    lookup_key: str | None = None
    order: int = 0
    last_synced_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    deleted_at: datetime | None = None


@dataclass(slots=True)
class Price:
    """Mirrors Stripe's Price and the PHP ``prices`` table.

    **Stripe prices are immutable.** A changed amount does not update a price,
    it archives the old one and creates a new one -- which is why ``id`` (a
    ULID this package owns) and ``external_id`` (Stripe's) are different fields
    and only the second one changes.
    """

    id: str
    product_id: str
    currency: str
    #: The amount in whole minor units. See :mod:`fancy_catalog.money`.
    unit_amount: int
    type: PriceType = "recurring"
    active: bool = True
    pricing_model: PricingModel | None = None

    # Recurring fields; all None for a one-time price.
    recurring_interval: str | None = None
    recurring_interval_count: int | None = None
    recurring_trial_period_days: int | None = None

    # Advanced Stripe pricing.
    billing_scheme: BillingScheme | None = None
    tiers: list[PriceTier] | None = None
    tiers_mode: TiersMode | None = None
    transform_quantity: TransformQuantity | None = None
    custom_unit_amount: CustomUnitAmount | None = None

    nickname: str | None = None
    lookup_key: str | None = None
    metadata: dict[str, Any] | None = None
    #: The Stripe price id (the PHP ``external_id``).
    external_id: str | None = None
    order: int = 0
    last_synced_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    deleted_at: datetime | None = None

    @property
    def is_recurring(self) -> bool:
        return self.type == "recurring"

    @property
    def is_one_time(self) -> bool:
        return self.type == "one_time"


@dataclass(slots=True)
class ProductFeature:
    """The catalog of billable features -- the PHP ``product_features`` table.

    ``key`` is unique and is the join to
    :class:`fancy_features.contract.Feature`: a ``ProductFeature`` here and a
    registered feature there are the same feature seen from the billing side
    and the gating side.
    """

    id: str
    key: str
    name: str
    description: str | None = None
    type: ProductFeatureType = "boolean"
    config: dict[str, Any] | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(slots=True)
class ProductFeatureConfig:
    """The ``product_feature_configs`` pivot: one feature resolved for one product.

    ``included_quantity`` is the per-period quota for a resource feature.
    **``None`` means unlimited**, per the shared contract -- and the PHP ``Fms``
    service reads the same nullable column as "deny". See
    ``.ai/plans/fancy-python-commerce-gating.md``.
    """

    id: str
    product_id: str
    product_feature_id: str
    enabled: bool = False
    included_quantity: int | None = None
    #: A soft cap before blocking. Stored by every runtime and **read by none**
    #: -- see the plan; it is a declared field with no behaviour behind it.
    overage_limit: int | None = None
    config: dict[str, Any] | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(slots=True)
class ProductFeatureWithConfig:
    """A pivot row joined to its feature -- what ``product_features.for_product`` returns."""

    feature: ProductFeature
    enabled: bool = False
    included_quantity: int | None = None
    overage_limit: int | None = None
    config: dict[str, Any] | None = None


@dataclass(slots=True)
class Subscription:
    """The minimal subscription shape the feature bridge needs.

    This package is storage-agnostic about subjects and subscriptions; the host
    resolves a subject to one of these. Deliberately not a full billing model --
    a catalog that owned subscriptions would be a billing system, and Cashier
    (PHP) or the host's own tables already are one.
    """

    id: str
    product_id: str
    status: str | None = None
    renews_at: datetime | None = None


@dataclass(slots=True)
class ConnectionTestResult:
    """The result of :meth:`fancy_catalog.Catalog.test_connection`."""

    success: bool
    message: str
    product_count: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)
