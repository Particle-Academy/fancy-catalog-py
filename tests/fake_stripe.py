"""A fake Stripe, structurally identical to what the real client offers.

**No test in this package touches the network, ever.** A catalog test that could
reach Stripe is a test that charges somebody's card by accident, and a test that
needs credentials is a test nobody runs.

The fake records every call so a test can assert on the *request*, which is what
matters here: the archive-and-replace decision, ``transfer_lookup_key``, the
metadata coercion. It is deliberately not a Stripe simulator -- it returns the
minimum a caller reads back and remembers what it was told.

The shape is taken from stripe-python 15.5.1, verified on 2026-08-18:
``products.create(params)``, ``products.update(id, params)``,
``products.list(params)``, ``prices.create(params)``,
``prices.update(id, params)``, ``prices.retrieve(id)``,
``checkout.sessions.create(params)``. A real ``stripe.StripeClient(...).v1``
satisfies the same protocol -- note the ``.v1``: the bare ``StripeClient.products``
still works but emits a ``DeprecationWarning`` on every access.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class StripeApiError(Exception):
    """Stands in for ``stripe.StripeError``, which is not imported anywhere here."""


@dataclass(frozen=True)
class Call:
    service: str
    method: str
    args: tuple[Any, ...]

    @property
    def params(self) -> dict[str, Any]:
        """The last dict argument -- the params payload for every operation here."""
        return next((a for a in reversed(self.args) if isinstance(a, dict)), {})


class _Products:
    def __init__(self, parent: FakeStripe) -> None:
        self._p = parent

    def create(self, params: Any, options: Any = None) -> dict[str, Any]:
        self._p.record("products", "create", params)
        self._p.raise_if_armed("products.create")
        product_id = f"prod_{self._p.next_id()}"
        row = {"id": product_id, **dict(params)}
        self._p.products_data[product_id] = row
        return row

    def update(self, id: str, params: Any = None, options: Any = None) -> dict[str, Any]:
        self._p.record("products", "update", id, params)
        self._p.raise_if_armed("products.update")
        row = self._p.products_data.setdefault(id, {"id": id})
        row.update(dict(params or {}))
        return row

    def list(self, params: Any = None, options: Any = None) -> dict[str, Any]:
        self._p.record("products", "list", params)
        self._p.raise_if_armed("products.list")
        limit = int((params or {}).get("limit", 10))
        return {"data": list(self._p.products_data.values())[:limit]}


class _Prices:
    def __init__(self, parent: FakeStripe) -> None:
        self._p = parent

    def create(self, params: Any, options: Any = None) -> dict[str, Any]:
        self._p.record("prices", "create", params)
        self._p.raise_if_armed("prices.create")
        price_id = f"price_{self._p.next_id()}"
        row = {"id": price_id, **dict(params)}
        self._p.prices_data[price_id] = row
        return row

    def update(self, price: str, params: Any = None, options: Any = None) -> dict[str, Any]:
        self._p.record("prices", "update", price, params)
        self._p.raise_if_armed("prices.update")
        row = self._p.prices_data.setdefault(price, {"id": price})
        row.update(dict(params or {}))
        return row

    def retrieve(self, price: str, params: Any = None, options: Any = None) -> dict[str, Any]:
        self._p.record("prices", "retrieve", price)
        self._p.raise_if_armed("prices.retrieve")
        row = self._p.prices_data.get(price)
        if row is None:
            # What the real API does for an unknown id, and the branch the sync
            # layer relies on to recreate a price that vanished from Stripe.
            raise StripeApiError(f"No such price: {price}")
        return row


class _Sessions:
    def __init__(self, parent: FakeStripe) -> None:
        self._p = parent

    def create(self, params: Any = None, options: Any = None) -> dict[str, Any]:
        self._p.record("checkout.sessions", "create", params)
        self._p.raise_if_armed("checkout.sessions.create")
        session_id = f"cs_{self._p.next_id()}"
        row = {
            "id": session_id,
            "url": f"https://checkout.stripe.test/{session_id}",
            **dict(params or {}),
        }
        self._p.sessions[session_id] = row
        return row


class _Checkout:
    def __init__(self, parent: FakeStripe) -> None:
        self.sessions = _Sessions(parent)


class FakeStripe:
    """An offline stand-in for ``stripe.StripeClient(...).v1``."""

    def __init__(self) -> None:
        self.products_data: dict[str, dict[str, Any]] = {}
        self.prices_data: dict[str, dict[str, Any]] = {}
        self.sessions: dict[str, dict[str, Any]] = {}
        self.calls: list[Call] = []
        #: Operations armed to raise, e.g. ``{"prices.create"}``.
        self.fail_on: set[str] = set()
        self._counter = 0

        self.products = _Products(self)
        self.prices = _Prices(self)
        self.checkout = _Checkout(self)

    def next_id(self) -> str:
        self._counter += 1
        return f"{self._counter:04d}"

    def record(self, service: str, method: str, *args: Any) -> None:
        self.calls.append(Call(service=service, method=method, args=args))

    def raise_if_armed(self, operation: str) -> None:
        if operation in self.fail_on:
            raise StripeApiError(f"armed failure: {operation}")

    def calls_to(self, service: str, method: str) -> list[Call]:
        return [c for c in self.calls if c.service == service and c.method == method]
