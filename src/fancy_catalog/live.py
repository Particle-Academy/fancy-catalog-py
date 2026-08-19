"""The catalog Live Contract: which events this package broadcasts, and what they stale.

Pure data. A host that wants live behaviour wires these into its broadcaster and
its client cache; a host that does not pays nothing for the declaration being
here.

``LaravelCatalog\\LiveContract`` (PHP) and ``catalogLive``
(``@particle-academy/fancy-catalog``) declare the identical list, and each side
has a parity test. **That test is the whole point.** A mirror pair drifts when
only one side is edited, and this is the failure mode where drift is invisible:
rename an event and nothing throws -- the client subscribes to a name nobody
broadcasts, the cache is never invalidated, and the UI just stops updating.

Conventions, matching both twins:

* namespace -- the bare short name, no ``laravel-`` prefix;
* event -- ``<namespace>.<resource>.<verb>``;
* key -- ``[namespace, resource, ...]``. TanStack Query matches by PREFIX, so
  ``["catalog"]`` invalidates the whole namespace.
"""

from __future__ import annotations

from types import MappingProxyType

__all__ = ["CATALOG_LIVE", "catalog_live_event_names"]

_EVENTS: dict[str, tuple[tuple[str, ...], ...]] = {
    "catalog.product.created": (("catalog", "products"),),
    "catalog.product.updated": (("catalog", "products"),),
    "catalog.product.deleted": (("catalog", "products"),),
    # A price change alters what a product costs, so both caches go stale.
    "catalog.price.created": (("catalog", "products"), ("catalog", "prices")),
    "catalog.price.updated": (("catalog", "products"), ("catalog", "prices")),
    "catalog.price.deleted": (("catalog", "products"), ("catalog", "prices")),
}

#: The contract. Read-only, because a host mutating it would change what its own
#: parity test compares against and the drift would then be undetectable.
CATALOG_LIVE = MappingProxyType(
    {
        "namespace": "catalog",
        "channel": "admin.products",
        "events": tuple(
            MappingProxyType({"event": event, "keys": keys}) for event, keys in _EVENTS.items()
        ),
    }
)


def catalog_live_event_names() -> list[str]:
    """Every event name this package promises to broadcast."""
    return list(_EVENTS)
