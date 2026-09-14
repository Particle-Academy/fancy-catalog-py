# fancy-catalog

[![Fancified](art/fancified.svg)](https://particle.academy)

**A headless Stripe catalog for Python** — products, prices, plans and checkout,
with money as integer minor units and persistence behind adapters. No web
framework, no ORM, and the Stripe SDK is injected rather than depended on.

The Python twin of [`particle-academy/laravel-catalog`](https://github.com/Particle-Academy/laravel-catalog)
(PHP) and [`@particle-academy/fancy-catalog`](https://github.com/Particle-Academy/fancy-catalog-js)
(Node/TypeScript).

```bash
pip install fancy-catalog
pip install "fancy-catalog[features]"   # + the entitlement bridge
```

## In one minute

```python
import stripe
from fancy_catalog import create_catalog

# NOTE the .v1 -- see "Passing a Stripe client" below.
catalog = create_catalog(stripe=stripe.StripeClient(api_key).v1)

pro = catalog.create_product("Pro plan", description="Everything, monthly")
price = catalog.create_price(pro.id, currency="USD", amount="19.99", recurring_interval="month")

price.unit_amount  # 1999   -- exactly, not 1998
catalog.sync_product_and_prices(pro)
catalog.subscription_checkout_url(price, customer="cus_123", success_url="...", cancel_url="...")
```

## Money is the point

Stripe stores an amount as an integer in the currency's smallest unit. A human
types a decimal string. **The conversion between them is where money is lost,
and neither twin owns it** — so every consumer writes it themselves:

```python
int(19.99 * 100)  # 1998.  One cent, on every order.
round(8.615 * 1000)  # 8614.  Rounding does not fix it, it moves it.
```

```python
from fancy_catalog import to_minor_units, format_minor_units, currency_exponent

to_minor_units("19.99", 2)  # 1999
to_minor_units("8.615", 3)  # 8615
to_minor_units("1000", currency_exponent("JPY"))  # 1000, not 100000
to_minor_units("1.005", currency_exponent("KWD"))  # 1005
format_minor_units(-7, 2)  # "-0.07"

to_minor_units(19.99, 2)  # TypeError -- a float has already lost it
to_minor_units("0.005", 2)  # MoneyPrecisionError -- rounds nothing silently
```

Pinned by the shared
[`shared/money-minor-units`](https://github.com/Particle-Academy/fancy-conformance)
conformance table, so a second implementation in any language inherits the
behaviour rather than the assumption.

## Prices are immutable, and this package knows it

A change to the amount, currency, interval, billing scheme, tiers,
`transform_quantity` or `custom_unit_amount` **archives** the Stripe price and
**creates a replacement**; a shared ULID in `metadata.price_id` keeps the two
linked. A change to only the metadata, active flag or lookup key updates in
place — and the lookup key is *transferred*, which is what stops the next
reprice failing with "lookup key already exists".

```python
price.unit_amount = 2999
catalog.sync_price(price)  # old price archived, new one created
price.external_id  # a NEW Stripe id
```

## Passing a Stripe client

`stripe` is **not** a dependency. The client is injected and used through six
operations declared in `fancy_catalog.stripe_client`.

```python
catalog = create_catalog(stripe=stripe.StripeClient(api_key).v1)
```

**Use `.v1`.** On stripe-python 15.x the bare `StripeClient.products` still
works but emits a `DeprecationWarning` on every access, and a host running with
warnings-as-errors would see a catalog sync crash.

Any object with the same shape works — your own wrapper, a proxy, or a recorded
cassette. That is how this package's own suite runs entirely offline.

## Persistence

`ProductStore`, `PriceStore` and `ProductFeatureStore` are protocols with
in-memory defaults. Soft deletes mirror the PHP `SoftDeletes` trait, because an
invoice referencing a hard-deleted product is an invoice nobody can explain.

```python
catalog = create_catalog(stripe=client, products=MyProductStore(), ...)
```

## Gating on what a plan includes

```python
from fancy_catalog.features import create_catalog_feature_source
from fancy_features import create_features

features = create_features(
    sources=[create_catalog_feature_source(catalog, resolve_subscription=lookup)],
)

features.can_access("use-mcp", user)  # via the user's plan's product features
features.remaining("ai-tokens", user)  # includedQuantity − usage
```

The bridge **imports** the shared contract from `fancy-features` rather than
mirroring it, so there is exactly one definition of `FeatureGrant` in the pair.
`fancy_catalog` itself never imports the bridge — a host with no gating layer
installs nothing extra.

## Requirements

Python 3.11+. No required runtime dependencies.

## License

MIT © Particle Academy
