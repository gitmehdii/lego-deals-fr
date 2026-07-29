import argparse
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy.engine import make_url

from bricks.adapters.cli.common import configure, load_settings
from bricks.config import Settings
from bricks.db.session import create_db_engine, create_session_factory
from bricks.services.health import (
    ALERT_WINDOW_DAYS,
    RESOLUTION_SAMPLE_SIZE,
    HealthReport,
    SourceHealth,
    collect_health,
)

_LABEL_WIDTH = 27


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m bricks.health",
        description="Report the health of the pipeline.",
    )
    parser.parse_args(argv)

    settings = load_settings()
    if settings is None:
        return 2
    configure(settings)

    session_factory = create_session_factory(create_db_engine(settings))
    with session_factory() as session:
        report = collect_health(session)

    print(_render(settings, report))
    # Non-zero when a source looks dead, so a cron or a CI step notices
    # without anyone reading the page.
    return 1 if any(source.looks_dead for source in report.sources) else 0


def _row(label: str, value: object) -> str:
    return f"{label:<{_LABEL_WIDTH}}{value}"


def _render(settings: Settings, report: HealthReport) -> str:
    """Never print DATABASE_URL: a libSQL URL carries an auth token."""
    lines = [
        "bricks pipeline health",
        "======================",
        "",
        _row("Database driver", make_url(settings.database_url).drivername),
        _row("BRICKSET_API_KEY set", settings.brickset_api_key is not None),
        _row("DISCORD_WEBHOOK_URL set", settings.discord_webhook_url is not None),
        "",
        "Runs",
    ]
    lines += _source_lines(report)

    lines += [
        "",
        "Offers",
        _row("  Active", report.active_offers),
        _row("  Total", report.total_offers),
        _row(
            f"  Resolved (last {RESOLUTION_SAMPLE_SIZE})",
            _resolution(report),
        ),
        "",
        "Catalogue",
        _row("  Sets", report.catalogue_sets),
        _row("  With a RRP", _with_rrp(report)),
        "",
        _row(f"Alerts (last {ALERT_WINDOW_DAYS} days)", report.alerts_last_7_days),
    ]

    if dead := [source for source in report.sources if source.looks_dead]:
        lines += [
            "",
            "⚠  " + ", ".join(source.source for source in dead) + " looks dead",
        ]
    return "\n".join(lines)


def _source_lines(report: HealthReport) -> list[str]:
    if not report.sources:
        return [_row("  —", "no run recorded yet")]

    lines = []
    for source in report.sources:
        lines.append(_row(f"  {source.source}", _last_run(source)))
        lines.append(_row("    last success", _ago(source.last_ok_at)))
        if source.consecutive_empty:
            lines.append(_row("    empty in a row", source.consecutive_empty))
        if source.consecutive_failed:
            lines.append(_row("    failed in a row", source.consecutive_failed))
    return lines


def _last_run(source: SourceHealth) -> str:
    if source.last_run_at is None:
        return "never run"
    return f"{source.last_status} · {_ago(source.last_run_at)}"


def _ago(moment: datetime | None) -> str:
    """Relative, because "3 hours ago" answers the question and a timestamp
    makes the reader do arithmetic."""
    if moment is None:
        return "never"
    seconds = (datetime.now(UTC) - moment).total_seconds()
    if seconds < 90:
        return "just now"
    if seconds < 5400:
        return f"{round(seconds / 60)} min ago"
    if seconds < 172800:
        return f"{round(seconds / 3600)} h ago"
    return f"{round(seconds / 86400)} days ago"


def _resolution(report: HealthReport) -> str:
    rate = report.resolution_rate
    if rate is None:
        return "no offer yet"
    return f"{report.resolved_in_sample}/{report.sample_size}  ({rate:.0%})"


def _with_rrp(report: HealthReport) -> str:
    if not report.catalogue_sets:
        return "0  (run: catalog sync)"
    share = report.catalogue_with_rrp / report.catalogue_sets
    return f"{report.catalogue_with_rrp}  ({share:.0%})"
