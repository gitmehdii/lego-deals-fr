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


# A merchant's title is mostly packaging. Everything below is noise that says
# nothing about which set is being sold, and leaving it in drags a fuzzy score
# toward whichever catalogue entry happens to share the filler.
#
# French first, because that is what Dealabs writes, then the English articles
# the Rebrickable names carry, since both sides go through _strip_stopwords
# before being compared.
_WHAT_THE_PRODUCT_IS = (
    "lego", "legos", "set", "sets", "coffret", "coffrets", "boite", "boites",
    "jouet", "jouets", "jeu", "jeux", "construction", "constructions",
    "brique", "briques", "piece", "pieces", "pcs",
    "figurine", "figurines", "minifigurine", "minifigurines",
)  # fmt: skip

_WHAT_THE_DEAL_IS = (
    "promo", "promotion", "promotions", "reduction", "reductions",
    "remise", "remises", "solde", "soldes", "bon", "bons", "plan", "plans",
    "deal", "deals", "offre", "offres", "prix", "cher", "pas", "achat",
    "precommande", "precommandes", "neuf", "occasion",
    "livraison", "gratuite", "gratuit",
)  # fmt: skip

# Dealabs titles are full of this: "via 3€ de cagnotte sur la carte fidélité".
_LOYALTY_SCHEMES = (
    "cagnotte", "cagnottes", "fidelite", "carte", "cartes",
    "ticket", "tickets", "boutique", "officielle", "officiel",
    "selection", "ex", "exemple", "soit", "via",
)  # fmt: skip

_FRENCH_GRAMMAR = (
    "le", "la", "les", "l", "un", "une", "des", "du", "de", "d",
    "au", "aux", "a", "et", "en", "sur", "pour", "avec", "dans", "par",
    "lieu", "plus", "moins", "jusqu", "jusqua",
)  # fmt: skip

# The Rebrickable names carry these, and both sides of a comparison go through
# _strip_stopwords.
_ENGLISH_ARTICLES = (
    "the", "an", "of", "and", "to", "for", "with", "in", "on", "at", "from",
)  # fmt: skip

_STOPWORDS = frozenset().union(
    _WHAT_THE_PRODUCT_IS,
    _WHAT_THE_DEAL_IS,
    _LOYALTY_SCHEMES,
    _FRENCH_GRAMMAR,
    _ENGLISH_ARTICLES,
)

# "via 3€ de fidélité", "-50%", "79,99€" and friends: anything with a currency
# or percent sign attached is about the deal, not about the product.
_MONEY_OR_PERCENT = re.compile(
    r"\d[\d.,\s]*\s*(?:€|eur|%)|(?:€|eur)\s*\d[\d.,]*", re.IGNORECASE
)


def _strip_stopwords(text: str) -> str:
    """Drop filler words. Applied to both sides of a fuzzy comparison.

    Numbers survive: a token like "10497" is the strongest signal a title can
    carry, and strategy one depends on it.
    """
    return " ".join(word for word in text.split() if word not in _STOPWORDS)


def normalize_title(value: str, *, merchant: str | None = None) -> str:
    """Reduce a merchant's title to the words that identify a set.

    "Jouet de construction Lego City 60511 - Le train à vapeur rétro"
    becomes "city 60511 train vapeur retro".

    The merchant name is passed in rather than guessed: the source already
    knows it, and a hardcoded list of shop names would rot.
    """
    without_money = _MONEY_OR_PERCENT.sub(" ", value)
    normalized = normalize_name(without_money)
    if merchant:
        merchant_words = set(normalize_name(merchant).split())
        normalized = " ".join(
            word for word in normalized.split() if word not in merchant_words
        )
    return _strip_stopwords(normalized)


def comparison_key(name_normalized: str) -> str:
    """The form a catalogue name takes when compared against a title.

    Kept separate from `normalize_name` so that `sets.name_normalized` stays
    exactly what schema.sql describes, while both sides of a fuzzy comparison
    still get the same filler removed.
    """
    return _strip_stopwords(name_normalized)
