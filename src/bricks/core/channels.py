"""Which channel a deal belongs in. Pure, no I/O, no Discord.

A channel is a business concept here, not a Discord one: `alerts.channel_id`
has existed in the schema since the first migration. `adapters/` is what turns
a name below into a webhook URL.

Deliberately few and broad. The catalogue carries 150 themes and real deals
land on about 24 of them, nine of which produced a single offer — one channel
per theme would be two dozen empty rooms. Anything unmapped falls to
CATCH_ALL, so a theme LEGO invents next year is quiet, never lost.
"""

CATCH_ALL = "divers"

# Rebrickable's spelling of the theme, exactly as `sets.theme` stores it.
_BY_THEME = {
    "Star Wars": "star_wars",
    # Display sets bought by adults, where the RRP is high enough for a
    # discount to be worth interrupting someone for.
    "Icons": "collection",
    "Botanicals": "collection",
    "Architecture": "collection",
    "LEGO Ideas and CUUSOO": "collection",
    "Brickheadz": "collection",
    "Technic": "vehicules",
    "Speed Champions": "vehicules",
    "Racers": "vehicules",
    "Train": "vehicules",
    # Licensed worlds and LEGO's own character lines together: the reader
    # cares that it is Harry Potter or Ninjago, not who owns the rights.
    "Harry Potter": "univers",
    "Super Heroes Marvel": "univers",
    "Super Heroes DC": "univers",
    "Minecraft": "univers",
    "Super Mario": "univers",
    "Jurassic World": "univers",
    "Disney": "univers",
    "Ninjago": "univers",
    "Pokémon": "univers",
    "Fortnite": "univers",
    "Wednesday": "univers",
    "Animal Crossing": "univers",
    "Legends of Chima": "univers",
    "Nexo Knights": "univers",
    "Bionicle": "univers",
    "Avatar": "univers",
    "Indiana Jones": "univers",
    "Sonic The Hedgehog": "univers",
}

# Every name a webhook can be configured for. CATCH_ALL is always among them.
CHANNELS = sorted({*_BY_THEME.values(), CATCH_ALL})


def channel_for(theme: str | None) -> str:
    """The channel a set of this theme is announced in.

    A set with no theme at all is a real case — the catalogue has some — and
    it lands in the catch-all like anything else unrecognised.
    """
    if theme is None:
        return CATCH_ALL
    return _BY_THEME.get(theme, CATCH_ALL)
