"""Which room a deal is announced in. Pure mapping, no I/O."""

import pytest

from bricks.core.channels import CATCH_ALL, CHANNELS, channel_for


@pytest.mark.parametrize(
    ("theme", "expected"),
    [
        ("Star Wars", "star_wars"),
        ("Botanicals", "collection"),
        ("Icons", "collection"),
        ("Technic", "vehicules"),
        ("Speed Champions", "vehicules"),
        ("Harry Potter", "univers"),
        ("Super Heroes Marvel", "univers"),
        ("Minecraft", "univers"),
    ],
)
def test_a_mapped_theme_reaches_its_room(theme, expected):
    assert channel_for(theme) == expected


@pytest.mark.parametrize(
    "theme",
    [
        # Real catalogue themes, deliberately unmapped: they are catalogue bulk
        # or too rare to deserve a room of their own.
        "Gear",
        "Books",
        "Educational and Dacta",
        "Creator",
        "City",
        "Duplo",
        "Seasonal",
        # And the one LEGO has not invented yet.
        "Theme Invented In 2031",
        None,
    ],
)
def test_anything_unmapped_falls_to_the_catch_all(theme):
    """Quiet, never lost. A new theme must not silence a deal."""
    assert channel_for(theme) == CATCH_ALL


def test_the_catch_all_is_a_configurable_channel_like_any_other():
    """adapters/ looks a webhook up per name, so it has to be in the list."""
    assert CATCH_ALL in CHANNELS


def test_every_room_a_theme_maps_to_is_declared():
    """CHANNELS is what config and the router iterate over. A room reachable
    by a theme but absent from it would silently have no webhook."""
    reachable = {channel_for(theme) for theme in _EVERY_MAPPED_THEME}
    assert reachable <= set(CHANNELS)


_EVERY_MAPPED_THEME = [
    "Star Wars",
    "Icons",
    "Botanicals",
    "Architecture",
    "LEGO Ideas and CUUSOO",
    "Brickheadz",
    "Technic",
    "Speed Champions",
    "Racers",
    "Train",
    "Harry Potter",
    "Super Heroes Marvel",
    "Super Heroes DC",
    "Minecraft",
    "Super Mario",
    "Jurassic World",
    "Disney",
    "Ninjago",
    "Pokémon",
    "Fortnite",
    "Wednesday",
    "Animal Crossing",
    "Legends of Chima",
    "Nexo Knights",
    "Bionicle",
    "Avatar",
    "Indiana Jones",
    "Sonic The Hedgehog",
]
