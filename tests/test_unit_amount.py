"""A price may have NO unit amount, and ``None`` is not zero.

Stripe sets none on a ``tiered`` or ``custom_unit_amount`` price -- the tiers
carry the money. This package models ``tiers``, ``tiers_mode`` and
``custom_unit_amount``, sends them, and compares them on the way back, and then
typed ``unit_amount`` as ``int``, which made every one of them unrepresentable.

The comparison matters as much as the type. Prices are immutable, so "changed"
archives the live price and creates a replacement -- a churned id and orphaned
references, silently.
"""

from __future__ import annotations

import pytest

from fancy_catalog import Catalog
from fancy_catalog.stripe_sync import same_amount

from .fake_stripe import FakeStripe


@pytest.fixture
def stripe() -> FakeStripe:
    return FakeStripe()


@pytest.fixture
def catalog(stripe: FakeStripe) -> Catalog:
    return Catalog(stripe=stripe)


def test_two_equal_amounts_are_unchanged() -> None:
    assert same_amount(1999, 1999) is True


def test_a_real_price_change_is_seen() -> None:
    assert same_amount(1999, 2499) is False


def test_no_unit_amount_is_not_the_same_as_free() -> None:
    # A tiered price has None; a free price has 0. Treating them as equal leaves
    # a tiered price un-updated, or archives a free one for nothing.
    assert same_amount(None, 0) is False
    assert same_amount(0, None) is False


def test_two_tiered_prices_are_unchanged() -> None:
    assert same_amount(None, None) is True


def test_a_numeric_string_is_not_a_price_change() -> None:
    # A recorded cassette or a JSON fixture hands back a string where the SDK
    # hands back an int. A false difference archives a live price.
    assert same_amount(1999, "1999") is True


def test_a_tiered_price_is_sent_with_no_unit_amount_field_at_all(
    catalog: Catalog, stripe: FakeStripe
) -> None:
    product = catalog.create_product("Metered", external_id="prod_x")
    price = catalog.create_price(
        product.id,
        currency="USD",
        billing_scheme="tiered",
        tiers_mode="graduated",
        tiers=[
            {"up_to": 1000, "unit_amount": 0},
            {"up_to": "inf", "unit_amount": 1},
        ],
    )

    catalog.sync_price(price)

    params = stripe.calls_to("prices", "create")[0].params
    # Not ``unit_amount: None``, and emphatically not 0 -- the key must be
    # ABSENT. Sending it alongside ``tiers`` is an API error; sending 0 would be
    # a free price, silently.
    assert "unit_amount" not in params
    assert params["billing_scheme"] == "tiered"
    assert len(params["tiers"]) == 2
