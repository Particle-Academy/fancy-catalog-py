"""Persistence adapters.

The PHP package is Eloquent-coupled; this port hides storage behind small
protocols with in-memory defaults, so it runs under Django, SQLAlchemy, a raw
driver, or nothing at all.

Soft-delete semantics mirror the PHP ``SoftDeletes`` trait: ``find`` and ``all``
exclude rows with a ``deleted_at`` unless ``with_trashed=True``, and ``remove``
sets the timestamp rather than dropping the row. Financial history is the reason
-- an invoice referencing a hard-deleted product is an invoice nobody can
explain.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from .types import (
    Price,
    Product,
    ProductFeature,
    ProductFeatureConfig,
    ProductFeatureWithConfig,
)

__all__ = [
    "InMemoryPriceStore",
    "InMemoryProductFeatureStore",
    "InMemoryProductStore",
    "PriceStore",
    "ProductFeatureStore",
    "ProductStore",
]


@runtime_checkable
class ProductStore(Protocol):
    def find(self, product_id: str, *, with_trashed: bool = False) -> Product | None: ...
    def all(self, *, with_trashed: bool = False) -> list[Product]: ...
    def save(self, product: Product) -> Product: ...
    def remove(self, product_id: str) -> None: ...


@runtime_checkable
class PriceStore(Protocol):
    def find(self, price_id: str, *, with_trashed: bool = False) -> Price | None: ...
    def for_product(self, product_id: str, *, with_trashed: bool = False) -> list[Price]: ...
    def all(self, *, with_trashed: bool = False) -> list[Price]: ...
    def save(self, price: Price) -> Price: ...
    def remove(self, price_id: str) -> None: ...


@runtime_checkable
class ProductFeatureStore(Protocol):
    def find(self, feature_id: str) -> ProductFeature | None: ...
    def find_by_key(self, key: str) -> ProductFeature | None: ...
    def all(self) -> list[ProductFeature]: ...
    def save(self, feature: ProductFeature) -> ProductFeature: ...
    def remove(self, feature_id: str) -> None: ...

    def for_product(self, product_id: str) -> list[ProductFeatureWithConfig]:
        """The pivot rows for a product, joined to their features.

        The PHP ``Product::productFeatures()`` belongsToMany-with-pivot, and what
        the feature bridge turns into ``FeatureGrant`` objects.
        """
        ...

    def set_config(self, config: ProductFeatureConfig) -> ProductFeatureConfig: ...
    def configs_for_product(self, product_id: str) -> list[ProductFeatureConfig]: ...


def _trashed(row: Product | Price) -> bool:
    return row.deleted_at is not None


def _now() -> datetime:
    # Timezone-aware, always. A naive timestamp compared against an aware one
    # raises, and the comparison that matters here (`updated_at > last_synced_at`
    # in the out-of-sync check) is exactly where it would.
    return datetime.now(UTC)


class InMemoryProductStore:
    """The default :class:`ProductStore`. Holds the objects themselves, not copies."""

    def __init__(self, seed: Iterable[Product] = ()) -> None:
        self._rows: dict[str, Product] = {p.id: p for p in seed}

    def find(self, product_id: str, *, with_trashed: bool = False) -> Product | None:
        row = self._rows.get(product_id)
        if row is None or (not with_trashed and _trashed(row)):
            return None
        return row

    def all(self, *, with_trashed: bool = False) -> list[Product]:
        rows = list(self._rows.values())
        return rows if with_trashed else [r for r in rows if not _trashed(r)]

    def save(self, product: Product) -> Product:
        self._rows[product.id] = product
        return product

    def remove(self, product_id: str) -> None:
        row = self._rows.get(product_id)
        if row is not None:
            row.deleted_at = _now()


class InMemoryPriceStore:
    """The default :class:`PriceStore`."""

    def __init__(self, seed: Iterable[Price] = ()) -> None:
        self._rows: dict[str, Price] = {p.id: p for p in seed}

    def find(self, price_id: str, *, with_trashed: bool = False) -> Price | None:
        row = self._rows.get(price_id)
        if row is None or (not with_trashed and _trashed(row)):
            return None
        return row

    def for_product(self, product_id: str, *, with_trashed: bool = False) -> list[Price]:
        return [
            r
            for r in self._rows.values()
            if r.product_id == product_id and (with_trashed or not _trashed(r))
        ]

    def all(self, *, with_trashed: bool = False) -> list[Price]:
        rows = list(self._rows.values())
        return rows if with_trashed else [r for r in rows if not _trashed(r)]

    def save(self, price: Price) -> Price:
        self._rows[price.id] = price
        return price

    def remove(self, price_id: str) -> None:
        row = self._rows.get(price_id)
        if row is not None:
            row.deleted_at = _now()


class InMemoryProductFeatureStore:
    """The default :class:`ProductFeatureStore`, features and pivot rows together."""

    def __init__(
        self,
        features: Iterable[ProductFeature] = (),
        configs: Iterable[ProductFeatureConfig] = (),
    ) -> None:
        self._features: dict[str, ProductFeature] = {f.id: f for f in features}
        self._configs: dict[str, ProductFeatureConfig] = {c.id: c for c in configs}

    def find(self, feature_id: str) -> ProductFeature | None:
        return self._features.get(feature_id)

    def find_by_key(self, key: str) -> ProductFeature | None:
        return next((f for f in self._features.values() if f.key == key), None)

    def all(self) -> list[ProductFeature]:
        return list(self._features.values())

    def save(self, feature: ProductFeature) -> ProductFeature:
        self._features[feature.id] = feature
        return feature

    def remove(self, feature_id: str) -> None:
        # A hard delete, matching the PHP model: `product_features` has no
        # SoftDeletes and the pivot cascades on delete.
        self._features.pop(feature_id, None)
        for config_id in [
            c.id for c in self._configs.values() if c.product_feature_id == feature_id
        ]:
            del self._configs[config_id]

    def for_product(self, product_id: str) -> list[ProductFeatureWithConfig]:
        out: list[ProductFeatureWithConfig] = []
        for config in self._configs.values():
            if config.product_id != product_id:
                continue
            feature = self._features.get(config.product_feature_id)
            if feature is None:
                continue
            out.append(
                ProductFeatureWithConfig(
                    feature=feature,
                    enabled=config.enabled,
                    included_quantity=config.included_quantity,
                    overage_limit=config.overage_limit,
                    config=config.config,
                )
            )
        return out

    def set_config(self, config: ProductFeatureConfig) -> ProductFeatureConfig:
        # The schema's UNIQUE(product_id, product_feature_id) is enforced here so
        # the in-memory store cannot hold a state the database would reject --
        # otherwise a test passes and the same code fails on a real schema.
        for existing in list(self._configs.values()):
            if (
                existing.product_id == config.product_id
                and existing.product_feature_id == config.product_feature_id
                and existing.id != config.id
            ):
                del self._configs[existing.id]
        self._configs[config.id] = config
        return config

    def configs_for_product(self, product_id: str) -> list[ProductFeatureConfig]:
        return [c for c in self._configs.values() if c.product_id == product_id]
