"""Dealabs, the only offer source in v1.

We read an RSS feed Dealabs publishes willingly rather than scraping the site.
That constraint is deliberate and documented in CLAUDE.md: a feed is a stable
contract, whereas a scraper breaks on the next CSS change and breaks silently.

One request per run, no pagination, no loop.
"""

import re
from datetime import UTC, datetime
from time import struct_time

import feedparser

from bricks.core.price import parse_price_eur
from bricks.log import get_logger, redact_secrets
from bricks.sources.http import HttpFetcher, SourceUnavailableError
from bricks.sources.models import RawOffer

# Dealabs runs on Pepper, whose feeds carry
# <pepper:merchant name="Alternate" price="158,90€"/>. feedparser surfaces it
# as a plain dict. Structured, so far more trustworthy than reading the title.
_MERCHANT_KEY = "pepper_merchant"

# A thread URL ends in its numeric id: .../precommande-lego-...-3383356
_THREAD_ID = re.compile(r"-(\d+)/?$")

_log = get_logger(__name__)


class DealabsSource:
    """Reads the LEGO feed and returns what it published, uninterpreted."""

    name = "dealabs"

    def __init__(self, fetcher: HttpFetcher, *, rss_url: str) -> None:
        self._fetcher = fetcher
        self._rss_url = rss_url

    def fetch(self) -> list[RawOffer]:
        response = self._fetcher.get(self._rss_url)
        feed = feedparser.parse(response.content)

        if feed.bozo and not feed.entries:
            raise SourceUnavailableError(
                f"dealabs feed is unreadable: {redact_secrets(feed.bozo_exception)}"
            )
        if feed.bozo:
            # Malformed but salvageable. Worth knowing about, not worth losing
            # a run over.
            _log.warning(
                "dealabs_feed_malformed", detail=redact_secrets(feed.bozo_exception)
            )

        offers = [offer for entry in feed.entries if (offer := _to_offer(entry))]
        _log.info("dealabs_fetched", entries=len(feed.entries), offers=len(offers))
        return offers


def _to_offer(entry: object) -> RawOffer | None:
    """None for an entry we could not identify. Skipped rather than guessed."""
    url = _get(entry, "link")
    title = _get(entry, "title")
    external_id = _external_id(_get(entry, "id") or url)

    if not (external_id and title and url):
        _log.warning("dealabs_entry_skipped", external_id=external_id, url=url)
        return None

    merchant = _get(entry, _MERCHANT_KEY) or {}
    return RawOffer(
        external_id=external_id,
        title=title,
        url=url,
        # Structured attribute first, title only as a fallback.
        price_eur=parse_price_eur(merchant.get("price")) or parse_price_eur(title),
        merchant=merchant.get("name") or None,
        published_at=_published_at(_get(entry, "published_parsed")),
    )


def _get(entry: object, key: str) -> object:
    """feedparser entries are dict-like, and every field is optional."""
    return entry.get(key) if hasattr(entry, "get") else None


def _external_id(guid: object) -> str | None:
    """The thread id, or the whole guid when there is no id to pull out.

    Either way it stays stable across runs, which is all deduplication needs.
    """
    if not isinstance(guid, str) or not guid:
        return None
    match = _THREAD_ID.search(guid)
    return match.group(1) if match else guid


def _published_at(parsed: object) -> datetime | None:
    """feedparser normalises every date it understands to UTC."""
    if not isinstance(parsed, struct_time):
        return None
    return datetime(*parsed[:6], tzinfo=UTC)
