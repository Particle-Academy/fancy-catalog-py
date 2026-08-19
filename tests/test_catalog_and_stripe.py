"""The catalog surface: stores, ids, Stripe sync, checkout, and the live contract.

Everything runs against :class:`tests.fake_stripe.FakeStripe`. No network, no
credentials, no monkeypatched transport.

The assertions are mostly about the **request** rather than the response,
because that is where this package's behaviour lives: whether a price was
archived and replaced or merely updated, whether the lookup key was transferred,
what ended up in metadata. Getting any of those wrong is silent and billable.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from fancy_catalog import (
    CATALOG_LIVE,
    InMemoryPriceStore,
    InMemoryProductStore,
    Price,
    catalog_live_event_names,
    create_catalog,
    ulid,
)

from .fake_stripe import FakeStripe


@pytest.fixture
def stripe() -> FakeStripe:
    return FakeStripe()


@pytest.fixture
def catalog(stripe: FakeStripe):  # type: ignore[no-untyped-def]
    return create_catalog(stripe=stripe)


# --- Authoring ------------------------------------------------------------


def test_create_product_assigns_a_ulid_and_timestamps(catalog) -> None:  # type: ignore[no-untyped-def]
    product = catalog.create_product("Pro plan")
    assert re.fullmatch(r"[0-9A-HJKMNP-TV-Z]{26}", product.id)
    assert product.active is True
    assert product.created_at is not None
    assert catalog.products.find(product.id) is product


def test_create_price_takes_whole_minor_units(catalog) -> None:  # type: ignore[no-untyped-def]
    product = catalog.create_product("Pro plan")
    price = catalog.create_price(product.id, currency="USD", unit_amount=1999)
    assert price.unit_amount == 1999


def test_create_price_converts_a_decimal_amount_exactly(catalog) -> None:  # type: ignore[no-untyped-def]
    # The reason `amount=` exists. Neither twin owns this conversion, so every
    # consumer writes `int(19.99 * 100)` and gets 1998.
    product = catalog.create_product("Pro plan")
    price = catalog.create_price(product.id, currency="USD", amount="19.99")
    assert price.unit_amount == 1999


def test_create_price_uses_the_currencys_own_exponent(catalog) -> None:  # type: ignore[no-untyped-def]
    product = catalog.create_product("Pro plan")
    yen = catalog.create_price(product.id, currency="JPY", amount="1000")
    dinar = catalog.create_price(product.id, currency="KWD", amount="1.005")
    assert yen.unit_amount == 1000
    assert dinar.unit_amount == 1005


def test_create_price_refuses_both_amount_forms(catalog) -> None:  # type: ignore[no-untyped-def]
    product = catalog.create_product("Pro plan")
    with pytest.raises(TypeError, match="at most one"):
        catalog.create_price(product.id, currency="USD", unit_amount=1999, amount="19.99")


def test_create_price_refuses_neither_amount_form(catalog) -> None:  # type: ignore[no-untyped-def]
    product = catalog.create_product("Pro plan")
    with pytest.raises(TypeError, match="needs unit_amount"):
        catalog.create_price(product.id, currency="USD")


def test_a_tiered_price_may_have_no_unit_amount_at_all(catalog) -> None:  # type: ignore[no-untyped-def]
    """Stripe sets none on a tiered price: the tiers carry the money.

    The "neither" guard above is relaxed exactly here and nowhere else, because
    a price that is neither tiered nor custom and has no amount is still a
    mistake.
    """
    product = catalog.create_product("Metered")
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

    assert price.unit_amount is None


def test_a_custom_amount_price_may_have_no_unit_amount_either(catalog) -> None:  # type: ignore[no-untyped-def]
    product = catalog.create_product("Pay what you want")
    price = catalog.create_price(
        product.id,
        currency="USD",
        custom_unit_amount={"enabled": True, "minimum": 500},
    )

    assert price.unit_amount is None


def test_attach_feature_enforces_the_schemas_unique_pair(catalog) -> None:  # type: ignore[no-untyped-def]
    # UNIQUE(product_id, product_feature_id) in the migration. An in-memory
    # store that allows two rows lets a test pass on state a real database
    # would reject.
    product = catalog.create_product("Pro plan")
    feature = catalog.create_product_feature("ai-tokens", "AI tokens", type="resource")
    catalog.attach_feature(product.id, feature.id, enabled=True, included_quantity=100)
    catalog.attach_feature(product.id, feature.id, enabled=True, included_quantity=500)

    rows = catalog.product_features.for_product(product.id)
    assert len(rows) == 1
    assert rows[0].included_quantity == 500


def test_ulids_are_sortable_by_their_timestamp() -> None:
    early = ulid(1_700_000_000_000)
    late = ulid(1_800_000_000_000)
    assert early < late


def test_a_ulid_avoids_the_ambiguous_letters() -> None:
    # Crockford base32 drops I, L, O and U so an id cannot be misread aloud or
    # accidentally spell a word.
    assert not set("ILOU") & set(ulid())


def test_a_ulid_timestamp_must_fit_in_48_bits() -> None:
    with pytest.raises(ValueError, match="48 bits"):
        ulid(1 << 48)


# --- Stores ---------------------------------------------------------------


def test_removing_a_product_is_a_soft_delete(catalog) -> None:  # type: ignore[no-untyped-def]
    # Soft deletes preserve financial history: an invoice referencing a
    # hard-deleted product is an invoice nobody can explain.
    product = catalog.create_product("Retired plan")
    catalog.products.remove(product.id)

    assert catalog.products.find(product.id) is None
    assert catalog.products.find(product.id, with_trashed=True) is not None
    assert catalog.products.all() == []
    assert len(catalog.products.all(with_trashed=True)) == 1


def test_removing_a_price_is_a_soft_delete(catalog) -> None:  # type: ignore[no-untyped-def]
    product = catalog.create_product("Pro plan")
    price = catalog.create_price(product.id, currency="USD", unit_amount=1999)
    catalog.prices.remove(price.id)

    assert catalog.prices.find(price.id) is None
    assert catalog.prices.for_product(product.id) == []
    assert len(catalog.prices.for_product(product.id, with_trashed=True)) == 1


def test_removing_a_feature_cascades_to_its_pivot_rows(catalog) -> None:  # type: ignore[no-untyped-def]
    # `cascadeOnDelete` in the migration. A pivot row pointing at a deleted
    # feature would resolve to a grant with no key.
    product = catalog.create_product("Pro plan")
    feature = catalog.create_product_feature("sso", "SSO")
    catalog.attach_feature(product.id, feature.id, enabled=True)
    catalog.product_features.remove(feature.id)

    assert catalog.product_features.for_product(product.id) == []
    assert catalog.product_features.configs_for_product(product.id) == []


def test_find_by_key(catalog) -> None:  # type: ignore[no-untyped-def]
    catalog.create_product_feature("ai-tokens", "AI tokens", type="resource")
    assert catalog.product_features.find_by_key("ai-tokens") is not None
    assert catalog.product_features.find_by_key("nope") is None


# --- Product sync ---------------------------------------------------------


def test_syncing_a_new_product_creates_it_and_captures_the_id(catalog, stripe) -> None:  # type: ignore[no-untyped-def]
    product = catalog.create_product("Pro plan", description="Everything")
    catalog.sync_product(product)

    assert product.external_id is not None
    params = stripe.calls_to("products", "create")[0].params
    assert params["name"] == "Pro plan"
    assert params["description"] == "Everything"
    assert params["metadata"]["product_id"] == product.id


def test_syncing_an_existing_product_updates_rather_than_recreates(catalog, stripe) -> None:  # type: ignore[no-untyped-def]
    product = catalog.create_product("Pro plan")
    catalog.sync_product(product)
    catalog.sync_product(product)

    assert len(stripe.calls_to("products", "create")) == 1
    assert len(stripe.calls_to("products", "update")) == 1


def test_an_empty_description_is_omitted_rather_than_sent_as_null(catalog, stripe) -> None:  # type: ignore[no-untyped-def]
    # Stripe rejects a null description on create; both twins omit the key.
    product = catalog.create_product("Pro plan")
    catalog.sync_product(product)
    assert "description" not in stripe.calls_to("products", "create")[0].params


def test_the_product_lookup_key_rides_in_metadata(catalog, stripe) -> None:  # type: ignore[no-untyped-def]
    # Stripe PRODUCTS have no native lookup key -- prices do. Confusing the two
    # is how a lookup silently finds nothing.
    product = catalog.create_product("Pro plan", lookup_key="pro")
    catalog.sync_product(product)
    params = stripe.calls_to("products", "create")[0].params
    assert params["metadata"]["product_lookup_key"] == "pro"
    assert "lookup_key" not in params


def test_metadata_values_are_coerced_to_strings(catalog, stripe) -> None:  # type: ignore[no-untyped-def]
    product = catalog.create_product("Pro plan", metadata={"seats": 5, "flags": ["a", "b"]})
    catalog.sync_product(product)
    metadata = stripe.calls_to("products", "create")[0].params["metadata"]
    assert metadata["seats"] == "5"
    assert metadata["flags"] == '["a", "b"]'


def test_a_null_metadata_value_is_dropped_not_stringified(catalog, stripe) -> None:  # type: ignore[no-untyped-def]
    # `"None"` would be stored as a literal, and `""` is how Stripe deletes a
    # metadata key. Neither is what a null field means.
    product = catalog.create_product("Pro plan", metadata={"note": None})
    catalog.sync_product(product)
    assert "note" not in stripe.calls_to("products", "create")[0].params["metadata"]


def test_a_failed_product_sync_is_logged_and_re_raised(stripe) -> None:  # type: ignore[no-untyped-def]
    logged: list[tuple[str, dict]] = []

    class Logger:
        def error(self, message, context=None):  # type: ignore[no-untyped-def]
            logged.append((message, dict(context or {})))

    catalog = create_catalog(stripe=stripe, logger=Logger())
    stripe.fail_on.add("products.create")
    product = catalog.create_product("Pro plan")

    with pytest.raises(Exception, match="armed failure"):
        catalog.sync_product(product)
    assert logged and logged[0][0] == "Stripe product sync failed"


# --- Price sync: the immutability rules -----------------------------------


def _synced_price(catalog, stripe, **overrides):  # type: ignore[no-untyped-def]
    product = catalog.create_product("Pro plan")
    price = catalog.create_price(
        product.id,
        currency="USD",
        unit_amount=overrides.pop("unit_amount", 1999),
        recurring_interval="month",
        **overrides,
    )
    catalog.sync_product(product)
    catalog.sync_price(price)
    return product, price


def test_syncing_a_new_price_creates_it(catalog, stripe) -> None:  # type: ignore[no-untyped-def]
    _, price = _synced_price(catalog, stripe)
    assert price.external_id is not None
    params = stripe.calls_to("prices", "create")[0].params
    assert params["unit_amount"] == 1999
    assert params["currency"] == "usd"
    assert params["recurring"] == {
        "interval": "month",
        "interval_count": 1,
        "usage_type": "licensed",
    }


def test_an_unchanged_price_is_updated_not_replaced(catalog, stripe) -> None:  # type: ignore[no-untyped-def]
    _, price = _synced_price(catalog, stripe)
    first_external = price.external_id
    catalog.sync_price(price)

    assert price.external_id == first_external
    assert len(stripe.calls_to("prices", "create")) == 1
    assert len(stripe.calls_to("prices", "update")) == 1


def test_a_changed_amount_archives_the_old_price_and_creates_a_new_one(catalog, stripe) -> None:  # type: ignore[no-untyped-def]
    # Stripe prices are immutable. This is the single most important behaviour
    # in the package: getting it wrong means the customer keeps paying the old
    # amount and nothing reports it.
    _, price = _synced_price(catalog, stripe)
    old_external = price.external_id

    price.unit_amount = 2999
    catalog.sync_price(price)

    assert price.external_id != old_external
    archive = stripe.calls_to("prices", "update")[0]
    assert archive.args[0] == old_external
    assert archive.params == {"active": False}
    assert len(stripe.calls_to("prices", "create")) == 2


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("currency", "EUR"),
        ("recurring_interval", "year"),
        ("recurring_interval_count", 3),
        ("pricing_model", "usage_recurring"),
        ("billing_scheme", "tiered"),
        ("tiers_mode", "volume"),
        ("transform_quantity", {"divide_by": 10, "round": "up"}),
        ("custom_unit_amount", {"enabled": True, "minimum": 100}),
    ],
)
def test_every_compared_field_triggers_a_replacement(catalog, stripe, field, value) -> None:  # type: ignore[no-untyped-def]
    # One row per field the change detection claims to compare. A field it
    # forgets is a price that never gets replaced -- and the failure is
    # invisible, because the sync reports success either way.
    # `tiers_mode` is only sent when the price is genuinely tiered -- in Stripe,
    # in both twins, and here -- so that row needs a tiered price to change.
    tiered = field in {"billing_scheme", "tiers_mode"}
    setup = (
        {"billing_scheme": "tiered", "tiers": [{"up_to": "inf", "unit_amount": 100}]}
        if field == "tiers_mode"
        else {}
    )
    _, price = _synced_price(catalog, stripe, **setup)
    before = price.external_id

    setattr(price, field, value)
    if tiered:
        price.billing_scheme = "tiered"
        price.tiers = price.tiers or [{"up_to": "inf", "unit_amount": 100}]
    catalog.sync_price(price)

    assert price.external_id != before, f"changing {field} did not replace the price"


def test_a_changed_tier_list_triggers_a_replacement(catalog, stripe) -> None:  # type: ignore[no-untyped-def]
    _, price = _synced_price(
        catalog,
        stripe,
        billing_scheme="tiered",
        tiers=[{"up_to": 10, "unit_amount": 100}, {"up_to": "inf", "unit_amount": 50}],
    )
    before = price.external_id

    price.tiers = [{"up_to": 10, "unit_amount": 200}, {"up_to": "inf", "unit_amount": 50}]
    catalog.sync_price(price)
    assert price.external_id != before


def test_reordered_tier_keys_do_not_trigger_a_replacement(catalog, stripe) -> None:  # type: ignore[no-untyped-def]
    # Both twins compare with `json_encode` / `JSON.stringify`, which is
    # insertion-ordered, so the same tier read back from Stripe with its keys in
    # another order reads as a change and archives a price that did not need
    # replacing. This port sorts keys before comparing.
    _, price = _synced_price(
        catalog, stripe, billing_scheme="tiered", tiers=[{"up_to": 10, "unit_amount": 100}]
    )
    before = price.external_id

    price.tiers = [{"unit_amount": 100, "up_to": 10}]
    catalog.sync_price(price)
    assert price.external_id == before


def test_a_lookup_key_is_transferred_on_create(catalog, stripe) -> None:  # type: ignore[no-untyped-def]
    # Without `transfer_lookup_key`, the archive-and-replace above fails with
    # "lookup key already exists" the first time anyone changes the price of
    # something that has one.
    _synced_price(catalog, stripe, lookup_key="pro-monthly")
    params = stripe.calls_to("prices", "create")[0].params
    assert params["lookup_key"] == "pro-monthly"
    assert params["transfer_lookup_key"] is True


def test_changing_only_the_lookup_key_still_reaches_stripe(catalog, stripe) -> None:  # type: ignore[no-untyped-def]
    # It leaves pricing untouched, so it lands in the metadata-update branch.
    # Omitting it there makes the edit a silent no-op: saved locally, never
    # sent, and a lookup by the new key finds nothing.
    _, price = _synced_price(catalog, stripe, lookup_key="pro-monthly")
    price.lookup_key = "pro-monthly-v2"
    catalog.sync_price(price)

    update = stripe.calls_to("prices", "update")[-1].params
    assert update["lookup_key"] == "pro-monthly-v2"
    assert update["transfer_lookup_key"] is True
    assert price.external_id is not None


def test_a_price_that_vanished_from_stripe_is_recreated(catalog, stripe) -> None:  # type: ignore[no-untyped-def]
    _, price = _synced_price(catalog, stripe)
    stripe.prices_data.clear()

    catalog.sync_price(price)
    assert len(stripe.calls_to("prices", "create")) == 2


def test_the_shared_ulid_links_an_archived_price_to_its_replacement(catalog, stripe) -> None:  # type: ignore[no-untyped-def]
    _, price = _synced_price(catalog, stripe)
    original_id = price.id

    price.unit_amount = 2999
    catalog.sync_price(price)

    for call in stripe.calls_to("prices", "create"):
        assert call.params["metadata"]["price_id"] == original_id


def test_a_usage_based_price_is_metered(catalog, stripe) -> None:  # type: ignore[no-untyped-def]
    _synced_price(catalog, stripe, pricing_model="usage_recurring")
    assert stripe.calls_to("prices", "create")[0].params["recurring"]["usage_type"] == "metered"


def test_a_one_time_price_has_no_recurring_block(catalog, stripe) -> None:  # type: ignore[no-untyped-def]
    product = catalog.create_product("Add-on")
    price = catalog.create_price(product.id, currency="USD", unit_amount=500, type="one_time")
    catalog.sync_product(product)
    catalog.sync_price(price)
    assert "recurring" not in stripe.calls_to("prices", "create")[0].params


def test_syncing_a_price_syncs_its_product_first(catalog, stripe) -> None:  # type: ignore[no-untyped-def]
    product = catalog.create_product("Pro plan")
    price = catalog.create_price(product.id, currency="USD", unit_amount=1999)
    catalog.sync_price(price)

    assert product.external_id is not None
    assert stripe.calls_to("prices", "create")[0].params["product"] == product.external_id


def test_a_price_referencing_a_missing_product_is_an_error(stripe) -> None:  # type: ignore[no-untyped-def]
    catalog = create_catalog(
        stripe=stripe, products=InMemoryProductStore(), prices=InMemoryPriceStore()
    )
    orphan = Price(id=ulid(), product_id="missing", currency="USD", unit_amount=100)
    with pytest.raises(LookupError, match="not in the store"):
        catalog.sync_price(orphan)


# --- Product + prices, and the sync stamp ---------------------------------


def test_sync_product_and_prices_stamps_everything_and_fires_the_hook(stripe) -> None:  # type: ignore[no-untyped-def]
    synced: list[str] = []
    catalog = create_catalog(stripe=stripe, on_product_synced=synced.append)

    product = catalog.create_product("Pro plan")
    catalog.create_price(product.id, currency="USD", unit_amount=1999)
    catalog.create_price(product.id, currency="USD", unit_amount=19990, recurring_interval="year")
    catalog.sync_product_and_prices(product)

    assert synced == [product.id]
    assert product.last_synced_at is not None
    assert all(p.last_synced_at is not None for p in catalog.prices.for_product(product.id))
    assert len(stripe.calls_to("prices", "create")) == 2


def test_a_never_synced_product_is_out_of_sync(catalog) -> None:  # type: ignore[no-untyped-def]
    assert catalog.is_out_of_sync(catalog.create_product("Pro plan")) is True


def test_a_product_edited_after_its_sync_is_out_of_sync(catalog, stripe) -> None:  # type: ignore[no-untyped-def]
    product = catalog.create_product("Pro plan")
    catalog.sync_product_and_prices(product)
    assert catalog.is_out_of_sync(product) is False

    product.updated_at = datetime.now(UTC) + timedelta(seconds=1)
    assert catalog.is_out_of_sync(product) is True


def test_a_price_edited_after_its_sync_makes_the_product_out_of_sync(catalog) -> None:  # type: ignore[no-untyped-def]
    product = catalog.create_product("Pro plan")
    price = catalog.create_price(product.id, currency="USD", unit_amount=1999)
    catalog.sync_product_and_prices(product)

    price.updated_at = datetime.now(UTC) + timedelta(seconds=1)
    assert catalog.is_out_of_sync(product) is True


def test_a_price_added_after_the_sync_makes_the_product_out_of_sync(catalog) -> None:  # type: ignore[no-untyped-def]
    product = catalog.create_product("Pro plan")
    catalog.sync_product_and_prices(product)
    catalog.create_price(product.id, currency="USD", unit_amount=999)
    assert catalog.is_out_of_sync(product) is True


# --- Connection test ------------------------------------------------------


def test_test_connection_reports_success_with_a_count(catalog, stripe) -> None:  # type: ignore[no-untyped-def]
    catalog.sync_product(catalog.create_product("Pro plan"))
    result = catalog.test_connection()
    assert result.success is True
    assert result.product_count == 1


def test_test_connection_never_leaks_the_stripe_error(catalog, stripe) -> None:  # type: ignore[no-untyped-def]
    # A raw Stripe error can carry account identifiers and key prefixes, and
    # this result is built for an admin screen.
    stripe.fail_on.add("products.list")
    result = catalog.test_connection()
    assert result.success is False
    assert "armed failure" not in result.message
    assert "credentials" in result.message


# --- Checkout -------------------------------------------------------------


def test_a_subscription_checkout_carries_the_price_and_product_ids(catalog, stripe) -> None:  # type: ignore[no-untyped-def]
    product, price = _synced_price(catalog, stripe)
    session = catalog.subscription_checkout(
        price, success_url="https://ok", cancel_url="https://no", customer="cus_1"
    )

    params = stripe.calls_to("checkout.sessions", "create")[0].params
    assert params["mode"] == "subscription"
    assert params["line_items"] == [{"price": price.external_id, "quantity": 1}]
    assert params["customer"] == "cus_1"
    assert params["subscription_data"]["metadata"]["price_id"] == price.id
    assert params["subscription_data"]["metadata"]["product_id"] == product.id
    assert session["url"].startswith("https://checkout.stripe.test/")


def test_a_subscription_checkout_without_a_customer_omits_the_key(catalog, stripe) -> None:  # type: ignore[no-untyped-def]
    # Stripe collects a new customer on the hosted page, which is right for a
    # signed-out storefront. Sending `customer: None` is an API error.
    _, price = _synced_price(catalog, stripe)
    catalog.subscription_checkout(price, success_url="https://ok", cancel_url="https://no")
    assert "customer" not in stripe.calls_to("checkout.sessions", "create")[0].params


def test_a_trial_period_reaches_the_session(catalog, stripe) -> None:  # type: ignore[no-untyped-def]
    _, price = _synced_price(catalog, stripe, recurring_trial_period_days=14)
    catalog.subscription_checkout(price, success_url="https://ok", cancel_url="https://no")
    params = stripe.calls_to("checkout.sessions", "create")[0].params
    assert params["subscription_data"]["trial_period_days"] == 14


def test_a_one_time_checkout_enables_invoice_creation(catalog, stripe) -> None:  # type: ignore[no-untyped-def]
    product = catalog.create_product("Add-on")
    price = catalog.create_price(product.id, currency="USD", unit_amount=500, type="one_time")
    catalog.sync_product(product)
    catalog.sync_price(price)

    catalog.one_time_checkout(price, quantity=3, success_url="https://ok", cancel_url="https://no")
    params = stripe.calls_to("checkout.sessions", "create")[0].params
    assert params["mode"] == "payment"
    assert params["line_items"][0]["quantity"] == 3
    assert params["invoice_creation"]["enabled"] is True


def test_a_recurring_price_cannot_open_a_one_time_checkout(catalog, stripe) -> None:  # type: ignore[no-untyped-def]
    _, price = _synced_price(catalog, stripe)
    with pytest.raises(ValueError, match=r"subscription_checkout\(\)"):
        catalog.one_time_checkout(
            price, quantity=1, success_url="https://ok", cancel_url="https://no"
        )


def test_a_one_time_price_cannot_open_a_subscription_checkout(catalog, stripe) -> None:  # type: ignore[no-untyped-def]
    product = catalog.create_product("Add-on")
    price = catalog.create_price(product.id, currency="USD", unit_amount=500, type="one_time")
    catalog.sync_product(product)
    catalog.sync_price(price)
    with pytest.raises(ValueError, match="subscription checkout"):
        catalog.subscription_checkout(price, success_url="https://ok", cancel_url="https://no")


def test_an_unsynced_price_cannot_open_a_checkout(catalog) -> None:  # type: ignore[no-untyped-def]
    product = catalog.create_product("Pro plan")
    price = catalog.create_price(product.id, currency="USD", unit_amount=1999)
    with pytest.raises(ValueError, match="no Stripe price id"):
        catalog.subscription_checkout(price, success_url="https://ok", cancel_url="https://no")


def test_a_one_time_checkout_needs_a_positive_whole_quantity(catalog, stripe) -> None:  # type: ignore[no-untyped-def]
    product = catalog.create_product("Add-on")
    price = catalog.create_price(product.id, currency="USD", unit_amount=500, type="one_time")
    catalog.sync_product(product)
    catalog.sync_price(price)

    for bad in (0, -1, 1.5, True):
        with pytest.raises(ValueError):
            catalog.one_time_checkout(
                price, quantity=bad, success_url="https://ok", cancel_url="https://no"
            )


def test_the_checkout_url_helpers_return_the_url(catalog, stripe) -> None:  # type: ignore[no-untyped-def]
    _, price = _synced_price(catalog, stripe)
    url = catalog.subscription_checkout_url(
        price, success_url="https://ok", cancel_url="https://no"
    )
    assert url.startswith("https://checkout.stripe.test/")


# --- The Live Contract ----------------------------------------------------


def test_every_live_event_is_in_the_catalog_namespace() -> None:
    for name in catalog_live_event_names():
        assert name.startswith("catalog."), f"{name} is not in the catalog namespace"


def test_the_live_contract_names_a_channel() -> None:
    assert CATALOG_LIVE["channel"]


def test_a_price_event_invalidates_both_caches() -> None:
    # A price change alters what a product costs, so the product cache is stale
    # too. Invalidating only `prices` leaves a pricing page showing yesterday's
    # numbers.
    events = {e["event"]: e["keys"] for e in CATALOG_LIVE["events"]}
    assert ("catalog", "products") in events["catalog.price.updated"]
    assert ("catalog", "prices") in events["catalog.price.updated"]


def test_live_contract_parity_with_the_php_twin() -> None:
    """The test the contract exists for, read straight out of the PHP source.

    Drift here is SILENT: rename an event on one side and nothing throws -- the
    client subscribes to a name nobody broadcasts, the cache is never
    invalidated, and the UI just stops updating.

    When the PHP twin is not on disk this WARNS rather than skipping. A silent
    skip reads exactly like a passing comparison, which is the failure mode the
    whole conformance effort in this org exists to end. CI checks the twin out
    so the warning never fires there.
    """
    php = _php_live_contract()
    if php is None:
        pytest.warns  # noqa: B018 - referenced so the intent is greppable
        import warnings

        warnings.warn(
            "laravel-catalog is not on disk, so the Live Contract parity check did NOT run. "
            "Set CATALOG_PHP_SRC or check the twin out beside this repository.",
            UserWarning,
            stacklevel=1,
        )
        return

    assert php["namespace"] == CATALOG_LIVE["namespace"]
    assert php["channel"] == CATALOG_LIVE["channel"]
    assert php["events"] == {
        e["event"]: [list(k) for k in e["keys"]] for e in CATALOG_LIVE["events"]
    }


def _php_live_contract() -> dict | None:
    import os

    root = os.environ.get("CATALOG_PHP_SRC")
    candidates = (
        [Path(root) / "LiveContract.php"]
        if root
        else [
            Path(__file__).resolve().parents[3] / "laravel-catalog" / "src" / "LiveContract.php",
            Path(__file__).resolve().parents[2] / "laravel-catalog" / "src" / "LiveContract.php",
        ]
    )
    source = next((p.read_text(encoding="utf-8") for p in candidates if p.is_file()), None)
    if source is None:
        return None

    namespace = re.search(r"const NAMESPACE = '([^']+)'", source)
    channel = re.search(r"const CHANNEL = '([^']+)'", source)
    block = re.search(r"const EVENTS = \[(.*?)\n    \];", source, re.S)

    events: dict[str, list[list[str]]] = {}
    for line in (block.group(1) if block else "").split("\n"):
        # Line by line rather than one multiline regex: the Node twin's first
        # attempt required a trailing newline per entry and silently dropped the
        # LAST one, and its "did it parse anything" guard still passed.
        match = re.match(r"^\s*'([a-z0-9.\-_]+)'\s*=>\s*(\[.*\])\s*,?\s*$", line)
        if not match:
            continue
        events[match.group(1)] = [
            re.findall(r"'([^']+)'", group)
            for group in re.findall(r"\[([^\[\]]*)\]", match.group(2))
        ]

    assert events, "parsed no events out of LiveContract.php -- the parser, not the contract"
    return {
        "namespace": namespace.group(1) if namespace else "",
        "channel": channel.group(1) if channel else "",
        "events": events,
    }
