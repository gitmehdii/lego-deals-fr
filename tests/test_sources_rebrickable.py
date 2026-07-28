import gzip

import httpx
import pytest

from bricks.sources.http import HttpFetcher, SourceUnavailableError
from bricks.sources.rebrickable import RebrickableCatalogue

SETS_URL = "https://cdn.example.test/sets.csv.gz"
THEMES_URL = "https://cdn.example.test/themes.csv.gz"

SETS_HEADER = "set_num,name,year,theme_id,num_parts,img_url"
THEMES_HEADER = "id,name,parent_id"

# The real dump's shape, taken from the columns Rebrickable actually publishes.
THEMES_CSV = "\n".join(
    [
        THEMES_HEADER,
        "158,Star Wars,",
        "171,Ultimate Collector Series,158",
        "209,Episode IV,171",
        "721,Icons,",
    ]
)


def _catalogue(sets_csv: str, themes_csv: str = THEMES_CSV, *, gzipped: bool = True):
    def encode(text: str) -> bytes:
        raw = text.encode("utf-8")
        return gzip.compress(raw) if gzipped else raw

    bodies = {SETS_URL: encode(sets_csv), THEMES_URL: encode(themes_csv)}

    def handler(request):
        return httpx.Response(200, content=bodies[str(request.url)])

    fetcher = HttpFetcher(
        httpx.Client(transport=httpx.MockTransport(handler)), sleep=lambda _: None
    )
    return RebrickableCatalogue(fetcher, sets_url=SETS_URL, themes_url=THEMES_URL)


def test_parses_a_set_row():
    csv = "\n".join(
        [
            SETS_HEADER,
            "10497-1,Galaxy Explorer,2022,721,1254,https://img.test/10497-1.jpg",
        ]
    )
    (result,) = _catalogue(csv).fetch()

    assert result.set_num == "10497-1"
    assert result.name == "Galaxy Explorer"
    assert result.name_normalized == "galaxy explorer"
    assert result.theme == "Icons"
    assert result.year == 2022
    assert result.pieces == 1254
    assert result.image_url == "https://img.test/10497-1.jpg"


def test_theme_is_the_root_of_the_tree_not_the_leaf():
    """209 -> 171 -> 158, and 158 is the name a human uses."""
    csv = "\n".join([SETS_HEADER, "75192-1,Millennium Falcon,2017,209,7541,"])
    (result,) = _catalogue(csv).fetch()
    assert result.theme == "Star Wars"


def test_theme_already_at_the_root_is_kept():
    csv = "\n".join([SETS_HEADER, "10497-1,Galaxy Explorer,2022,721,1254,"])
    assert _catalogue(csv).fetch()[0].theme == "Icons"


def test_unknown_theme_id_yields_no_theme():
    csv = "\n".join([SETS_HEADER, "99999-1,Mystery Set,2020,4242,10,"])
    assert _catalogue(csv).fetch()[0].theme is None


def test_theme_cycle_does_not_hang():
    """Bad upstream data must not turn the import into an infinite loop."""
    themes = "\n".join([THEMES_HEADER, "1,Loop A,2", "2,Loop B,1"])
    csv = "\n".join([SETS_HEADER, "1-1,Set,2020,1,10,"])
    assert _catalogue(csv, themes).fetch()[0].theme in {"Loop A", "Loop B"}


@pytest.mark.parametrize("year", ["", "0"])
def test_missing_year_is_none_not_zero(year):
    csv = "\n".join([SETS_HEADER, f"1-1,Set,{year},721,10,"])
    assert _catalogue(csv).fetch()[0].year is None


@pytest.mark.parametrize("parts", ["", "0"])
def test_missing_piece_count_is_none_not_zero(parts):
    """A set with no parts recorded must not print "0 pièces" in an embed."""
    csv = "\n".join([SETS_HEADER, f"1-1,Set,2020,721,{parts},"])
    assert _catalogue(csv).fetch()[0].pieces is None


def test_blank_image_url_is_none():
    csv = "\n".join([SETS_HEADER, "1-1,Set,2020,721,10,"])
    assert _catalogue(csv).fetch()[0].image_url is None


def test_quoted_comma_in_a_name_is_parsed_as_one_field():
    csv = "\n".join([SETS_HEADER, '1-1,"Ninjago: Fire, Ice and Water",2020,721,10,'])
    assert _catalogue(csv).fetch()[0].name == "Ninjago: Fire, Ice and Water"


def test_reads_an_already_decompressed_body():
    """Guards against a CDN that starts setting Content-Encoding: gzip."""
    csv = "\n".join([SETS_HEADER, "10497-1,Galaxy Explorer,2022,721,1254,"])
    assert _catalogue(csv, gzipped=False).fetch()[0].set_num == "10497-1"


def test_a_corrupt_dump_is_reported_not_crashed_through():
    def handler(request):
        return httpx.Response(200, content=b"\x1f\x8b not really gzip")

    fetcher = HttpFetcher(
        httpx.Client(transport=httpx.MockTransport(handler)), sleep=lambda _: None
    )
    catalogue = RebrickableCatalogue(fetcher, sets_url=SETS_URL, themes_url=THEMES_URL)
    with pytest.raises(SourceUnavailableError, match="not a readable CSV dump"):
        catalogue.fetch()
