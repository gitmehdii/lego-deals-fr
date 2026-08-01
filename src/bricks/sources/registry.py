"""Where a source gets registered, and the only place that knows their names.

Adding a source means writing one file in this package and adding one line
below. The CLI does not change, and neither does `services/`: that is the
whole point of the `Source` protocol.
"""

from collections.abc import Callable

from bricks.config import Settings
from bricks.sources.base import Source
from bricks.sources.dealabs import DealabsSource
from bricks.sources.http import HttpFetcher

_BUILDERS: dict[str, Callable[[HttpFetcher, Settings], Source]] = {
    "dealabs": lambda fetcher, settings: DealabsSource(
        fetcher, rss_url=settings.dealabs_rss_url
    ),
}

# Fed to argparse, so an unknown name is rejected before anything is opened
# and --help lists what is available.
SOURCE_NAMES = tuple(_BUILDERS)


def build_source(name: str, fetcher: HttpFetcher, settings: Settings) -> Source:
    try:
        return _BUILDERS[name](fetcher, settings)
    except KeyError:
        raise ValueError(f"unknown source: {name}") from None
