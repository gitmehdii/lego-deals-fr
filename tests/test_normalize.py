import pytest

from bricks.core.normalize import normalize_name


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
