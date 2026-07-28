"""What the outside world hands us, before the database sees any of it.

These carry no primary keys, no timestamps and no persistence concerns. A
source fills them in; `services/` decides what to do with them.
"""

from pydantic import BaseModel, ConfigDict, computed_field

from bricks.core.normalize import normalize_name


class CatalogSet(BaseModel):
    """A set's identity as a catalogue provider describes it. Never a price.

    `rrp_eur` is deliberately absent: it comes from a different provider and
    lives on a different refresh cycle. Merging the two here would let an
    identity refresh quietly wipe a price.
    """

    model_config = ConfigDict(frozen=True)

    set_num: str
    name: str
    theme: str | None = None
    year: int | None = None
    pieces: int | None = None
    image_url: str | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def name_normalized(self) -> str:
        """Derived, never passed in, so it cannot drift away from `name`."""
        return normalize_name(self.name)


class RetailPrice(BaseModel):
    """A set's recommended retail price, in euros.

    `rrp_eur` is None whenever the provider has no euro price for this set.
    Such a set can never trigger a discount-threshold alert; it can still
    trigger an all-time-low one.
    """

    model_config = ConfigDict(frozen=True)

    set_num: str
    rrp_eur: float | None = None
