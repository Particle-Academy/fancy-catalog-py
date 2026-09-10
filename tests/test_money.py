"""Money arithmetic, and the shared conformance table that pins it.

Two halves:

* the ``shared/money-minor-units`` table, run through the loader the fixture
  package owns, printed unconditionally, and **failing loudly if the fixtures
  cannot be found** -- a conformance suite that silently does not run reads
  exactly like full coverage;
* the behaviours the table cannot express, chiefly the refusals.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from fancy_conformance import cases, format_summary, run_table, version

from fancy_catalog.money import (
    MoneyPrecisionError,
    currency_exponent,
    format_minor_units,
    line_total,
    to_minor_units,
)

SUITE = "shared/money-minor-units"

#: Moved deliberately, never automatically. A pin that follows whatever is on
#: disk asserts nothing.
# Moved to 0.20.0 on 2026-09-10, deliberately and not to get to green: every
# table above was re-run against the checkout FIRST and every row passes, with
# the only skip being the documented cross-engine one. shared/decimal 26 rows.
#
# Five ports had drifted to a pin this stale at once, which says the failure is
# structural rather than anyone forgetting: the pin only moves when a human
# re-runs the tables, and nothing prompts that when the fixture package ships.
PINNED_SUITE_VERSION = "0.20.0"

_IMPL = {
    "toMinorUnits": lambda i: to_minor_units(i["amount"], i["exponent"]),
    "formatMinorUnits": lambda i: format_minor_units(i["minor"], i["exponent"]),
    "lineTotal": lambda i: line_total(i["unitAmount"], i["quantity"]),
}


def test_the_pinned_fixture_version_is_what_is_on_disk() -> None:
    assert version() == PINNED_SUITE_VERSION, (
        f"fancy-conformance is at {version()}, this suite is pinned to "
        f"{PINNED_SUITE_VERSION}. Re-run the suites and move the pin deliberately."
    )


def test_money_conformance(capsys: pytest.CaptureFixture[str]) -> None:
    summary = run_table(SUITE, lambda case: _IMPL[case["fn"]](case["input"]))
    with capsys.disabled():
        # Printed unconditionally, pass or fail. A summary only shown on failure
        # cannot tell anyone that the suite ran at all.
        print("\n" + format_summary(summary))
    assert summary["ok"], format_summary(summary)


def test_the_conformance_table_is_not_empty() -> None:
    # Guards the shape of the failure that matters most: a loader that resolves,
    # returns nothing, and reports "0 failed".
    assert len(cases(SUITE)) >= 20


# --- What the table cannot express: the refusals --------------------------


def test_a_float_amount_is_refused() -> None:
    # By the time an amount is a float the precision is already gone, and this
    # function cannot recover it. Accepting 19.99 and returning 1999 would work
    # by luck for that literal and fail for 8.615.
    with pytest.raises(TypeError, match="refuses a float"):
        to_minor_units(19.99, 2)


def test_excess_precision_is_refused_rather_than_rounded() -> None:
    with pytest.raises(MoneyPrecisionError, match="more precision"):
        to_minor_units("0.005", 2)


def test_excess_precision_is_refused_for_a_zero_decimal_currency() -> None:
    with pytest.raises(MoneyPrecisionError):
        to_minor_units("1000.50", 0)


def test_a_decimal_is_accepted_directly() -> None:
    assert to_minor_units(Decimal("19.99"), 2) == 1999


def test_an_int_is_accepted_directly() -> None:
    assert to_minor_units(20, 2) == 2000


def test_a_bool_is_not_an_amount() -> None:
    with pytest.raises(TypeError):
        to_minor_units(True, 2)


def test_nonsense_is_refused() -> None:
    with pytest.raises(ValueError, match="not a decimal amount"):
        to_minor_units("nineteen ninety nine", 2)


def test_infinity_is_refused() -> None:
    # `Decimal("Infinity")` parses. `int()` of it raises something unhelpful.
    with pytest.raises(ValueError, match="not a finite amount"):
        to_minor_units("Infinity", 2)


def test_a_negative_exponent_is_refused() -> None:
    with pytest.raises(ValueError, match="cannot be negative"):
        to_minor_units("19.99", -1)
    with pytest.raises(ValueError, match="cannot be negative"):
        format_minor_units(1999, -1)


def test_line_total_refuses_a_float_quantity() -> None:
    with pytest.raises(TypeError):
        line_total(1999, 2.5)


def test_line_total_refuses_a_bool() -> None:
    with pytest.raises(TypeError):
        line_total(1999, True)


# --- Properties -----------------------------------------------------------


@pytest.mark.parametrize("exponent", [0, 2, 3])
@pytest.mark.parametrize("minor", [0, 1, 7, 99, 100, 1999, -1, -7, -1999, 900719925474099])
def test_the_conversion_round_trips(minor: int, exponent: int) -> None:
    # The property the two functions owe each other. A formatting bug that the
    # table happens not to cover still fails here.
    assert to_minor_units(format_minor_units(minor, exponent), exponent) == minor


def test_line_total_is_exact_far_beyond_a_double() -> None:
    # The row the conformance table deliberately cannot carry: past 2^53 a JSON
    # golden could not survive `JSON.parse` either. Python integers are
    # arbitrary precision, so the assertion belongs here, and it documents what
    # the Node twin will need `BigInt` for.
    assert line_total(999_999_999_999, 999_999_999) == 999_999_999_999 * 999_999_999
    assert line_total(2**53 + 1, 3) == (2**53 + 1) * 3


def test_a_refund_line_is_the_exact_negation_of_its_charge() -> None:
    assert line_total(-1999, 7) == -line_total(1999, 7)


# --- Currency exponents ---------------------------------------------------


@pytest.mark.parametrize(
    ("currency", "exponent"),
    [
        ("USD", 2),
        ("usd", 2),
        (" EUR ", 2),
        ("GBP", 2),
        ("JPY", 0),
        ("KRW", 0),
        ("VND", 0),
        ("XOF", 0),
        ("KWD", 3),
        ("BHD", 3),
        ("OMR", 3),
        ("ZZZ", 2),
    ],
)
def test_currency_exponents(currency: str, exponent: int) -> None:
    assert currency_exponent(currency) == exponent


def test_a_yen_price_is_not_multiplied_by_a_hundred() -> None:
    # The concrete failure `currency_exponent` prevents: JPY 1000 charged as
    # 100000 is a hundred times the price, and it looks plausible in a log.
    assert to_minor_units("1000", currency_exponent("JPY")) == 1000
    assert to_minor_units("1000", currency_exponent("USD")) == 100_000
