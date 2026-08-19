# Changelog

All notable changes to `fancy-catalog` (Python) are documented here, in
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) format.

This package is pre-1.0, so **breaking changes land in MINOR releases**. The
version number is not a promise it can yet keep; the entries are.

## [Unreleased]

## [0.1.0] - unreleased

The first cut: the domain model, exact money, Stripe sync and checkout, and the
bridge into `fancy-features`.

### Added

- **`Catalog` / `create_catalog`** — the port of PHP `CatalogManager`. Stores,
  Stripe sync, checkout, and terse authoring helpers
  (`create_product`, `create_price`, `create_product_feature`, `attach_feature`)
  with ULIDs assigned automatically.
- **`fancy_catalog.money`** — money as integer minor units, and the reason this
  module exists: **neither twin owns this conversion**, so every consumer writes
  `int(price * 100)` in application code where nothing checks it.
  - `to_minor_units` goes through `decimal.Decimal` and **refuses a float**.
    `int(19.99 * 100)` is 1998 in every IEEE-754 language, and rounding instead
    of truncating only moves the bug: `8.615 * 1000` is `8614.999999999999`.
  - `format_minor_units` is the exact inverse, zero-padded, sign in front.
  - `line_total` is exact integer multiplication — trivial here, and the reason
    a Node implementation needs `BigInt` past 2^53.
  - `currency_exponent` reads the ISO-4217 exponent. A hard-coded 100 charges a
    JPY customer a hundred times the price and a KWD customer a tenth of it.
  - `MoneyPrecisionError` when an amount carries more precision than its
    currency has. It **raises rather than rounding**: only the caller knows
    whether the extra digit is a rounding question or a typo.
  - All of it pinned by the new shared `shared/money-minor-units` conformance
    suite (26 rows).
- **`StripeCatalogSync`** — create-or-update for products; archive-and-replace
  for prices, because Stripe prices are immutable. `transfer_lookup_key` on both
  the create and the metadata-only update paths.
- **`StripeCheckout`** — subscription and one-time sessions, with the PHP
  Cashier "owner" replaced by the Stripe customer id the host resolved.
- **Store protocols with in-memory defaults**, soft-delete aware, and with the
  schema's `UNIQUE(product_id, product_feature_id)` enforced so an in-memory
  test cannot pass on state a real database would reject.
- **`CATALOG_LIVE`** — the Live Contract, with a parity test that reads
  `LaravelCatalog\LiveContract` straight out of the PHP source.
- **`fancy_catalog.features`** — the catalog as a `FeatureSource`, behind the
  `features` extra.
- **Zero required runtime dependencies.** `stripe` is injected through a narrow
  protocol; the ULID generator is thirty lines of standard library.

### Changed relative to the twins

- **Structures are compared with SORTED keys** during price-change detection.
  Both twins compare tiers, `transform_quantity` and `custom_unit_amount` with
  `json_encode` / `JSON.stringify`, which is insertion-ordered — so the same
  tier list read back from Stripe with its keys in another order reads as a
  change, archives a working price and creates a duplicate.

  *What a consumer must do:* nothing. It only ever removes spurious
  replacements.

- **A null metadata value is dropped rather than stringified.** Stripe treats an
  empty-string metadata value as a deletion and `"None"` as a literal; neither
  is what a null field means.

- **The bridge IMPORTS the shared feature contract instead of mirroring it.**
  `@particle-academy/fancy-catalog/features` re-declares `FeatureGrant` and
  `FeatureSource` verbatim and relies on TypeScript's structural typing to keep
  the copy honest. Python has no cross-distribution equivalent, so there is one
  definition and `fancy-features` is a real extra.

  *What a consumer must do:* `pip install "fancy-catalog[features]"` if you use
  the bridge. Nothing if you do not — `fancy_catalog` never imports it.

### Notes for a consumer

- **Pass `stripe.StripeClient(key).v1`.** On stripe-python 15.x the bare
  `StripeClient.products` still works but emits a `DeprecationWarning` on every
  access, and a host running with warnings-as-errors would see a catalog sync
  crash. Verified against 15.5.1.
- **`overage_limit` is carried and not enforced**, here and in both twins. It
  reaches a `FeatureGrant` and no resolution path reads it. Pinned by a test so
  the day it is implemented, something says so.
