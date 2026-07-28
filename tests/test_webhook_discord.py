from datetime import UTC, datetime

import httpx
import pytest

from bricks.adapters.webhook.discord import (
    DiscordWebhook,
    build_embed,
    embed_colour,
    format_date,
    format_euros,
    render_console,
)
from bricks.services.alerts import AlertPayload
from bricks.sources.http import HttpFetcher, SourceUnavailableError

WEBHOOK = "https://discord.com/api/webhooks/123456789/s3cret-token"

PAYLOAD = AlertPayload(
    offer_id=1,
    set_num="10497-1",
    set_name="Galaxy Explorer",
    url="https://dealabs.test/deal",
    price_eur=69.99,
    rrp_eur=99.99,
    discount_pct=30.0,
    reason="discount_threshold",
    merchant="Amazon",
    pieces=1254,
    theme="Icons",
    year=2022,
    image_url="https://img.test/10497.jpg",
)


# --- French formatting -----------------------------------------------------


@pytest.mark.parametrize(
    ("amount", "expected_digits"),
    [(69.99, "69,99"), (8.0, "8,00"), (1299.0, "1"), (629.99, "629,99")],
)
def test_euros_use_a_comma_decimal(amount, expected_digits):
    assert expected_digits in format_euros(amount)


def test_thousands_are_grouped_and_the_euro_sign_trails():
    formatted = format_euros(1299.0)
    assert formatted.endswith("€")
    assert "," in formatted, "comma decimal separator, French style"
    assert "1" in formatted and "299,00" in formatted


def test_dates_are_shown_in_paris_never_utc():
    """SPEC.md section 6. 23:30 UTC in July is the next day in Paris."""
    assert format_date(datetime(2026, 3, 12, 9, 0, tzinfo=UTC)) == "12 mars 2026"
    assert format_date(datetime(2026, 7, 27, 23, 30, tzinfo=UTC)) == "28 juillet 2026"


# --- colour ----------------------------------------------------------------


def test_colour_follows_the_discount():
    """Green beyond 40 %, orange between 25 and 40, grey below."""
    deep, modest, shallow = embed_colour(55.0), embed_colour(30.0), embed_colour(10.0)
    assert deep != modest != shallow
    assert embed_colour(None) == shallow, "no discount reads as grey"


def test_the_boundaries_land_on_the_documented_side():
    assert embed_colour(25.0) == embed_colour(39.9), "25 to 40 is one band"
    assert embed_colour(40.1) == embed_colour(80.0), "beyond 40 is one band"


# --- embed -----------------------------------------------------------------


def test_the_embed_carries_the_deal_link_and_the_set_image():
    embed = build_embed(PAYLOAD)
    assert embed["url"] == "https://dealabs.test/deal"
    assert embed["thumbnail"]["url"] == "https://img.test/10497.jpg"
    assert "Galaxy Explorer" in embed["title"]


def test_a_set_without_an_image_gets_no_thumbnail():
    assert "thumbnail" not in build_embed(
        PAYLOAD.model_copy(update={"image_url": None})
    )


def test_the_description_shows_price_reference_and_discount():
    description = build_embed(PAYLOAD)["description"]
    assert "69,99" in description
    assert "99,99" in description
    assert "-30 %" in description


def test_the_all_time_low_line_appears_only_for_that_reason():
    quiet = build_embed(PAYLOAD)["description"]
    assert "Plus bas prix" not in quiet

    low = build_embed(
        PAYLOAD.model_copy(
            update={
                "reason": "all_time_low",
                "previous_low_eur": 74.90,
                "previous_low_at": datetime(2026, 3, 12, 9, 0, tzinfo=UTC),
            }
        )
    )["description"]
    assert "Plus bas prix jamais observé" in low
    assert "74,90" in low
    assert "12 mars 2026" in low


def test_the_merchant_and_set_details_are_shown():
    values = " ".join(field["value"] for field in build_embed(PAYLOAD)["fields"])
    assert "Amazon" in values
    assert "pièces" in values
    assert "Icons" in values
    assert "2022" in values


def test_a_set_with_nothing_to_say_shows_no_empty_field():
    bare = PAYLOAD.model_copy(
        update={"merchant": None, "pieces": None, "theme": None, "year": None}
    )
    assert build_embed(bare)["fields"] == []


def test_the_message_is_in_french():
    """The only audience in the project that is not a developer."""
    embed = build_embed(
        PAYLOAD.model_copy(update={"reason": "all_time_low", "previous_low_eur": 74.9})
    )
    text = embed["description"] + " ".join(f["name"] for f in embed["fields"])
    assert "au lieu de" in text
    assert "Marchand" in text


# --- dry-run rendering -----------------------------------------------------


def test_the_console_rendering_keeps_the_facts_and_drops_the_markup():
    rendered = render_console(PAYLOAD)
    assert "Galaxy Explorer" in rendered
    assert "10497-1" in rendered
    assert "69,99" in rendered
    assert "https://dealabs.test/deal" in rendered
    assert "**" not in rendered and "~~" not in rendered


# --- sending ---------------------------------------------------------------


def _webhook(handler, calls=None):
    def wrapped(request):
        if calls is not None:
            calls.append(request)
        return handler(request)

    fetcher = HttpFetcher(
        httpx.Client(transport=httpx.MockTransport(wrapped)), sleep=lambda _: None
    )
    return DiscordWebhook(fetcher, webhook_url=WEBHOOK)


def test_the_embed_is_posted_as_json():
    calls = []
    _webhook(lambda request: httpx.Response(204), calls).send(PAYLOAD)

    (call,) = calls
    assert call.method == "POST"
    assert b'"embeds"' in call.content
    assert b"Galaxy Explorer" in call.content


def test_a_refused_send_never_leaks_the_webhook_url():
    """The token lives in the path, so the URL is itself the credential."""
    webhook = _webhook(lambda request: httpx.Response(401))

    with pytest.raises(SourceUnavailableError) as excinfo:
        webhook.send(PAYLOAD)

    assert "s3cret-token" not in str(excinfo.value)
    assert "123456789" not in str(excinfo.value)


def test_a_refused_send_raises_rather_than_pretending_it_worked():
    """services/ writes the alerts row only after a successful send."""
    webhook = _webhook(lambda request: httpx.Response(500))
    with pytest.raises(SourceUnavailableError):
        webhook.send(PAYLOAD)
