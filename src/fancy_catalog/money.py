"""Money, in integer minor units, with no float anywhere in the path.

Stripe stores an amount as an integer in the currency's smallest unit -- 1999
for $19.99, 1000 for JPY 1000, 1005 for KWD 1.005. Both twins of this package
(``laravel-catalog`` and ``@particle-academy/fancy-catalog``) store the same
integer and **neither converts to or from a decimal string**, so every consumer
writes that conversion themselves, in application code, where nothing checks it.

This module is that conversion, done once and pinned by the shared
``shared/money-minor-units`` conformance suite.

## The bug it exists to stop

``int(19.99 * 100)`` is **1998** in every IEEE-754 language, because the nearest
double to 19.99 is 19.98999999999999843... One cent, on every order, in the
direction the customer notices least.

Swapping truncation for rounding does not fix it, it moves it: ``8.615 * 1000``
is ``8614.999999999999`` as a double, so a rounded conversion gives 8614. The
only correct answer is not to involve a float at all, which is what
:func:`to_minor_units` does -- exact :class:`decimal.Decimal` scaling.

``fancy-conformance`` exists because a money bug got through two
implementations of ``fancy-mlm``. This is the same class of bug, one layer up.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

__all__ = [
    "MoneyPrecisionError",
    "THREE_DECIMAL_CURRENCIES",
    "ZERO_DECIMAL_CURRENCIES",
    "currency_exponent",
    "format_minor_units",
    "line_total",
    "to_minor_units",
]


class MoneyPrecisionError(ValueError):
    """An amount carries more precision than its currency has.

    ``to_minor_units("0.005", 2)`` raises rather than returning 0 or 1. Silently
    rounding a payment amount is how half a cent per transaction goes missing,
    and the caller is the only one who knows whether the extra digit is a
    rounding question or a typo.
    """


#: Currencies with no minor unit, per Stripe's zero-decimal list. An amount in
#: one of these is passed to Stripe as-is: JPY 1000 is ``unit_amount: 1000``,
#: not 100000.
ZERO_DECIMAL_CURRENCIES = frozenset(
    {
        "BIF", "CLP", "DJF", "GNF", "JPY", "KMF", "KRW", "MGA",
        "PYG", "RWF", "UGX", "VND", "VUV", "XAF", "XOF", "XPF",
    }
)  # fmt: skip

#: Currencies with three minor digits.
#:
#: Stripe has an extra rule for these that this module does NOT enforce, because
#: it is a Stripe API constraint rather than an arithmetic one: the amount must
#: be a multiple of 10 (Stripe reads the value as if it had two decimals and
#: pads). A consumer charging KWD should check the current Stripe documentation;
#: converting 1.005 to 1005 here is arithmetically right either way.
THREE_DECIMAL_CURRENCIES = frozenset({"BHD", "IQD", "JOD", "KWD", "LYD", "OMR", "TND"})


def currency_exponent(currency: str) -> int:
    """The ISO-4217 minor-unit exponent for a currency code.

    Two for most of the world, zero for JPY and its peers, three for the Gulf
    dinars. **A hard-coded 100 is wrong for roughly a quarter of the world's
    currencies** -- and wrong by a factor of ten or a hundred, not by a rounding
    error.
    """
    code = currency.strip().upper()
    if code in ZERO_DECIMAL_CURRENCIES:
        return 0
    if code in THREE_DECIMAL_CURRENCIES:
        return 3
    return 2


def to_minor_units(amount: str | int | Decimal, exponent: int = 2) -> int:
    """A decimal amount to whole minor units, exactly.

    ``to_minor_units("19.99")`` is ``1999``. ``to_minor_units("1000", 0)`` is
    ``1000``. ``to_minor_units("1.005", 3)`` is ``1005``.

    A :class:`float` is **refused**: by the time an amount is a float it has
    already been rounded by the language, and this function cannot recover what
    was lost. Pass the string the user typed, or a :class:`~decimal.Decimal`.

    Raises :class:`MoneyPrecisionError` when the amount has more precision than
    the currency does.
    """
    if isinstance(amount, float):
        raise TypeError(
            "to_minor_units refuses a float: 19.99 is not 19.99 as a double, and by the time "
            "an amount is a float the precision is already gone. Pass the decimal string the "
            "user typed, an int, or a decimal.Decimal."
        )
    if isinstance(amount, bool):
        raise TypeError(f"An amount must be a decimal string, int or Decimal; got {amount!r}.")
    if exponent < 0:
        raise ValueError(f"A minor-unit exponent cannot be negative; got {exponent}.")

    try:
        value = amount if isinstance(amount, Decimal) else Decimal(str(amount).strip())
    except InvalidOperation as exc:
        raise ValueError(f"{amount!r} is not a decimal amount.") from exc

    if not value.is_finite():
        raise ValueError(f"{amount!r} is not a finite amount.")

    scaled = value.scaleb(exponent)
    if scaled != scaled.to_integral_value():
        raise MoneyPrecisionError(
            f"{amount!r} has more precision than an exponent of {exponent} allows "
            f"({scaled} minor units). Round it deliberately before converting -- this "
            "refuses rather than choosing, because rounding a payment amount silently is "
            "how fractions of a unit go missing one transaction at a time."
        )
    return int(scaled)


def format_minor_units(minor: int, exponent: int = 2) -> str:
    """Whole minor units back to a plain decimal string. The inverse of :func:`to_minor_units`.

    ``format_minor_units(1999)`` is ``"19.99"``; ``format_minor_units(7)`` is
    ``"0.07"``, not ``"0.7"``; ``format_minor_units(-7)`` is ``"-0.07"``, with
    the sign in front of the whole amount rather than inside it.

    No thousands separator, no currency symbol, no locale. This is the machine
    representation; presentation is the host's business and depends on a locale
    this package has no opinion about.
    """
    if isinstance(minor, bool) or not isinstance(minor, int):
        raise TypeError(f"Minor units are a whole number; got {minor!r}.")
    if exponent < 0:
        raise ValueError(f"A minor-unit exponent cannot be negative; got {exponent}.")
    if exponent == 0:
        return str(minor)

    sign = "-" if minor < 0 else ""
    digits = str(abs(minor)).rjust(exponent + 1, "0")
    return f"{sign}{digits[:-exponent]}.{digits[-exponent:]}"


def line_total(unit_amount: int, quantity: int) -> int:
    """``unit_amount x quantity``, exactly.

    Trivial in Python, where integers are arbitrary precision -- and not trivial
    in the Node twin, where the product silently loses precision above 2^53 and
    needs ``BigInt``. It is a named function so the conformance table has
    something to hold all three runtimes to.
    """
    if isinstance(unit_amount, bool) or not isinstance(unit_amount, int):
        raise TypeError(f"A unit amount is whole minor units; got {unit_amount!r}.")
    if isinstance(quantity, bool) or not isinstance(quantity, int):
        raise TypeError(f"A quantity is a whole number; got {quantity!r}.")
    return unit_amount * quantity
