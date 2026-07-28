"""Brickset API v3: the recommended retail price half of the catalogue.

Not a `Source`: it yields prices, not offers. See `rebrickable.py` for why
both live in this layer anyway.

Brickset is the only provider here that gives us a retail price in euros,
which is what makes an honest discount computable at all. Without it we would
be reducing a merchant's struck-through price, which is marketing.
"""

import json
import time
from collections.abc import Callable

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from bricks.log import get_logger, redact_secrets
from bricks.sources.http import HttpFetcher, SourceUnavailableError
from bricks.sources.models import RetailPrice

# Brickset caps a page at 500. Fetching a year at a time keeps a full sync
# around 110 requests for the whole catalogue.
PAGE_SIZE = 500
PAUSE_SECONDS = 1.0

# Only reached if `matches` disagrees with what the pages actually return.
# A wrong count must not turn into an unbounded loop against someone's API.
MAX_PAGES_PER_YEAR = 20

_log = get_logger(__name__)


class _RegionPrice(BaseModel):
    model_config = ConfigDict(extra="ignore")

    retail_price: float | None = Field(default=None, alias="retailPrice")


class _LegoCom(BaseModel):
    model_config = ConfigDict(extra="ignore")

    # Brickset also publishes US, UK and CA prices. DE is the only one in
    # euros, and Germany and France share LEGO's eurozone recommended price.
    de: _RegionPrice | None = Field(default=None, alias="DE")


class _BricksetSet(BaseModel):
    # Brickset returns some thirty fields per set and we read three of them.
    # Ignoring the rest means a new field upstream cannot break a sync.
    model_config = ConfigDict(extra="ignore", coerce_numbers_to_str=True)

    number: str
    number_variant: int = Field(alias="numberVariant")
    lego_com: _LegoCom | None = Field(default=None, alias="LEGOCom")

    def to_retail_price(self) -> RetailPrice:
        return RetailPrice(
            set_num=f"{self.number}-{self.number_variant}", rrp_eur=self._euro_price()
        )

    def _euro_price(self) -> float | None:
        """Zero counts as unknown, exactly like a missing price.

        A set stored with rrp_eur = 0 would divide by zero in the discount
        calculation, and a "100 % off" alert is worse than no alert.
        """
        if self.lego_com is None or self.lego_com.de is None:
            return None
        price = self.lego_com.de.retail_price
        return price if price is not None and price > 0 else None


class _GetSetsResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    status: str
    matches: int = 0
    message: str | None = None
    sets: list[_BricksetSet] = Field(default_factory=list)


class BricksetCatalogue:
    """Reads recommended retail prices, one catalogue year per call."""

    def __init__(
        self,
        fetcher: HttpFetcher,
        *,
        api_url: str,
        api_key: SecretStr,
        page_size: int = PAGE_SIZE,
        pause_seconds: float = PAUSE_SECONDS,
        max_pages_per_year: int = MAX_PAGES_PER_YEAR,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._fetcher = fetcher
        self._url = f"{api_url.rstrip('/')}/getSets"
        # Kept wrapped so no repr, log or traceback of this object can spill it.
        self._api_key = api_key
        self._page_size = page_size
        self._pause_seconds = pause_seconds
        self._max_pages_per_year = max_pages_per_year
        self._sleep = sleep
        self._has_called = False

    def fetch_retail_prices(self, year: int) -> list[RetailPrice]:
        prices: list[RetailPrice] = []
        for page in range(1, self._max_pages_per_year + 1):
            payload = self._get_sets(year, page)
            prices.extend(item.to_retail_price() for item in payload.sets)
            if not payload.sets or len(prices) >= payload.matches:
                break
        else:
            _log.warning(
                "brickset_page_cap_reached", year=year, pages=self._max_pages_per_year
            )
        _log.info("brickset_year_fetched", year=year, prices=len(prices))
        return prices

    def _get_sets(self, year: int, page: int) -> _GetSetsResponse:
        if self._has_called:
            # One call at a time, never a tight loop.
            self._sleep(self._pause_seconds)
        self._has_called = True

        response = self._fetcher.post(
            self._url,
            data={
                # POST rather than GET so the key never enters a URL, and so
                # never enters an httpx exception message either.
                "apiKey": self._api_key.get_secret_value(),
                # Empty is what public set data expects; we read no user data.
                "userHash": "",
                "params": json.dumps(
                    {
                        "year": str(year),
                        "pageSize": self._page_size,
                        "pageNumber": page,
                    }
                ),
            },
        )
        try:
            # json() raises ValueError too, so it belongs inside the guard.
            payload = _GetSetsResponse.model_validate(response.json())
        except ValueError as exc:
            raise SourceUnavailableError(
                f"brickset getSets returned an unreadable body: {redact_secrets(exc)}"
            ) from exc

        if payload.status != "success":
            detail = redact_secrets(payload.message or payload.status)
            raise SourceUnavailableError(f"brickset getSets failed: {detail}")
        return payload
