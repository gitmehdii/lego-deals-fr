"""Price parsing. Pure functions, no I/O.

Everything a source hands us is a human-written string. This module turns it
into euros or into nothing at all, and never into a wrong number: a price that
is guessed badly ends up in price_points forever, and price_points is the one
table nobody could rebuild afterwards.
"""

import re

# The three characters French prices use to group thousands: plain space,
# no-break space, narrow no-break space. Dealabs emits the narrow one.
#
# Built with chr() rather than typed literally: two of the three are invisible
# in a source file, and an invisible character inside a regex is a bug waiting
# for someone to "clean up the whitespace".
_THOUSANDS = " " + chr(0x00A0) + chr(0x202F)

# "158,90€", "130€", "79.99 €", "1 299,00 EUR". The decimal part is optional
# because Dealabs publishes round prices without one.
_AMOUNT = re.compile(
    r"(?<![\d,.])(\d{1,3}(?:[" + _THOUSANDS + r"]\d{3})*|\d+)"
    r"(?:[.,](\d{1,2}))?\s*(?:€|EUR\b)",
    re.IGNORECASE,
)

# "79€99": the euro sign stands in for the decimal separator, a French habit
# the pattern above would otherwise read as a plain "79".
_EURO_AS_SEPARATOR = re.compile(r"(?<![\d,.])(\d+)\s*€\s*(\d{2})(?![\d.,])")

_SEPARATORS = re.compile("[" + _THOUSANDS + "]")

# Nothing LEGO sells is worth either of these. A match outside the range means
# the pattern latched onto something that was not a price.
_MIN_PLAUSIBLE_EUR = 0.5
_MAX_PLAUSIBLE_EUR = 100_000.0


def parse_price_eur(value: str | None) -> float | None:
    """First plausible euro amount in the text, or None.

    None is a perfectly good answer: SPEC.md keeps such an offer in the
    database and merely leaves it out of detection.
    """
    if not value:
        return None

    if match := _EURO_AS_SEPARATOR.search(value):
        return _plausible(f"{match.group(1)}.{match.group(2)}")

    if match := _AMOUNT.search(value):
        digits = _SEPARATORS.sub("", match.group(1))
        return _plausible(f"{digits}.{match.group(2) or '0'}")

    return None


def _plausible(number: str) -> float | None:
    try:
        amount = float(number)
    except ValueError:
        return None
    return amount if _MIN_PLAUSIBLE_EUR <= amount <= _MAX_PLAUSIBLE_EUR else None
