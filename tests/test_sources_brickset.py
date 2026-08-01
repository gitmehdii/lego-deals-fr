import json
from urllib.parse import parse_qs

import httpx
import pytest
from pydantic import SecretStr

from bricks.sources.brickset import BricksetCatalogue
from bricks.sources.http import HttpFetcher, SourceUnavailableError

API_URL = "https://brickset.example.test/api/v3.asmx"
API_KEY = "s3cret-api-key"


def _form(request) -> dict[str, str]:
    """The form body Brickset received, URL-decoded."""
    parsed = parse_qs(request.content.decode(), keep_blank_values=True)
    return {key: values[0] for key, values in parsed.items()}


def _params(request) -> dict:
    """The JSON blob Brickset takes as its `params` field."""
    return json.loads(_form(request)["params"])


def _set(number="10497", variant=1, *, de_price=99.99, lego_com=True):
    """A getSets entry, trimmed to the fields we read."""
    payload = {
        "setID": 27908,
        "number": number,
        "numberVariant": variant,
        "name": "Galaxy Explorer",
        "year": 2022,
        "theme": "Icons",
        "pieces": 1254,
    }
    if lego_com:
        regions = {"US": {"retailPrice": 99.99}, "UK": {"retailPrice": 84.99}}
        if de_price is not None:
            regions["DE"] = {"retailPrice": de_price}
        payload["LEGOCom"] = regions
    return payload


def _catalogue(pages, *, record=None, **kwargs):
    """`pages` is the list of JSON bodies to return, in order."""
    calls = []

    def handler(request):
        calls.append(request)
        if record is not None:
            record.append(request)
        body = pages[min(len(calls) - 1, len(pages) - 1)]
        return httpx.Response(200, json=body)

    fetcher = HttpFetcher(
        httpx.Client(transport=httpx.MockTransport(handler)), sleep=lambda _: None
    )
    kwargs.setdefault("sleep", lambda _: None)
    catalogue = BricksetCatalogue(
        fetcher, api_url=API_URL, api_key=SecretStr(API_KEY), **kwargs
    )
    return catalogue, calls


def _ok(sets, matches=None):
    return {
        "status": "success",
        "matches": len(sets) if matches is None else matches,
        "sets": sets,
    }


def test_reads_the_german_price_as_the_euro_rrp():
    catalogue, _ = _catalogue([_ok([_set(de_price=99.99)])])
    (price,) = catalogue.fetch_retail_prices(2022)
    assert price.set_num == "10497-1"
    assert price.rrp_eur == 99.99


def test_set_num_matches_the_rebrickable_format():
    catalogue, _ = _catalogue([_ok([_set(number="75192", variant=1)])])
    assert catalogue.fetch_retail_prices(2017)[0].set_num == "75192-1"


def test_a_numeric_set_number_is_still_a_string():
    """Brickset publishes it as a string; a number must not become "10497.0"."""
    entry = _set()
    entry["number"] = 10497
    catalogue, _ = _catalogue([_ok([entry])])
    assert catalogue.fetch_retail_prices(2022)[0].set_num == "10497-1"


def test_no_german_price_leaves_the_rrp_unknown():
    catalogue, _ = _catalogue([_ok([_set(de_price=None)])])
    assert catalogue.fetch_retail_prices(2022)[0].rrp_eur is None


def test_no_lego_com_block_at_all_leaves_the_rrp_unknown():
    catalogue, _ = _catalogue([_ok([_set(lego_com=False)])])
    assert catalogue.fetch_retail_prices(2022)[0].rrp_eur is None


@pytest.mark.parametrize("price", [0, 0.0, -5.0])
def test_a_zero_or_negative_price_is_unknown_not_a_price(price):
    """rrp_eur = 0 would divide by zero when computing a discount."""
    catalogue, _ = _catalogue([_ok([_set(de_price=price)])])
    assert catalogue.fetch_retail_prices(2022)[0].rrp_eur is None


def test_paginates_until_every_match_is_collected():
    first = _ok([_set(number=str(n)) for n in range(100, 102)], matches=4)
    second = _ok([_set(number=str(n)) for n in range(102, 104)], matches=4)
    catalogue, calls = _catalogue([first, second], page_size=2)

    prices = catalogue.fetch_retail_prices(2022)

    assert len(prices) == 4
    assert len(calls) == 2
    assert [_params(call)["pageNumber"] for call in calls] == [1, 2]


def test_stops_on_an_empty_page_even_if_matches_disagrees():
    catalogue, calls = _catalogue([_ok([], matches=999)])
    assert catalogue.fetch_retail_prices(2022) == []
    assert len(calls) == 1


def test_never_loops_past_the_page_cap():
    """A wrong `matches` must not turn into an unbounded loop on someone's API."""
    catalogue, calls = _catalogue([_ok([_set()], matches=10_000)], max_pages_per_year=3)
    catalogue.fetch_retail_prices(2022)
    assert len(calls) == 3


def test_an_api_error_status_stops_the_run():
    catalogue, _ = _catalogue([{"status": "error", "message": "invalid API key"}])
    with pytest.raises(SourceUnavailableError, match="invalid API key"):
        catalogue.fetch_retail_prices(2022)


def test_an_unreadable_body_stops_the_run():
    def handler(request):
        return httpx.Response(200, content=b"<html>down for maintenance</html>")

    fetcher = HttpFetcher(
        httpx.Client(transport=httpx.MockTransport(handler)), sleep=lambda _: None
    )
    catalogue = BricksetCatalogue(
        fetcher, api_url=API_URL, api_key=SecretStr(API_KEY), sleep=lambda _: None
    )
    with pytest.raises(SourceUnavailableError, match="unreadable body"):
        catalogue.fetch_retail_prices(2022)


def test_the_api_key_travels_in_the_body_never_in_the_url():
    catalogue, calls = _catalogue([_ok([_set()])])
    catalogue.fetch_retail_prices(2022)

    (call,) = calls
    assert call.method == "POST"
    assert API_KEY not in str(call.url)
    assert _form(call)["apiKey"] == API_KEY


def test_queries_the_year_it_was_asked_for():
    catalogue, calls = _catalogue([_ok([_set()])])
    catalogue.fetch_retail_prices(1998)
    assert _params(calls[0])["year"] == "1998"


def test_pauses_between_calls_but_not_before_the_first():
    delays = []
    first = _ok([_set(number="100")], matches=2)
    second = _ok([_set(number="101")], matches=2)
    catalogue, _ = _catalogue(
        [first, second], page_size=1, pause_seconds=1.0, sleep=delays.append
    )
    catalogue.fetch_retail_prices(2022)
    assert delays == [1.0]
