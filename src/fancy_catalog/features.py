"""The integration bridge: a catalog, exposed as a ``FeatureSource``.

    pip install "fancy-catalog[features]"

## This module IMPORTS the contract. It does not mirror it.

That is a **deliberate divergence from the TypeScript pair**, where
``@particle-academy/fancy-catalog/features`` re-declares ``FeatureType``,
``FeatureGrant`` and ``FeatureSource`` verbatim and relies on TypeScript's
structural typing to keep the two copies assignable.

``fancy-conformance``'s own README names that pair as the case that "survives
only because TypeScript's structural typing does the checking -- a mechanism
that does not exist in Rust or Go". It does not meaningfully exist across Python
distributions either: :class:`typing.Protocol` would make the *shape* check, but
nothing would check that ``FeatureGrant``'s **fields** still match, and a
dataclass copied by hand is exactly the thing that drifts. So there is one
definition, in ``fancy_features.contract``, and this module imports it.

The cost is that the bridge needs ``fancy-features`` installed. That is the
point: a bridge between two packages is not usable with one of them missing, and
an ``ImportError`` naming the extra is a better outcome than a second contract.

**``fancy_catalog`` itself never imports this module**, so a host using the
catalog without any gating layer installs nothing extra.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

try:
    from fancy_features import FeatureGrant, FeatureSource, Subject
except ModuleNotFoundError as exc:  # pragma: no cover - exercised in test_features_bridge
    raise ModuleNotFoundError(
        "fancy_catalog.features needs the shared feature contract, which lives in "
        "fancy-features. Install it with: pip install 'fancy-catalog[features]'. This module "
        "imports the contract rather than mirroring it, deliberately -- see its docstring."
    ) from exc

from .catalog import Catalog
from .types import Subscription

__all__ = ["CatalogFeatureSource", "create_catalog_feature_source"]

#: ``(subject, context) -> Subscription | None``, possibly awaitable. The host
#: owns this: the catalog is storage-agnostic about who a subject is.
SubscriptionResolver = Callable[..., Subscription | Awaitable[Subscription | None] | None]


class CatalogFeatureSource:
    """Resolves a subject's entitlements from their subscription's product features.

    The replacement for the PHP ``Fms`` service's subscription -> product ->
    pivot walk, with the storage left to the host.

    ``grants_for`` returns a list when the host's resolver is synchronous and a
    coroutine when it is not. That is the contract's own rule -- a
    ``FeatureSource`` may return an awaitable -- and it is why there is no
    separate async method: ``FeatureManager`` only ever calls ``grants_for``, so
    an ``agrants_for`` would be a method nothing invokes, and a host with an
    async resolver would silently get no grants at all.
    """

    #: For ``explain()``, e.g. ``source:catalog``.
    name = "catalog"

    def __init__(self, catalog: Catalog, *, resolve_subscription: SubscriptionResolver) -> None:
        self._catalog = catalog
        self._resolve = resolve_subscription

    def grants_for(
        self, subject: Subject, context: Any = None
    ) -> Sequence[FeatureGrant] | Awaitable[Sequence[FeatureGrant]]:
        subscription = self._resolve(subject, context)
        if inspect.isawaitable(subscription):
            return _PendingGrants(subscription, self._grants)
        return self._grants(subscription)

    def _grants(self, subscription: Subscription | None) -> list[FeatureGrant]:
        if subscription is None:
            return []
        product = self._catalog.products.find(subscription.product_id)
        if product is None:
            return []
        return [
            FeatureGrant(
                key=row.feature.key,
                type=row.feature.type,
                enabled=row.enabled,
                included_quantity=row.included_quantity,
                overage_limit=row.overage_limit,
                source=f"catalog:{product.id}",
                config=row.config,
            )
            for row in self._catalog.product_features.for_product(product.id)
        ]


class _PendingGrants:
    """The resolver's pending subscription, wrapped so it can be CLOSED unawaited.

    ``FeatureManager``'s synchronous driver refuses an awaitable and closes it,
    which is right -- but a plain ``async def`` wrapper here would close only the
    wrapper, leaving the host's own coroutine un-awaited. Python then emits a
    ``RuntimeWarning`` from the garbage collector, detached from the error that
    caused it, and a host running with warnings-as-errors gets a second failure
    it cannot trace. Closing forwards to the coroutine that actually exists.
    """

    __slots__ = ("_build", "_pending")

    def __init__(
        self,
        pending: Awaitable[Subscription | None],
        build: Callable[[Subscription | None], list[FeatureGrant]],
    ) -> None:
        self._pending = pending
        self._build = build

    def __await__(self) -> Any:
        return self._resolve().__await__()

    async def _resolve(self) -> list[FeatureGrant]:
        return self._build(await self._pending)

    def close(self) -> None:
        close = getattr(self._pending, "close", None)
        if callable(close):
            close()


def create_catalog_feature_source(
    catalog: Catalog, *, resolve_subscription: SubscriptionResolver
) -> FeatureSource:
    """Build a :class:`CatalogFeatureSource`.

    >>> features = create_features(
    ...     sources=[create_catalog_feature_source(catalog, resolve_subscription=lookup)]
    ... )
    >>> features.can_access("use-mcp", user)
    """
    return CatalogFeatureSource(catalog, resolve_subscription=resolve_subscription)
