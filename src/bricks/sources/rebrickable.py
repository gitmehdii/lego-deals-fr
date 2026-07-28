"""Rebrickable CSV dumps: the identity half of the catalogue.

Not a `Source`: it yields catalogue rows, not offers, so it has no `fetch()
-> list[RawOffer]`. It sits here because it matches what this layer is for —
reaching outside, knowing nothing about the database or about Discord.

Rebrickable publishes complete CSV dumps, which is why we read those instead
of asking an API for 27 000 sets one at a time.
"""

import csv
import gzip
import io

from bricks.log import get_logger, redact_secrets
from bricks.sources.http import HttpFetcher, SourceUnavailableError
from bricks.sources.models import CatalogSet

_GZIP_MAGIC = b"\x1f\x8b"

_log = get_logger(__name__)


def _gunzip(payload: bytes) -> bytes:
    """Decompress unless the transport already did it for us.

    The CDN serves the dump as octet-stream today, so httpx hands it over
    untouched. A CDN that starts sending Content-Encoding: gzip would have
    httpx decompress it first, and blindly gunzipping again would fail on
    perfectly good data.
    """
    return gzip.decompress(payload) if payload[:2] == _GZIP_MAGIC else payload


class RebrickableCatalogue:
    """Downloads and parses `sets.csv.gz` and `themes.csv.gz`."""

    def __init__(self, fetcher: HttpFetcher, *, sets_url: str, themes_url: str) -> None:
        self._fetcher = fetcher
        self._sets_url = sets_url
        self._themes_url = themes_url

    def fetch(self) -> list[CatalogSet]:
        root_themes = _root_theme_names(self._read_csv(self._themes_url))
        rows = self._read_csv(self._sets_url)
        sets = [_to_catalog_set(row, root_themes) for row in rows]
        _log.info("rebrickable_fetched", sets=len(sets), themes=len(root_themes))
        return sets

    def _read_csv(self, url: str) -> list[dict[str, str]]:
        """Both dumps are small enough to hold in memory: 2.5 MB uncompressed."""
        response = self._fetcher.get(url)
        try:
            text = _gunzip(response.content).decode("utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise SourceUnavailableError(
                f"{redact_secrets(url)} is not a readable CSV dump: "
                f"{redact_secrets(exc)}"
            ) from exc
        return list(csv.DictReader(io.StringIO(text)))


def _root_theme_names(rows: list[dict[str, str]]) -> dict[str, str]:
    """Map every theme id to the name of its top-level ancestor.

    Rebrickable themes are a tree: "Ultimate Collector Series" hangs off
    "Star Wars". We store the root because that is the name a human uses and
    the one that makes `idx_sets_theme` worth grouping on.
    """
    names = {row["id"]: row["name"] for row in rows}
    parents = {row["id"]: row["parent_id"] for row in rows}

    roots: dict[str, str] = {}
    for theme_id in names:
        current = theme_id
        seen = {current}
        while (parent := parents.get(current)) and parent in names:
            if parent in seen:
                # A cycle should not exist upstream, but an import that hangs
                # on someone else's bad data is not an acceptable failure mode.
                _log.warning("theme_cycle_detected", theme_id=theme_id)
                break
            current = parent
            seen.add(current)
        roots[theme_id] = names[current]
    return roots


def _to_catalog_set(row: dict[str, str], root_themes: dict[str, str]) -> CatalogSet:
    return CatalogSet(
        set_num=row["set_num"],
        name=row["name"],
        theme=root_themes.get(row["theme_id"]),
        year=_positive_int(row["year"]),
        pieces=_positive_int(row["num_parts"]),
        image_url=row["img_url"] or None,
    )


def _positive_int(value: str) -> int | None:
    """Blank and zero both mean "unknown", and neither should be stored as 0.

    An unknown year is not year zero, and a set recorded with no parts would
    otherwise print "0 pièces" in a Discord embed.
    """
    try:
        number = int(value)
    except ValueError:
        return None
    return number if number > 0 else None
