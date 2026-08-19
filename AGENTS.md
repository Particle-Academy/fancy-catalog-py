# AGENTS.md — fancy-catalog-py

Headless Stripe catalog: products, prices, plans, checkout. The Python twin of
`particle-academy/laravel-catalog` and `@particle-academy/fancy-catalog`.
`CLAUDE.md` symlinks here.

This file describes **this package's code**. Process rules — publishing, kit
versioning, backports, the issue protocol — live in the envelope's `AGENTS.md`
and are deliberately not repeated.

## What this package is

A **port**, not a redesign. Behaviour questions are settled against
`laravel-catalog`'s `src/Services/StripeCatalogService.php` and
`src/Services/StripeCheckoutService.php` for the Stripe semantics, and against
`@particle-academy/fancy-catalog` for the headless adapter shape.

Deliberate divergences are in a docstring at the point of divergence AND in
[`.ai/plans/fancy-python-commerce-gating.md`](../../.ai/plans/fancy-python-commerce-gating.md).

## Architecture

`src/` layout, **zero required runtime dependencies**.

- `types.py` — `Product`, `Price`, `ProductFeature`, `ProductFeatureConfig`,
  `Subscription`. Fields and nullability match the migrations.
- `money.py` — integer minor units. **The only place a decimal amount becomes
  one.** Pinned by the shared `shared/money-minor-units` conformance suite.
- `stores.py` — the persistence protocols and in-memory defaults, soft-delete
  aware.
- `stripe_client.py` — the injected-client protocol. Six operations.
- `stripe_sync.py` — product and price sync, including the immutability rules.
- `stripe_checkout.py` — Checkout sessions.
- `catalog.py` — the unified surface plus terse authoring helpers.
- `live.py` — the Live Contract, mirrored by the PHP twin and asserted against it.
- `features.py` — the `FeatureSource` bridge. **Not imported by anything else
  here.**
- `ulid.py` — a ULID in thirty lines of standard library.

## The invariants

**Money is an integer in the currency's minor unit, and never a float.**
`to_minor_units` **refuses a float argument outright**: by the time an amount is
a float the precision is gone. `int(19.99 * 100)` is 1998 in every IEEE-754
language, and swapping truncation for rounding only moves the bug —
`8.615 * 1000` is `8614.999999999999`, so a rounded conversion gives 8614. The
conversion goes through `decimal.Decimal` and nothing else.

**Excess precision raises rather than rounding.** `to_minor_units("0.005", 2)`
is a `MoneyPrecisionError`. Silently rounding a payment amount is how fractions
of a unit go missing one transaction at a time, and only the caller knows
whether the extra digit is a rounding question or a typo.

**A currency's exponent is looked up, never assumed.** A hard-coded 100 is wrong
for roughly a quarter of the world's currencies — by a factor of a hundred for
JPY and a factor of ten for KWD.

**Stripe prices are immutable.** A change to any compared field archives the old
price (`active: false`) and creates a new one; the shared internal ULID rides in
`metadata.price_id` so the two stay linked. This is the single most important
behaviour in the package: getting it wrong means the customer keeps paying the
old amount and the sync still reports success. `tests/test_catalog_and_stripe.py`
has one parametrised row **per compared field** for exactly that reason.

**A lookup key is transferred, not re-created.** Without `transfer_lookup_key`
the archive-and-replace above fails with "lookup key already exists" the first
time anyone reprices something that has one. It is also sent on the
metadata-only update path, because changing *only* the lookup key leaves pricing
untouched and would otherwise never reach Stripe.

**Structures are compared with SORTED keys.** Both twins compare tiers and
`transform_quantity` with `json_encode` / `JSON.stringify`, which is
insertion-ordered — so the same tier list read back from Stripe with its keys in
another order reads as a change and archives a price that did not need
replacing. `_canonical()` sorts. This is a deliberate divergence and it is
tested.

**`test_connection` never leaks a Stripe error.** A raw API error can carry
account identifiers and key prefixes, and that result is built for an admin
screen.

## Stripe is injected, not depended on

`stripe` is not a runtime dependency. `stripe_client.py` declares the six
operations this package uses, and a real client satisfies them structurally.

**Pass `stripe.StripeClient(key).v1`, not `stripe.StripeClient(key)`.** On
stripe-python 15.x the bare `StripeClient.products` still works but emits a
`DeprecationWarning` on every access, and a host running `-W error` would turn a
catalog sync into a crash. Verified against 15.5.1 on 2026-08-18.

**No test in this package touches the network.** `tests/fake_stripe.py` records
every call so the assertions are about the *request* — which is where this
package's behaviour lives.

## The feature bridge imports the contract; it does not mirror it

`fancy_catalog.features` imports `FeatureGrant` / `FeatureSource` from
`fancy_features.contract`, and `fancy-features` is a real extra
(`pip install "fancy-catalog[features]"`).

The TypeScript pair duplicates those types verbatim and relies on structural
typing to keep the copy assignable. `fancy-conformance`'s own README names that
pair as the case that "survives only because TypeScript's structural typing does
the checking". Python has no cross-distribution equivalent: `Protocol` would
check the *shape*, and nothing would check that `FeatureGrant`'s **fields**
still match.

`tests/test_features_bridge.py::test_there_is_exactly_one_definition_of_the_grant_type`
fails if anyone adds a mirrored copy back.

`fancy_catalog` itself never imports the bridge, so a host with no gating layer
installs nothing extra.

## Testing

```bash
python -m pytest        # 141 tests, offline, no install required
ruff check . && ruff format --check .
mypy
```

Two suites need something beside the repository, and **neither skips silently**:

- the money conformance table needs `fancy-conformance` (resolution fails loudly
  if absent);
- the Live Contract parity check reads `laravel-catalog/src/LiveContract.php`
  and **warns** when it is not on disk rather than passing quietly. CI checks it
  out so the warning never fires there.

Every load-bearing behaviour has been mutation-checked: routing the money
conversion through a float, dropping the fraction padding, assuming exponent 2,
updating instead of archive-and-replace, dropping `transfer_lookup_key`,
comparing with insertion order, and ignoring soft deletes each fail a named
test.
