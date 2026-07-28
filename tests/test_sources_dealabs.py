from datetime import UTC, datetime

import httpx
import pytest

from bricks.sources.dealabs import DealabsSource
from bricks.sources.http import HttpFetcher, SourceUnavailableError

RSS_URL = "https://dealabs.example.test/rss/groupe/lego"

HEADER = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<rss xmlns:pepper="http://www.pepper.com/rss" version="2.0"><channel>'
    "<title>Bons plans Lego</title>"
)
FOOTER = "</channel></rss>"


def item(
    *,
    title="Lego Technic 42231 - Dodge Charger",
    link="https://www.dealabs.com/bons-plans/lego-technic-42231-3383357",
    guid=None,
    merchant='<pepper:merchant name="Alternate" price="115,90€"/>',
    date="Tue, 28 Jul 2026 13:28:31 +0200",
):
    guid = link if guid is None else guid
    parts = ["<item>", merchant, f"<title><![CDATA[{title}]]></title>"]
    if link:
        parts.append(f"<link>{link}</link>")
    if guid:
        parts.append(f"<guid>{guid}</guid>")
    if date:
        parts.append(f"<pubDate>{date}</pubDate>")
    parts.append("</item>")
    return "".join(parts)


def _source(body: str, *, status=200) -> DealabsSource:
    def handler(request):
        return httpx.Response(status, content=body.encode("utf-8"))

    fetcher = HttpFetcher(
        httpx.Client(transport=httpx.MockTransport(handler)), sleep=lambda _: None
    )
    return DealabsSource(fetcher, rss_url=RSS_URL)


def _feed(*items: str) -> str:
    return HEADER + "".join(items) + FOOTER


def test_reads_an_entry_the_way_dealabs_publishes_it():
    (offer,) = _source(_feed(item())).fetch()

    assert offer.external_id == "3383357"
    assert offer.title == "Lego Technic 42231 - Dodge Charger"
    assert offer.url == "https://www.dealabs.com/bons-plans/lego-technic-42231-3383357"
    assert offer.price_eur == pytest.approx(115.90)
    assert offer.merchant == "Alternate"
    assert offer.published_at == datetime(2026, 7, 28, 11, 28, 31, tzinfo=UTC)


def test_the_title_is_kept_untouched():
    """When resolution misbehaves later, this string is the evidence."""
    raw = "[Précommande] LEGO Star Wars 75453 - Offworld Sandcrawler et Mudhorn"
    (offer,) = _source(_feed(item(title=raw))).fetch()
    assert offer.title == raw


def test_published_at_is_utc_not_paris():
    """13:28 Paris in July is 11:28 UTC. Timestamps are stored in UTC."""
    (offer,) = _source(_feed(item())).fetch()
    assert offer.published_at.tzinfo is not None
    assert offer.published_at.hour == 11


def test_price_comes_from_the_structured_attribute_not_the_title():
    """The title's price is the struck-through one often enough to distrust it."""
    entry = item(
        title="LEGO Icons 10497 Galaxy Explorer au lieu de 99,99€",
        merchant='<pepper:merchant name="Amazon" price="69,99€"/>',
    )
    (offer,) = _source(_feed(entry)).fetch()
    assert offer.price_eur == pytest.approx(69.99)


def test_falls_back_to_the_title_when_the_attribute_has_no_price():
    entry = item(
        title="LEGO City 60511 à 65,90€", merchant='<pepper:merchant name="Amazon"/>'
    )
    (offer,) = _source(_feed(entry)).fetch()
    assert offer.price_eur == pytest.approx(65.90)
    assert offer.merchant == "Amazon"


def test_falls_back_to_the_title_when_there_is_no_attribute_at_all():
    entry = item(title="LEGO City 60511 à 65,90€", merchant="")
    (offer,) = _source(_feed(entry)).fetch()
    assert offer.price_eur == pytest.approx(65.90)
    assert offer.merchant is None


def test_an_offer_with_no_price_anywhere_is_still_returned():
    """SPEC.md: kept in the database, merely invisible to detection."""
    entry = item(title="LEGO Star Wars 75447 en promo", merchant="")
    (offer,) = _source(_feed(entry)).fetch()
    assert offer.price_eur is None
    assert offer.title == "LEGO Star Wars 75447 en promo"


def test_a_round_price_without_decimals_is_read():
    entry = item(merchant='<pepper:merchant name="Amazon" price="130€"/>')
    (offer,) = _source(_feed(entry)).fetch()
    assert offer.price_eur == pytest.approx(130.0)


def test_external_id_is_the_thread_id_so_it_survives_a_slug_change():
    first = item(link="https://www.dealabs.com/bons-plans/ancien-titre-3383357")
    (offer,) = _source(_feed(first)).fetch()
    assert offer.external_id == "3383357"

    renamed = item(link="https://www.dealabs.com/bons-plans/titre-corrige-3383357")
    (same,) = _source(_feed(renamed)).fetch()
    assert same.external_id == offer.external_id


def test_a_guid_without_a_trailing_id_is_used_whole():
    entry = item(link="https://www.dealabs.com/bons-plans/sans-identifiant")
    (offer,) = _source(_feed(entry)).fetch()
    assert offer.external_id == "https://www.dealabs.com/bons-plans/sans-identifiant"


def test_an_entry_with_no_link_is_skipped_not_guessed():
    """Without an id there is nothing to deduplicate on."""
    assert _source(_feed(item(link="", guid=""))).fetch() == []


def test_a_good_entry_survives_a_bad_neighbour():
    offers = _source(_feed(item(link="", guid=""), item())).fetch()
    assert [o.external_id for o in offers] == ["3383357"]


def test_an_empty_feed_is_not_an_error():
    """Lot 6 alerts on three consecutive empty runs; one is not a failure."""
    assert _source(_feed()).fetch() == []


def test_an_unreadable_body_stops_the_run():
    with pytest.raises(SourceUnavailableError, match="unreadable"):
        _source("this is not XML at all, not even close").fetch()


def test_the_source_announces_its_name():
    """services/ writes this into runs.source and offers.source."""
    assert _source(_feed()).name == "dealabs"
