"""Text normalisation. Pure functions, no I/O, no database, no HTTP.

Fuzzy resolution compares a merchant's title to `sets.name_normalized`, so both
sides have to be flattened the same way. This module owns that flattening.
"""

import re
import unicodedata

# Letters NFKD leaves untouched because they are not accented forms of a base
# letter but letters in their own right. Without this they would be swallowed
# by the punctuation rule below, which splits a word in two and costs real
# fuzzy-match accuracy: "Jabłkowy" would flatten to "jab kowy".
#
# Measured on the Rebrickable dump: 13 set names are affected. The French
# entries matter more than that count suggests, because they also turn up in
# the merchant titles resolution has to read.
_TRANSLITERATIONS = str.maketrans(
    {
        "ß": "ss",
        "æ": "ae",
        "œ": "oe",
        "ø": "o",
        "ł": "l",
        "đ": "d",
        "ð": "d",
        "þ": "th",
    }
)

# Everything else non-alphanumeric becomes a single space: dashes, curly
# quotes, bullets, zero-width spaces and emoji all separate words rather than
# belong to one.
_NON_ALPHANUMERIC = re.compile(r"[^0-9a-z]+")


def normalize_name(value: str) -> str:
    """Lowercase, accent-free, punctuation-free, single-spaced.

    "Café Corner" -> "cafe corner", "R2-D2" -> "r2 d2". Digits are kept: a set
    number inside a name is a signal, not noise.

    Applied to `sets.name` at import time because every offer is matched
    against the result, so it must never be recomputed at resolution time.
    """
    lowered = value.lower().translate(_TRANSLITERATIONS)
    decomposed = unicodedata.normalize("NFKD", lowered)
    without_accents = "".join(
        char for char in decomposed if not unicodedata.combining(char)
    )
    return _NON_ALPHANUMERIC.sub(" ", without_accents).strip()
