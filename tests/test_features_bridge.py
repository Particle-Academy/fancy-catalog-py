"""The seam: a catalog driving `fancy-features`, through one shared contract.

This is the file that proves the pair composes. Everything else in either
package can be right while this is wrong, and the failure mode is a subject who
pays for a plan and cannot use it.
"""

from __future__ import annotations

import asyncio
import dataclasses

import pytest

fancy_features = pytest.importorskip(
    "fancy_features",
    reason=(
        "the bridge IMPORTS the shared contract rather than mirroring it, so it needs "
        "fancy-features installed: pip install 'fancy-catalog[features]'"
    ),
)

from fancy_features import FeatureGrant, create_features  # noqa: E402
from fancy_features.contract import FeatureSource  # noqa: E402

from fancy_catalog import Subscription, create_catalog  # noqa: E402
from fancy_catalog.features import (  # noqa: E402
    CatalogFeatureSource,
    create_catalog_feature_source,
)

from .fake_stripe import FakeStripe  # noqa: E402


@pytest.fixture
def catalog():  # type: ignore[no-untyped-def]
    return create_catalog(stripe=FakeStripe())


@pytest.fixture
def pro_plan(catalog):  # type: ignore[no-untyped-def]
    """A product with one boolean feature and one metered one."""
    product = catalog.create_product("Pro plan")
    mcp = catalog.create_product_feature("use-mcp", "Use MCP")
    tokens = catalog.create_product_feature("ai-tokens", "AI tokens", type="resource")
    catalog.attach_feature(product.id, mcp.id, enabled=True)
    catalog.attach_feature(product.id, tokens.id, enabled=True, included_quantity=100)
    return product


# --- The contract is shared, not copied -----------------------------------


def test_the_bridge_emits_the_contracts_own_grant_type(catalog, pro_plan) -> None:  # type: ignore[no-untyped-def]
    # Not "a structurally identical object" -- the class from
    # `fancy_features.contract`. In TypeScript the two packages mirror the type
    # and structural assignability keeps them honest; Python has no equivalent
    # across distributions, so the bridge imports it. This asserts that it did.
    source = create_catalog_feature_source(
        catalog,
        resolve_subscription=lambda s, c=None: Subscription(id="sub", product_id=pro_plan.id),
    )
    grants = list(source.grants_for("u"))
    assert grants and all(isinstance(g, FeatureGrant) for g in grants)
    assert type(grants[0]) is FeatureGrant


def test_the_bridge_satisfies_the_feature_source_protocol(catalog) -> None:  # type: ignore[no-untyped-def]
    source = create_catalog_feature_source(catalog, resolve_subscription=lambda s, c=None: None)
    assert isinstance(source, FeatureSource)


def test_there_is_exactly_one_definition_of_the_grant_type() -> None:
    # The regression guard for the divergence itself. If somebody later adds a
    # mirrored FeatureGrant to fancy_catalog "so the bridge has no dependency",
    # this fails -- which is the whole point, because the copy would then be
    # maintained by hand and nothing else would notice it drifting.
    import fancy_catalog

    assert not hasattr(fancy_catalog, "FeatureGrant")
    assert FeatureGrant.__module__ == "fancy_features.contract"


def test_the_bridge_maps_every_pivot_column(catalog, pro_plan) -> None:  # type: ignore[no-untyped-def]
    feature = catalog.create_product_feature("seats", "Seats", type="resource")
    catalog.attach_feature(
        pro_plan.id,
        feature.id,
        enabled=True,
        included_quantity=10,
        overage_limit=3,
        config={"warn_at": 8},
    )
    source = create_catalog_feature_source(
        catalog,
        resolve_subscription=lambda s, c=None: Subscription(id="sub", product_id=pro_plan.id),
    )
    grant = next(g for g in source.grants_for("u") if g.key == "seats")

    assert grant == FeatureGrant(
        key="seats",
        type="resource",
        enabled=True,
        included_quantity=10,
        overage_limit=3,
        source=f"catalog:{pro_plan.id}",
        config={"warn_at": 8},
    )


def test_every_pivot_field_reaches_the_grant() -> None:
    # A field added to ProductFeatureConfig and not mapped here is an
    # entitlement the gating layer never sees. Asserted structurally so the
    # omission fails at the moment the field is added, not months later.
    from fancy_catalog.types import ProductFeatureConfig

    pivot = {f.name for f in dataclasses.fields(ProductFeatureConfig)}
    carried = {f.name for f in dataclasses.fields(FeatureGrant)}
    unmapped = (
        pivot
        - carried
        - {
            "id",  # the pivot row's own identity, meaningless downstream
            "product_id",  # carried as `source`
            "product_feature_id",  # resolved into `key`
            "created_at",
            "updated_at",
        }
    )
    assert unmapped == set(), f"pivot field(s) {unmapped} never reach a FeatureGrant"


# --- Resolution through the manager ---------------------------------------


def _features(catalog, product_id: str | None, **kwargs):  # type: ignore[no-untyped-def]
    def resolve(subject, context=None):  # type: ignore[no-untyped-def]
        return None if product_id is None else Subscription(id="sub", product_id=product_id)

    return create_features(
        sources=[create_catalog_feature_source(catalog, resolve_subscription=resolve)], **kwargs
    )


def test_a_subscribers_boolean_feature_is_on(catalog, pro_plan) -> None:  # type: ignore[no-untyped-def]
    features = _features(catalog, pro_plan.id)
    assert features.can_access("use-mcp", "u") is True


def test_a_non_subscriber_gets_nothing(catalog, pro_plan) -> None:  # type: ignore[no-untyped-def]
    features = _features(catalog, None)
    assert features.can_access("use-mcp", "u") is False
    assert features.remaining("ai-tokens", "u") is None
    assert features.enabled("u") == []


def test_a_subscription_to_a_deleted_product_grants_nothing(catalog, pro_plan) -> None:  # type: ignore[no-untyped-def]
    # A soft-deleted product must not keep granting. `products.find` excludes
    # trashed rows, and this is the assertion that the bridge goes through it.
    features = _features(catalog, pro_plan.id)
    assert features.can_access("use-mcp", "u") is True
    catalog.products.remove(pro_plan.id)
    assert features.can_access("use-mcp", "u") is False


def test_a_disabled_pivot_row_does_not_grant(catalog, pro_plan) -> None:  # type: ignore[no-untyped-def]
    feature = catalog.create_product_feature("sso", "SSO")
    catalog.attach_feature(pro_plan.id, feature.id, enabled=False)
    features = _features(catalog, pro_plan.id)
    assert features.can_access("sso", "u") is False


def test_the_metered_quota_comes_from_the_pivot(catalog, pro_plan) -> None:  # type: ignore[no-untyped-def]
    features = _features(catalog, pro_plan.id)
    assert features.remaining("ai-tokens", "u") == 100
    features.increment("ai-tokens", "u", 40)
    assert features.remaining("ai-tokens", "u") == 60


def test_an_exhausted_metered_feature_refuses_consumption_but_stays_entitled(
    catalog, pro_plan
) -> None:  # type: ignore[no-untyped-def]
    """The ruling, through the catalog bridge.

    The last assertion used to be ``is False``. `can_access` answers ENTITLEMENT
    now: the customer is still paying for the feature, and hiding it at the
    moment they are spending most on it is the opposite of useful. `can_consume`
    and `try_consume` carry the quota question.
    """
    features = _features(catalog, pro_plan.id)
    assert features.try_consume("ai-tokens", "u", 100) is True
    assert features.try_consume("ai-tokens", "u", 1) is False
    assert features.can_consume("ai-tokens", "u", 1) is False

    assert features.can_access("ai-tokens", "u") is True
    assert features.is_entitled("ai-tokens", "u") is True


def test_explain_names_the_product_the_entitlement_came_from(catalog, pro_plan) -> None:  # type: ignore[no-untyped-def]
    features = _features(catalog, pro_plan.id)
    result = features.explain("use-mcp", "u")
    assert result.allowed is True
    assert result.source == f"source:catalog:{pro_plan.id}"


def test_a_group_can_raise_a_plans_quota(catalog, pro_plan) -> None:  # type: ignore[no-untyped-def]
    # The two halves composing: the plan grants 100, an override grants more,
    # and MAX wins because a comp'd account must not be worse off than the plan.
    from fancy_features import InMemoryGroupStore

    store = InMemoryGroupStore()
    store.assign("u", "staff")
    features = _features(
        catalog,
        pro_plan.id,
        groups=[
            {"key": "staff", "features": ["ai-tokens"], "overrides": {"ai-tokens": {"limit": 5000}}}
        ],
        group_store=store,
    )
    assert features.remaining("ai-tokens", "u") == 5000


def test_a_gate_still_outranks_a_paid_entitlement(catalog, pro_plan) -> None:  # type: ignore[no-untyped-def]
    # Paying for something does not out-rank a suspension. The gate is
    # authoritative in both directions, and that has to survive composition.
    features = _features(catalog, pro_plan.id, gate=lambda f, s, c: False)
    assert features.can_access("use-mcp", "u") is False


def test_enabled_lists_what_the_plan_grants(catalog, pro_plan) -> None:  # type: ignore[no-untyped-def]
    features = _features(catalog, pro_plan.id)
    assert sorted(features.enabled("u")) == ["ai-tokens", "use-mcp"]


# --- Async resolvers ------------------------------------------------------


def test_an_async_subscription_resolver_works_through_the_async_api(catalog, pro_plan) -> None:  # type: ignore[no-untyped-def]
    async def resolve(subject, context=None):  # type: ignore[no-untyped-def]
        await asyncio.sleep(0)
        return Subscription(id="sub", product_id=pro_plan.id)

    features = create_features(
        sources=[create_catalog_feature_source(catalog, resolve_subscription=resolve)]
    )
    assert asyncio.run(features.acan_access("use-mcp", "u")) is True
    assert asyncio.run(features.aremaining("ai-tokens", "u")) == 100


def test_an_async_resolver_is_refused_by_the_sync_api_rather_than_silently_denying(
    catalog, pro_plan
) -> None:  # type: ignore[no-untyped-def]
    # The failure this guards is nasty: a coroutine is truthy but is not a list
    # of grants, so a bridge that shrugged would resolve to "entitled to
    # nothing" -- a denial indistinguishable from a correct one.
    from fancy_features import FeatureAsyncRequiredError

    async def resolve(subject, context=None):  # type: ignore[no-untyped-def]
        await asyncio.sleep(0)
        return Subscription(id="sub", product_id=pro_plan.id)

    features = create_features(
        sources=[create_catalog_feature_source(catalog, resolve_subscription=resolve)]
    )
    with pytest.raises(FeatureAsyncRequiredError):
        features.can_access("use-mcp", "u")


def test_the_bridge_has_no_method_the_manager_never_calls(catalog) -> None:  # type: ignore[no-untyped-def]
    # `FeatureManager` only ever calls `grants_for`. An `agrants_for` sitting
    # beside it would be dead code that a host with an async resolver would
    # reasonably assume was being used -- and they would silently get no grants.
    assert not hasattr(CatalogFeatureSource, "agrants_for")


# --- The contract's own null-quantity disagreement -------------------------


def test_a_pivot_row_with_no_included_quantity_is_unlimited(catalog, pro_plan) -> None:  # type: ignore[no-untyped-def]
    """THE cross-runtime disagreement, pinned at the seam where it bites.

    ``included_quantity`` is nullable. The shared contract spec and
    ``fancy-features-js`` both read null as **unlimited**; the PHP ``Fms``
    service reads it as **deny** (``remaining()`` returns null, and ``can()``
    denies on null). Same column, opposite meanings, two shipped packages.

    This port follows the written spec -- which is why it is not a coin flip --
    and the divergence is reported rather than absorbed.
    """
    feature = catalog.create_product_feature("api-calls", "API calls", type="resource")
    catalog.attach_feature(pro_plan.id, feature.id, enabled=True, included_quantity=None)

    features = _features(catalog, pro_plan.id)
    assert features.can_access("api-calls", "u") is True
    assert features.remaining("api-calls", "u") is None
    features.increment("api-calls", "u", 1_000_000)
    assert features.can_access("api-calls", "u") is True


def test_the_overage_limit_is_carried_and_now_enforced(catalog, pro_plan) -> None:  # type: ignore[no-untyped-def]
    """This test used to pin ``overage_limit`` as decorative. It is the day.

    The field was on the pivot, on the grant and in the contract, and no
    resolution path in PHP, Node or Python consulted it. The pin existed so that
    whoever implemented it would be told, rather than the field quietly staying
    decorative for another year. `fancy-features` 0.2.0 makes it a CEILING on
    billable consumption past the included quantity.
    """
    feature = catalog.create_product_feature("calls", "Calls", type="resource")
    catalog.attach_feature(
        pro_plan.id, feature.id, enabled=True, included_quantity=10, overage_limit=5
    )
    features = _features(catalog, pro_plan.id)
    # Overage is permitted only where it can be recorded; the bundled store can,
    # and a listener is what a host would use to reach an invoice.
    recorded: list[int] = []
    features.on_overage(lambda event: recorded.append(event.units))

    features.increment("calls", "u", 10)
    assert features.remaining("calls", "u") == 0

    # Five billable units above the line, and then the ceiling.
    assert features.try_consume("calls", "u", 5) is True
    assert features.overage_for("calls", "u") == 5
    assert recorded == [5]
    assert features.try_consume("calls", "u", 1) is False


def test_an_unset_overage_limit_still_stops_at_the_included_quantity(catalog, pro_plan) -> None:  # type: ignore[no-untyped-def]
    """The other half, and the one that keeps the ruling opt-in.

    Every pivot row written before this release has ``overage_limit`` unset,
    because nothing read it. `None` therefore has to mean NO overage: reading it
    as unbounded would turn each untouched row into an unlimited spending
    authority the moment this shipped.
    """
    feature = catalog.create_product_feature("emails", "Emails", type="resource")
    catalog.attach_feature(pro_plan.id, feature.id, enabled=True, included_quantity=10)
    features = _features(catalog, pro_plan.id)
    features.on_overage(lambda event: None)

    features.increment("emails", "u", 10)
    assert features.try_consume("emails", "u", 1) is False
    assert features.overage_for("emails", "u") == 0
