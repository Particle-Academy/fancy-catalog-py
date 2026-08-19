"""The Stripe seam: what this package needs from a Stripe client, and nothing else.

``stripe`` is **not a runtime dependency**. The client is injected, exactly as
the Node twin injects a configured ``Stripe`` instance, and this module declares
the narrow shape it is used through -- six operations across three services.

That buys three things a hard dependency would not:

* the SDK version is the host's choice, and this package cannot pin them to one;
* the whole test suite runs offline against a fake, with no network and no
  monkeypatching of a transport;
* a host with its own Stripe wrapper, or a proxy, or a recorded-cassette layer,
  passes that instead.

## Passing a real client

``stripe.StripeClient`` satisfies these protocols structurally. On stripe-python
15.x, **pass ``client.v1``**::

    import stripe
    from fancy_catalog import create_catalog

    catalog = create_catalog(stripe=stripe.StripeClient(api_key).v1)

``StripeClient.products`` still works and is a documented alias, but it emits a
``DeprecationWarning`` on every access -- and a host running ``-W error`` would
turn a catalog sync into a crash. ``StripeClient.v1.products`` is the same
service object without the warning. Verified against stripe-python 15.5.1 on
2026-08-18.

## Errors

Nothing here catches a specific Stripe exception class, because importing one
would be the dependency this module exists to avoid. The sync layer catches
:class:`Exception`, logs, and re-raises -- which is what the PHP twin does with
``ApiErrorException`` and what the Node twin does with a bare ``catch``.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

__all__ = [
    "CheckoutSessionService",
    "CheckoutService",
    "PriceService",
    "ProductService",
    "StripeLike",
]


@runtime_checkable
class ProductService(Protocol):
    def create(self, params: Any, options: Any = None) -> Any: ...
    def update(self, id: str, params: Any = None, options: Any = None) -> Any: ...
    def list(self, params: Any = None, options: Any = None) -> Any: ...


@runtime_checkable
class PriceService(Protocol):
    def create(self, params: Any, options: Any = None) -> Any: ...
    def update(self, price: str, params: Any = None, options: Any = None) -> Any: ...
    def retrieve(self, price: str, params: Any = None, options: Any = None) -> Any: ...


@runtime_checkable
class CheckoutSessionService(Protocol):
    def create(self, params: Any = None, options: Any = None) -> Any: ...


@runtime_checkable
class CheckoutService(Protocol):
    @property
    def sessions(self) -> CheckoutSessionService: ...


@runtime_checkable
class StripeLike(Protocol):
    """What this package uses. A ``stripe.StripeClient().v1`` satisfies it."""

    @property
    def products(self) -> ProductService: ...

    @property
    def prices(self) -> PriceService: ...

    @property
    def checkout(self) -> CheckoutService: ...


def stripe_get(obj: Any, key: str, default: Any = None) -> Any:
    """Read a field from a Stripe object, whether it behaves like a dict or an object.

    ``stripe.Price`` supports both ``price["unit_amount"]`` and
    ``price.unit_amount``; a host passing a plain dict from a recorded fixture
    supports only the first. Change detection has to read the same fields out of
    both, and getting this wrong is silent -- a missed field means "nothing
    changed", which means an archived-and-recreated price never happens and the
    customer keeps paying the old amount.
    """
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)
