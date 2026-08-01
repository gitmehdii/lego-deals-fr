"""Turning a merchant's title into a set number. Pure, no I/O.

The most interesting part of the project and the one that deserves the most
tests. One rule governs every trade-off here: **a wrong resolution is worse
than no resolution**, because it produces a confidently false alert. When in
doubt, score low and let the threshold discard it.
"""

import re
from dataclasses import dataclass
from typing import Literal

from rapidfuzz import fuzz, process

from bricks.core.normalize import comparison_key, normalize_title

ResolutionMethod = Literal["set_number", "fuzzy_name"]

# Official numbers run from 3 to 7 digits, but 3-digit runs match far too much
# ordinary text to be worth the risk.
_CANDIDATE = re.compile(r"\b(\d{4,7})\b")

# "1254 pièces", "3 893 pcs": a piece count is the single most common thing
# mistaken for a set number, and it always announces itself.
_PIECE_COUNT = re.compile(
    r"\b\d[\d\s.,]*\s*(?:pi[eè]ces?|pcs|briques?|parts?|elements?)\b", re.IGNORECASE
)

# Trailing variant, so "10497-1" and "10497" reach the same set.
_VARIANT_SUFFIX = re.compile(r"^(\d{4,7})-\d+$")

# Only used when several numbers in one title exist in the catalogue. Below any
# sane threshold on purpose: two plausible sets means we do not know which.
_AMBIGUOUS_SCORE = 0.5

# A four-digit number in this range is far more likely to be a year a human
# typed than a set someone is selling. SPEC.md names "LEGO Star Wars 2024" as
# the case to defeat.
#
# The range costs almost nothing: exactly 42 catalogue sets carry a number in
# it, and every one is a 1990s value pack or Duplo oddity that will never
# appear in a deal. They are still reachable, but only with corroboration.
_YEAR_LIKE = range(1990, 2036)

# A title with almost nothing left after normalisation gives fuzzy matching
# nothing to work with, and short strings are exactly where it invents things.
_MIN_FUZZY_TOKENS = 2

# How far the best name has to beat the second best to mean anything. A fuzzy
# score is only a number about one string; the distance to the runner-up is
# what says the catalogue can tell them apart at all.
#
# Measured against the real 27 810-set catalogue, on real titles with their
# number removed: "Le Bouquet de Roses" put `bouquet pink roses` at 0.86 and
# the correct `bouquet roses` at 0.81 — 0.05 apart, and the wrong one on top.
# "Transformers : Soundwave" won its by 0.22. The sample is thin, so the value
# is deliberately loose: it exists to reject ties, not to tune accuracy.
_DECISIVE_MARGIN = 0.10


@dataclass(frozen=True)
class CatalogueEntry:
    set_num: str
    name_normalized: str


@dataclass(frozen=True)
class Resolution:
    """What the resolver believes, before any threshold is applied.

    `score` and `method` are always populated, even when the finding will be
    rejected: SPEC.md keeps them so the quality of resolution can one day be
    measured rather than guessed.
    """

    set_num: str | None = None
    score: float = 0.0
    method: ResolutionMethod | None = None

    def accepted(self, threshold: float) -> bool:
        return self.set_num is not None and self.score >= threshold


UNRESOLVED = Resolution()


class SetIndex:
    """The catalogue, arranged for the two strategies. Built once per run."""

    def __init__(self, entries: list[CatalogueEntry]) -> None:
        self._by_number: dict[str, list[str]] = {}
        for entry in entries:
            self._by_number.setdefault(_bare_number(entry.set_num), []).append(
                entry.set_num
            )
        self._set_nums = [entry.set_num for entry in entries]
        # Both sides of a fuzzy comparison get the same filler removed.
        self._keys = [comparison_key(entry.name_normalized) for entry in entries]
        self._names = {entry.set_num: entry.name_normalized for entry in entries}

    def __len__(self) -> int:
        return len(self._set_nums)

    def match_number(self, number: str) -> list[str]:
        """Every catalogue set num sharing this bare number, variants included."""
        return self._by_number.get(number, [])

    def name_of(self, set_num: str) -> str:
        return self._names.get(set_num, "")

    def best_name_match(self, title: str) -> tuple[str, float, float] | None:
        """Best catalogue name for this title, its score, and the runner-up's.

        The runner-up is returned rather than discarded because the gap between
        the two is what says whether the winner means anything.
        """
        if not self._keys:
            return None
        top = process.extract(title, self._keys, scorer=fuzz.token_sort_ratio, limit=2)
        if not top:
            return None
        _, score, position = top[0]
        runner_up = top[1][1] / 100.0 if len(top) > 1 else 0.0
        return self._set_nums[position], score / 100.0, runner_up


def resolve(title: str, index: SetIndex, *, merchant: str | None = None) -> Resolution:
    """Best guess for `title`, with the confidence behind it.

    Strategy one reads a set number out of the title. Strategy two falls back
    to fuzzy matching on the name, and only runs when the first found nothing.
    """
    if by_number := _resolve_by_number(title, index):
        return by_number
    return _resolve_by_name(title, index, merchant)


def _resolve_by_number(title: str, index: SetIndex) -> Resolution | None:
    """None means "no number worth considering", not "no match"."""
    hunting_ground = _PIECE_COUNT.sub(" ", title)
    title_words = set(normalize_title(title).split())

    matches: list[str] = []
    for candidate in _CANDIDATE.findall(hunting_ground):
        found = index.match_number(candidate)
        if (
            found
            and _reads_as_a_year(candidate)
            and not any(_corroborated(set_num, title_words, index) for set_num in found)
        ):
            continue
        matches.extend(set_num for set_num in found if set_num not in matches)

    if not matches:
        return None
    if len(matches) == 1:
        return Resolution(set_num=matches[0], score=1.0, method="set_number")

    # Several real sets named in one title: a bundle, or a comparison. Recorded
    # so the case is visible in the data, but scored to be rejected.
    return Resolution(set_num=matches[0], score=_AMBIGUOUS_SCORE, method="set_number")


def _resolve_by_name(title: str, index: SetIndex, merchant: str | None) -> Resolution:
    normalized = normalize_title(title, merchant=merchant)
    if len(normalized.split()) < _MIN_FUZZY_TOKENS:
        return UNRESOLVED

    best = index.best_name_match(normalized)
    if best is None:
        return UNRESOLVED

    set_num, score, runner_up = best
    if score - runner_up < _DECISIVE_MARGIN:
        # Two catalogue names this close are two answers, not one, and the same
        # rule already governs strategy 1. Recorded so the case shows up in the
        # data, scored to be rejected.
        return Resolution(set_num=set_num, score=_AMBIGUOUS_SCORE, method="fuzzy_name")
    return Resolution(set_num=set_num, score=score, method="fuzzy_name")


def _reads_as_a_year(candidate: str) -> bool:
    return int(candidate) in _YEAR_LIKE


def _corroborated(set_num: str, title_words: set[str], index: SetIndex) -> bool:
    """Does the rest of the title agree that this is the set being sold?

    SPEC.md asks for exactly this cross-check: a number that could be a year
    only counts when the catalogue name behind it shares a word with the
    title. "LEGO Star Wars 2024" does not corroborate a 1993 racing pickup.
    """
    name_words = set(comparison_key(index.name_of(set_num)).split())
    return bool(name_words & title_words)


def _bare_number(set_num: str) -> str:
    """ "10497-1" -> "10497". Titles quote the number without its variant."""
    match = _VARIANT_SUFFIX.match(set_num)
    return match.group(1) if match else set_num
