# Using fancy-catalog (Python) — guide for agents

How an agent installs and uses this package in a consumer app. What the package
is, is in [README.md](./README.md). Notes for working ON this repo's code are in
[CONTRIBUTING.md](./CONTRIBUTING.md).

## Install

```bash
pip install fancy-catalog
pip install "fancy-catalog[features]"   # + the entitlement bridge (fancy-features)
```

- **Python** 3.11+. No required runtime dependencies.
- `stripe` is not a dependency: bring your own client (the `stripe` extra exists for
  convenience only).
- The PyPI name is `fancy-catalog`; the import is `fancy_catalog`; the repo is
  `fancy-catalog-py`. Twins: `particle-academy/laravel-catalog` (PHP) and
  `@particle-academy/fancy-catalog` (Node).

## Find it on the Fancy MCP server

The catalog server is `https://ui.particle.academy/mcp` (streamable HTTP, no API
key; MCP id `fancy-ui`). This package is the registry entry `fancy-catalog-py`.

| Need | Tool |
|---|---|
| Find a server-side package by capability | `search-backend-packages` (stack `python`) |
| Exact install commands for a slug | `install-instructions` |
| Is the app on current versions? | `check-versions` |

## API essentials

- **`create_catalog(stripe=client, products=..., ...)`** returns the catalog:
  `create_product`, `create_price`, `sync_price`, `sync_product_and_prices`,
  `subscription_checkout_url`.
- **Stores:** `ProductStore`, `PriceStore`, `ProductFeatureStore` are protocols with
  in-memory, soft-delete-aware defaults.
- **Money:** `to_minor_units(amount_str, exponent)`, `format_minor_units`,
  `currency_exponent("JPY")`.
- **Gating:** `fancy_catalog.features.create_catalog_feature_source(catalog,
  resolve_subscription=...)` plugs into `fancy_features.create_features(sources=[...])`.

## Traps

- **Pass `stripe.StripeClient(api_key).v1`, not the bare client.** On stripe-python
  15.x the bare client emits a `DeprecationWarning` on every access, which crashes a
  host running warnings-as-errors.
- **Pass decimal amounts as strings (`"19.99"`), never floats.** `to_minor_units(19.99, 2)` is a
  `TypeError`; excess precision (`"0.005"` at exponent 2) raises
  `MoneyPrecisionError` rather than rounding.
- **Look the exponent up with `currency_exponent`.** Assuming 2 is wrong for JPY,
  KWD and others.
- **Prices are immutable on Stripe.** Changing a compared field and syncing archives
  the old price and creates a new one, so `price.external_id` changes.
- **A tiered or custom-amount price has `unit_amount = None`**, which is not zero.
