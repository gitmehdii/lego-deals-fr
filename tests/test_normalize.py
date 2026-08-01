import pytest

from bricks.core.normalize import comparison_key, normalize_name, normalize_title


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Galaxy Explorer", "galaxy explorer"),
        ("GALAXY EXPLORER", "galaxy explorer"),
        ("Café Corner", "cafe corner"),
        (
            "Real Madrid – Santiago Bernabéu Stadium",
            "real madrid santiago bernabeu stadium",
        ),
        ("R2-D2", "r2 d2"),
        ("Bluey’s Family House", "bluey s family house"),
        ("WALL•E [Original Version]", "wall e original version"),
        ("  Spring Animal Playground ​ ", "spring animal playground"),
        ("﻿Up House", "up house"),
        ("Millennium Falcon™", "millennium falcon"),
        ("", ""),
        ("!!!", ""),
    ],
)
def test_normalize_name(raw, expected):
    assert normalize_name(raw) == expected


def test_digits_are_kept():
    """A set number inside a name is a resolution signal, not noise."""
    assert normalize_name("Millennium Falcon 75192") == "millennium falcon 75192"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Jabłkowy zawrót głowy", "jablkowy zawrot glowy"),
        ("Große Straße", "grosse strasse"),
        ("Æon", "aeon"),
        ("Cœur", "coeur"),
        ("Køge", "koge"),
    ],
)
def test_letters_nfkd_leaves_alone_are_transliterated_not_dropped(raw, expected):
    """Without the transliteration table these split a word into two tokens."""
    assert normalize_name(raw) == expected


def test_is_idempotent():
    """Normalising an already normalised name must be a no-op."""
    once = normalize_name("Real Madrid – Santiago Bernabéu Stadium")
    assert normalize_name(once) == once


# --- normalize_title -------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            "Jouet de construction Lego City 60511 - Le train à vapeur rétro",
            "city 60511 train vapeur retro",
        ),
        ("LEGO Star Wars 75447 - Le Razor Crest", "star wars 75447 razor crest"),
        ("[Précommande] LEGO Super Mario 72051", "super mario 72051"),
    ],
)
def test_normalize_title_keeps_only_what_identifies_a_set(raw, expected):
    assert normalize_title(raw) == expected


def test_the_set_number_always_survives_normalisation():
    """Strategy one depends on it, so no stopword rule may ever eat a number."""
    assert "10497" in normalize_title("Jouet Lego Icons 10497 en promo")


@pytest.mark.parametrize(
    "raw",
    [
        "Lego Icons 40813 (via 6€ sur la carte de fidélité)",
        "Lego Icons 40813 - 35% de réduction",
        "Lego Icons 40813 à 79,99€ au lieu de 99,99€",
    ],
)
def test_money_and_percentages_are_stripped(raw):
    normalized = normalize_title(raw)
    assert normalized == "icons 40813", normalized


def test_the_merchant_name_is_removed_when_the_source_knows_it():
    title = "Lego Creator 31173 Toucan Tropical chez Cdiscount"
    assert "cdiscount" not in normalize_title(title, merchant="Cdiscount")


def test_a_merchant_is_only_removed_when_supplied():
    """No hardcoded list of shop names: such a list rots."""
    title = "Lego Creator 31173 Toucan Tropical chez Cdiscount"
    assert "cdiscount" in normalize_title(title)


def test_comparison_key_strips_the_same_filler_from_a_catalogue_name():
    """Both sides of a fuzzy comparison must lose the same words."""
    assert comparison_key("the razor crest") == "razor crest"
    assert comparison_key(normalize_name("The LEGO Movie Set")) == "movie"
