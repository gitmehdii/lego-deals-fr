import pytest

from bricks.core.price import parse_price_eur


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # Every shape observed on a real Dealabs feed.
        ("158,90€", 158.90),
        ("130€", 130.0),
        ("8,99€", 8.99),
        ("212,99€", 212.99),
        # Shapes SPEC.md section 2 requires the fallback to handle.
        ("79.99 €", 79.99),
        ("79€99", 79.99),
        ("79,99 EUR", 79.99),
        ("1 299,00€", 1299.0),
    ],
)
def test_parses_the_shapes_a_source_actually_publishes(raw, expected):
    assert parse_price_eur(raw) == pytest.approx(expected)


@pytest.mark.parametrize(
    "separator",
    [" ", chr(0x00A0), chr(0x202F)],
    ids=["space", "no-break-space", "narrow-no-break-space"],
)
def test_every_thousands_separator_a_french_price_uses(separator):
    """Spelled with chr(): two of the three are invisible, and a test that
    silently uses a plain space instead would prove nothing at all."""
    assert parse_price_eur(f"1{separator}299,00€") == pytest.approx(1299.0)


def test_reads_a_price_out_of_a_full_title():
    title = "LEGO Icons 10497 Galaxy Explorer à 79,99€ (au lieu de 99,99€) - Amazon"
    assert parse_price_eur(title) == pytest.approx(79.99)


def test_takes_the_first_amount_which_is_the_one_being_offered():
    """SPEC.md: the struck-through price is marketing, never the reference."""
    assert parse_price_eur("69,99€ au lieu de 99,99€") == pytest.approx(69.99)


@pytest.mark.parametrize(
    "raw",
    [None, "", "Gratuit", "LEGO Star Wars 75192", "-50%", "prix en magasin"],
)
def test_returns_none_rather_than_guessing(raw):
    assert parse_price_eur(raw) is None


def test_a_set_number_is_not_a_price():
    """No euro sign, no price. 75192 must never become 75192 EUR."""
    assert parse_price_eur("Millennium Falcon 75192 UCS 7541 pièces") is None


@pytest.mark.parametrize("raw", ["0,10€", "0€", "999999€"])
def test_implausible_amounts_are_rejected(raw):
    """A match outside the plausible range means the pattern latched on wrong."""
    assert parse_price_eur(raw) is None


def test_euro_as_separator_wins_over_the_bare_integer():
    """79€99 is 79.99, not 79."""
    assert parse_price_eur("le set à 79€99 chez Amazon") == pytest.approx(79.99)
