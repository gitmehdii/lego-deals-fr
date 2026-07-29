import argparse
from collections.abc import Sequence

from sqlalchemy import update
from sqlalchemy.orm import Session

from bricks.adapters.cli.common import configure, load_settings
from bricks.adapters.webhook.discord import (
    DiscordHealthWebhook,
    DiscordWebhook,
    render_console,
    render_health_console,
)
from bricks.config import Settings
from bricks.db.models import Run
from bricks.db.session import create_db_engine, create_session_factory
from bricks.log import get_logger, redact_secrets
from bricks.services.alerts import AlertsReport, detect_and_alert
from bricks.services.health import HealthWarning, warn_about_dead_sources
from bricks.services.ingest import IngestReport, ingest
from bricks.sources.http import HttpFetcher
from bricks.sources.registry import SOURCE_NAMES, build_source

# Unused in v1, which has a single channel. The column exists so the anti-spam
# rules and a future multi-server setup have something to key on.
_CHANNEL_ID = "default"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m bricks.ingest",
        description="Fetch offers from a source, resolve them and alert.",
    )
    parser.add_argument(
        "--source",
        required=True,
        choices=SOURCE_NAMES,
        help="Name of the source to read.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the alerts that would be sent, touching neither Discord "
        "nor the alerts table.",
    )
    args = parser.parse_args(argv)

    settings = load_settings()
    if settings is None:
        return 2
    configure(settings)
    log = get_logger(__name__)

    session_factory = create_session_factory(create_db_engine(settings))
    with HttpFetcher() as fetcher, session_factory() as session:
        failure: Exception | None = None
        report = alerts = previews = None
        try:
            report = ingest(
                session,
                build_source(args.source, fetcher, settings),
                min_resolution_score=settings.min_resolution_score,
            )
            alerts, previews = detect_and_alert(
                session,
                min_discount_pct=settings.min_discount_pct,
                channel_id=_CHANNEL_ID,
                send=_sender(fetcher, settings, dry_run=args.dry_run),
                only_offer_ids=report.seen_offer_ids,
            )
        except Exception as exc:
            # The run row is already written by the service, with the reason.
            # Nothing is re-logged here beyond the exit path.
            log.error("ingest_aborted", source=args.source, error=redact_secrets(exc))
            failure = exc

        # Deliberately outside the try: a run that just failed is precisely
        # when a source needs checking, and the run row is already committed,
        # so this run's own outcome counts towards the streak.
        warnings = _warn_or_log(session, fetcher, settings, args.dry_run, log)

        if failure is not None:
            _print_warnings(warnings)
            return 1

        if not args.dry_run:
            _record_alerts_sent(session, report.run_id, alerts.sent)

    print(_render(report, alerts, previews, warnings, dry_run=args.dry_run))
    return 0


def _sender(fetcher: HttpFetcher, settings: Settings, *, dry_run: bool) -> object:
    """None means "compute everything, send nothing" — the dry run.

    A missing webhook URL takes the same path as --dry-run rather than
    failing: the offers and price points are already worth the run.
    """
    if dry_run:
        return None
    if settings.discord_webhook_url is None:
        get_logger(__name__).warning("discord_webhook_url_missing")
        return None
    webhook = DiscordWebhook(
        fetcher, webhook_url=settings.discord_webhook_url.get_secret_value()
    )
    return webhook.send


def _warn_or_log(
    session: Session,
    fetcher: HttpFetcher,
    settings: Settings,
    dry_run: bool,
    log: object,
) -> list[HealthWarning]:
    """The health check must never be what takes the run down.

    It runs after a failure as well as after a success, so a webhook that is
    itself unreachable would otherwise turn one problem into two.
    """
    try:
        return warn_about_dead_sources(
            session, send=_health_sender(fetcher, settings, dry_run=dry_run)
        )
    except Exception as exc:
        get_logger(__name__).error("health_warning_failed", error=redact_secrets(exc))
        return []


def _print_warnings(warnings: list[HealthWarning]) -> None:
    for warning in warnings:
        print(render_health_console(warning))


def _health_sender(
    fetcher: HttpFetcher, settings: Settings, *, dry_run: bool
) -> object:
    if dry_run or settings.discord_webhook_url is None:
        return None
    webhook = DiscordHealthWebhook(
        fetcher, webhook_url=settings.discord_webhook_url.get_secret_value()
    )
    return webhook.send


def _record_alerts_sent(session: Session, run_id: int, sent: int) -> None:
    session.execute(update(Run).where(Run.id == run_id).values(alerts_sent=sent))
    session.commit()


def _render(
    report: IngestReport,
    alerts: AlertsReport,
    previews: list,
    warnings: list[HealthWarning],
    *,
    dry_run: bool,
) -> str:
    lines = [
        f"ingest: {report.source}",
        "=" * (8 + len(report.source)),
        "",
        f"Run                        {report.run_id}",
        f"Offers found               {report.items_found}",
        f"Offers new                 {report.items_new}",
        f"Offers resolved            {report.items_resolved}",
        f"Price points recorded      {report.price_points}",
        f"Older offers caught up     {report.caught_up}",
        "",
        f"Offers deactivated         {report.deactivated}",
        "",
        f"Offers evaluated           {alerts.considered}",
        f"Alerts suppressed          {alerts.suppressed}",
        f"Alerts sent                {alerts.sent}",
    ]
    if alerts.capped:
        lines.append("Run hit the alert cap; check for a detection bug.")

    for warning in warnings:
        lines += ["", render_health_console(warning)]

    if dry_run:
        lines += ["", f"--dry-run: {len(previews)} alert(s) would be sent", ""]
        lines += [render_console(payload) for payload in previews]
    return "\n".join(lines)
