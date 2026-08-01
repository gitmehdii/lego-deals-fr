"""What the outside world hands us, before the database sees any of it.

These carry no primary keys, no timestamps and no persistence concerns. A
source fills them in; `services/` decides what to do with them.
"""

from datetime import datetime

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


class RawOffer(BaseModel):
    """One deal, exactly as a source published it. Deliberately poor.

    No set number and no discount: this model predates any interpretation.
    Resolution happens later, and `title` is kept untouched so that when
    resolution misbehaves there is still evidence of what it was given.
    """

    model_config = ConfigDict(frozen=True)

    # The source's own id for this deal. With the source name, this is what
    # makes deduplication across runs possible.
    external_id: str
    title: str
    url: str

    # None when the source publishes no usable price. The offer is still
    # stored; it is simply invisible to detection.
    price_eur: float | None = None
    merchant: str | None = None
    published_at: datetime | None = None
