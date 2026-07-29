"""The safety net of the project.

Two rules govern the numbers asserted here, both from TICKETS.md lot 4:
90 % of resolutions correct, and **zero false positives above the threshold**.
A false positive counts far heavier than a miss: better to skip a deal than to
announce the wrong set.
"""

from pathlib import Path

import pytest
import yaml

from bricks.core.resolve import CatalogueEntry, SetIndex, resolve

FIXTURE = Path(__file__).parent / "fixtures" / "titles.yaml"
THRESHOLD = 0.85

# The ticket's number exactly. A floor, not a target: the fixture is dominated
# by titles carrying an explicit set number, and the index below is built from
# the fixture's own expectations, so strategy 1 has an easy time here by
# construction. What this file really guards is which number gets picked out of
# a title, and the decoys are what make that non-trivial.
MIN_ACCURACY = 0.90


@pytest.fixture(scope="module")
def cases() -> dict[str, list[dict]]:
    return yaml.safe_load(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def index(cases) -> SetIndex:
    """A catalogue holding every expected set, plus the decoys that matter.

    Built from the fixture rather than from the real database so the suite
    stays hermetic: a test that needs 27 810 rows loaded is a test nobody runs.
    """
    expected = {
        case["set_num"] for group in cases.values() for case in group if case["set_num"]
    }
    entries = [CatalogueEntry(set_num=num, name_normalized="") for num in expected]
    entries += [
        # Decoys the traps depend on: a year, a round piece count and a price
        # that all happen to exist as real LEGO set numbers.
        CatalogueEntry(set_num="2024-1", name_normalized="rescue helicopter"),
        CatalogueEntry(set_num="1000-1", name_normalized="basic building set"),
        CatalogueEntry(set_num="7541-1", name_normalized="unknown decoy"),
        CatalogueEntry(set_num="10323-1", name_normalized="pac man arcade"),
        CatalogueEntry(set_num="7999-1", name_normalized="price decoy"),
    ]
    return SetIndex(entries)


def _outcome(case: dict, index: SetIndex) -> tuple[str, str]:
    """Returns (verdict, detail) for one case."""
    result = resolve(case["title"], index, merchant=case["merchant"])
    got = result.set_num if result.accepted(THRESHOLD) else None
    expected = case["set_num"]

    if got == expected:
        return "correct", ""
    if expected is None:
        return "false_positive", f"invented {got}"
    if got is None:
        return "missed", f"expected {expected}"
    return "false_positive", f"said {got}, expected {expected}"


def test_the_fixture_holds_enough_real_titles(cases):
    """TICKETS.md asks for at least 50 titles copied from Dealabs."""
    real = len(cases["real"])
    if real < 50:
        pytest.xfail(
            f"{real}/50 real titles so far. The feed publishes 30 at a time and "
            "rotates by roughly 9 a day, so this fills up as ingestion runs. "
            "Synthetic titles would defeat the point of the fixture."
        )


@pytest.mark.parametrize("group", ["real", "spec_traps"])
def test_no_false_positive_anywhere(cases, index, group):
    """The rule that outranks every other number in this file."""
    failures = [
        (case["title"], detail)
        for case in cases[group]
        if (outcome := _outcome(case, index))[0] == "false_positive"
        for detail in [outcome[1]]
    ]
    assert not failures, "resolver announced the wrong set:\n" + "\n".join(
        f"  {detail}: {title}" for title, detail in failures
    )


def test_accuracy_on_real_titles(cases, index):
    verdicts = [_outcome(case, index)[0] for case in cases["real"]]
    correct = verdicts.count("correct")
    accuracy = correct / len(verdicts)
    assert accuracy >= MIN_ACCURACY, (
        f"{correct}/{len(verdicts)} correct ({accuracy:.0%}), "
        f"floor is {MIN_ACCURACY:.0%}"
    )


def test_every_spec_trap_is_handled(cases, index):
    """SPEC.md section 3 names these explicitly; each one earns its test."""
    wrong = [
        (case["title"], _outcome(case, index)[1])
        for case in cases["spec_traps"]
        if _outcome(case, index)[0] != "correct"
    ]
    assert not wrong, "spec traps not handled:\n" + "\n".join(
        f"  {detail}: {title}" for title, detail in wrong
    )


# --- the two strategies, in isolation --------------------------------------


@pytest.fixture
def small_index() -> SetIndex:
    return SetIndex(
        [
            CatalogueEntry("10497-1", "galaxy explorer"),
            CatalogueEntry("75192-1", "millennium falcon"),
            CatalogueEntry("31200-2", "star wars sith"),
            CatalogueEntry("2024-1", "rescue helicopter"),
        ]
    )


def test_a_lone_set_number_scores_a_perfect_one(small_index):
    result = resolve("LEGO Icons 10497 Galaxy Explorer", small_index)
    assert (result.set_num, result.score, result.method) == (
        "10497-1",
        1.0,
        "set_number",
    )


def test_a_number_with_its_variant_reaches_the_same_set(small_index):
    """SPEC.md: 10497-1 and 10497 must lead to the same set."""
    assert resolve("LEGO 10497-1", small_index).set_num == "10497-1"


def test_a_variant_other_than_one_is_found_too(small_index):
    assert resolve("LEGO 31200 promo", small_index).set_num == "31200-2"


def test_a_number_absent_from_the_catalogue_does_not_resolve(small_index):
    """A number we do not know is not a set, whatever it looks like."""
    result = resolve("LEGO Super Mario 99999", small_index)
    assert result.set_num is None or not result.accepted(THRESHOLD)


def test_a_piece_count_is_never_read_as_a_set_number(small_index):
    """ "7541 pièces" announces itself, and 75192 is the real answer."""
    result = resolve("LEGO 75192 Millennium Falcon 7541 pièces", small_index)
    assert result.set_num == "75192-1"
    assert result.score == 1.0


def test_two_real_sets_in_one_title_are_scored_to_be_rejected(small_index):
    result = resolve("LEGO 10497 et 75192 en promo", small_index)
    assert not result.accepted(THRESHOLD)
    assert result.method == "set_number", "the case should still be visible in the data"


def test_a_year_does_not_match_a_set_that_happens_to_share_its_number(small_index):
    """SPEC.md's first named trap."""
    result = resolve("LEGO Star Wars 2024 nouveautés", small_index)
    assert not result.accepted(THRESHOLD)


def test_a_year_shaped_number_still_resolves_when_the_title_corroborates(small_index):
    """The rule rejects years, not the 42 real sets numbered like one."""
    result = resolve("LEGO 2024 Rescue Helicopter", small_index)
    assert result.set_num == "2024-1"
    assert result.score == 1.0


def test_the_score_and_method_are_recorded_even_when_rejected(small_index):
    """SPEC.md keeps them so resolution quality can be measured later."""
    result = resolve("LEGO 10497 et 75192 en promo", small_index)
    assert result.method is not None
    assert 0.0 < result.score < THRESHOLD


def test_an_empty_catalogue_resolves_nothing_without_crashing():
    result = resolve("LEGO Icons 10497 Galaxy Explorer", SetIndex([]))
    assert result.set_num is None
    assert result.method is None


def test_fuzzy_matching_is_the_fallback_not_the_first_choice(small_index):
    """A title with a number never reaches strategy two."""
    assert resolve("LEGO 10497 galaxy explorer", small_index).method == "set_number"


def test_a_title_too_short_to_judge_is_left_alone(small_index):
    """Short strings are exactly where fuzzy matching invents things."""
    assert resolve("LEGO", small_index).method is None
