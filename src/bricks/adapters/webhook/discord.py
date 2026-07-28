"""Turning an AlertPayload into something a human reads, in Discord.

The only module in the project that speaks French in its output, because it is
the only one whose audience is the end user rather than a developer. Every
other string — logs, exceptions, CLI — stays English.

`services/` knows nothing about any of this and must keep it that way.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

from bricks.log import get_logger, redact_secrets
from bricks.services.alerts import AlertPayload
from bricks.sources.http import HttpFetcher, SourceUnavailableError

# Timestamps are stored in UTC and converted exactly once, here, at the moment
# of display. Nowhere else in the codebase is allowed to know about Paris.
PARIS = ZoneInfo("Europe/Paris")

# Embed colour follows the discount, per SPEC.md section 6.
_GREEN = 0x2ECC71
_ORANGE = 0xE67E22
_GREY = 0x95A5A6
_DEEP_DISCOUNT_PCT = 40.0
_MODEST_DISCOUNT_PCT = 25.0

# French formatting uses a narrow no-break space to group thousands. Spelled
# with chr() because it is invisible in a source file.
_THIN_SPACE = chr(0x202F)

_MONTHS = (
    "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre",
)  # fmt: skip

_log = get_logger(__name__)


def format_euros(amount: float) -> str:
    """1299.0 -> "1 299,00 €". Comma decimal, thin space thousands."""
    formatted = f"{amount:,.2f}".replace(",", "\x00").replace(".", ",")
    return formatted.replace("\x00", _THIN_SPACE) + f"{_THIN_SPACE}€"


def format_date(moment: datetime) -> str:
    """ "12 mars 2026", in Paris time. Never UTC in front of a reader."""
    local = moment.astimezone(PARIS)
    return f"{local.day} {_MONTHS[local.month - 1]} {local.year}"


def embed_colour(discount_pct: float | None) -> int:
    if discount_pct is None:
        return _GREY
    if discount_pct > _DEEP_DISCOUNT_PCT:
        return _GREEN
    if discount_pct >= _MODEST_DISCOUNT_PCT:
        return _ORANGE
    return _GREY


def build_embed(payload: AlertPayload) -> dict:
    """The message body, in the shape Discord's webhook API expects."""
    embed: dict = {
        "title": f"🧱  {payload.set_name}",
        "url": payload.url,
        "color": embed_colour(payload.discount_pct),
        "description": "\n".join(_description_lines(payload)),
        "fields": _fields(payload),
    }
    if payload.image_url:
        embed["thumbnail"] = {"url": payload.image_url}
    return embed


def _description_lines(payload: AlertPayload) -> list[str]:
    price = f"**{format_euros(payload.price_eur)}**"
    if payload.rrp_eur is not None and payload.discount_pct is not None:
        price += (
            f"　au lieu de ~~{format_euros(payload.rrp_eur)}~~"
            f"　**-{payload.discount_pct:.0f} %**"
        )
    lines = [price]

    if payload.reason == "all_time_low":
        lines += ["", "🔻 **Plus bas prix jamais observé**"]
        if payload.previous_low_eur is not None and payload.previous_low_at:
            lines.append(
                f"Précédent record : {format_euros(payload.previous_low_eur)} "
                f"le {format_date(payload.previous_low_at)}"
            )
    return lines


def _fields(payload: AlertPayload) -> list[dict]:
    fields = []
    if payload.merchant:
        fields.append({"name": "Marchand", "value": payload.merchant, "inline": True})

    details = [
        f"{payload.pieces:,}".replace(",", _THIN_SPACE) + " pièces"
        if payload.pieces
        else None,
        payload.theme,
        str(payload.year) if payload.year else None,
    ]
    if joined := " · ".join(detail for detail in details if detail):
        fields.append({"name": "Set", "value": joined, "inline": True})
    return fields


def render_console(payload: AlertPayload) -> str:
    """What --dry-run prints. The same facts, without the markup."""
    lines = [f"🧱  {payload.set_name}  [{payload.set_num}]", ""]
    lines += [
        "   " + line.replace("**", "").replace("~~", "")
        for line in _description_lines(payload)
    ]
    lines.append("")
    for field in _fields(payload):
        lines.append(f"   {field['name']} : {field['value']}")
    lines += ["", f"   {payload.url}", ""]
    return "\n".join(lines)


class DiscordWebhook:
    """Posts embeds to one webhook URL."""

    def __init__(self, fetcher: HttpFetcher, *, webhook_url: str) -> None:
        self._fetcher = fetcher
        # Kept off every log line: a webhook URL is a credential, anyone
        # holding it can post to the channel.
        self._webhook_url = webhook_url

    def send(self, payload: AlertPayload) -> None:
        try:
            self._fetcher.post_json(
                self._webhook_url, json={"embeds": [build_embed(payload)]}
            )
        except SourceUnavailableError as exc:
            # Never let the URL reach a log, even inside an exception message.
            raise SourceUnavailableError(
                f"discord webhook refused the alert: {redact_secrets(exc)}"
            ) from None
        _log.info("alert_sent", offer_id=payload.offer_id, reason=payload.reason)
