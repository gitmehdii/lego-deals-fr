"""What the pipeline looks like from the outside.

Without this the project is a toy: a parser can break and stay broken for
weeks with nobody noticing. Every number here answers "is this still working",
and the resolution rate is the one to watch — if it collapses, either Dealabs
changed its title format or the catalogue has gone stale.
"""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from bricks.db.models import Alert, HealthAlert, HealthAlertReason, Offer, Run, Set
from bricks.log import get_logger

# SPEC.md section 7: this many consecutive empty or failed runs is what
# "the pipeline has died" looks like from the outside.
CONSECUTIVE_BAD_RUNS_BEFORE_WARNING = 3

# The window the resolution rate is measured over. Recent enough to notice a
# format change, wide enough not to swing on a single odd title.
RESOLUTION_SAMPLE_SIZE = 100

ALERT_WINDOW_DAYS = 7

_log = get_logger(__name__)

# SPEC.md section 7: the warning must not be repeated more than once a day.
# A source stays broken for hours; repeating every fifteen minutes would train
# the reader to ignore it, which is the one outcome worse than silence.
MIN_HOURS_BETWEEN_WARNINGS = 24


class SourceHealth(BaseModel):
    source: str
    last_run_at: datetime | None = None
    last_ok_at: datetime | None = None
    last_status: str | None = None
    consecutive_empty: int = 0
    consecutive_failed: int = 0

    @property
    def looks_dead(self) -> bool:
        """Three runs finding nothing, or three failing, per SPEC.md."""
        return self.death_reason is not None

    @property
    def death_reason(self) -> HealthAlertReason | None:
        """Failing outranks silent: an exception says more than a zero count."""
        threshold = CONSECUTIVE_BAD_RUNS_BEFORE_WARNING
        if self.consecutive_failed >= threshold:
            return "failing"
        if self.consecutive_empty >= threshold:
            return "no_items"
        return None


class HealthReport(BaseModel):
    sources: list[SourceHealth] = []
    active_offers: int = 0
    total_offers: int = 0
    resolved_in_sample: int = 0
    sample_size: int = 0
    alerts_last_7_days: int = 0
    catalogue_sets: int = 0
    catalogue_with_rrp: int = 0

    @property
    def resolution_rate(self) -> float | None:
        """None rather than zero when there is nothing to measure yet."""
        if not self.sample_size:
            return None
        return self.resolved_in_sample / self.sample_size


def collect_health(session: Session, *, now: datetime | None = None) -> HealthReport:
    now = now or datetime.now(UTC)
    total_offers = _count(session, Offer)
    since = now - timedelta(days=ALERT_WINDOW_DAYS)

    return HealthReport(
        sources=[_source_health(session, name) for name in _source_names(session)],
        active_offers=_count(session, Offer, Offer.is_active.is_(True)),
        total_offers=total_offers,
        resolved_in_sample=_resolved_in_sample(session),
        sample_size=min(total_offers, RESOLUTION_SAMPLE_SIZE),
        alerts_last_7_days=_count(session, Alert, Alert.sent_at >= since),
        catalogue_sets=_count(session, Set),
        catalogue_with_rrp=_count(session, Set, Set.rrp_eur.is_not(None)),
    )


def _count(session: Session, model: type, *conditions: ColumnElement[bool]) -> int:
    query = select(func.count()).select_from(model)
    return session.scalar(query.where(*conditions) if conditions else query) or 0


def _source_names(session: Session) -> list[str]:
    return list(session.scalars(select(Run.source).distinct().order_by(Run.source)))


def _source_health(session: Session, source: str) -> SourceHealth:
    runs = list(
        session.scalars(
            select(Run)
            .where(Run.source == source, Run.status != "running")
            .order_by(Run.started_at.desc())
            .limit(CONSECUTIVE_BAD_RUNS_BEFORE_WARNING * 3)
        )
    )
    last_ok = session.scalars(
        select(Run)
        .where(Run.source == source, Run.status == "ok")
        .order_by(Run.started_at.desc())
    ).first()

    return SourceHealth(
        source=source,
        last_run_at=runs[0].started_at if runs else None,
        last_status=runs[0].status if runs else None,
        last_ok_at=last_ok.started_at if last_ok else None,
        consecutive_empty=_streak(runs, lambda run: run.items_found == 0),
        consecutive_failed=_streak(runs, lambda run: run.status == "error"),
    )


def _streak(runs: list[Run], matches: Callable[[Run], bool]) -> int:
    """How many of the most recent runs in a row satisfy `matches`."""
    streak = 0
    for run in runs:
        if not matches(run):
            break
        streak += 1
    return streak


def _resolved_in_sample(session: Session) -> int:
    """How many of the most recent offers carry a set_num.

    The metric to watch: if it collapses, either Dealabs changed its title
    format or the catalogue has gone stale.
    """
    recent = (
        select(Offer.set_num)
        .order_by(Offer.id.desc())
        .limit(RESOLUTION_SAMPLE_SIZE)
        .subquery()
    )
    query = (
        select(func.count()).select_from(recent).where(recent.c.set_num.is_not(None))
    )
    return session.scalar(query) or 0


class HealthWarning(BaseModel):
    """A source has stopped producing. What a reader needs, no presentation."""

    source: str
    reason: HealthAlertReason
    consecutive_runs: int
    last_ok_at: datetime | None = None


def warn_about_dead_sources(
    session: Session,
    *,
    send: Callable[[HealthWarning], None] | None = None,
    now: datetime | None = None,
) -> list[HealthWarning]:
    """Warn once per source per day about sources that have gone quiet.

    `send` is injected, and None is the dry run: warnings are computed and
    returned, nothing is sent and no row is written. Mirrors detect_and_alert
    so both paths behave the same way under --dry-run.
    """
    now = now or datetime.now(UTC)
    report = collect_health(session, now=now)

    warnings: list[HealthWarning] = []
    for source in report.sources:
        reason = source.death_reason
        if reason is None:
            continue
        if _warned_recently(session, source.source, now):
            _log.info("health_warning_suppressed", source=source.source)
            continue

        warning = HealthWarning(
            source=source.source,
            reason=reason,
            consecutive_runs=(
                source.consecutive_failed
                if reason == "failing"
                else source.consecutive_empty
            ),
            last_ok_at=source.last_ok_at,
        )
        warnings.append(warning)

        if send is None:
            continue
        send(warning)
        # Recorded only after a successful send, so the 24h rule counts
        # warnings that were actually delivered.
        session.add(HealthAlert(source=warning.source, reason=reason, sent_at=now))
        session.commit()
        _log.warning("health_warning_sent", source=warning.source, reason=reason)

    return warnings


def _warned_recently(session: Session, source: str, now: datetime) -> bool:
    cutoff = now - timedelta(hours=MIN_HOURS_BETWEEN_WARNINGS)
    last = session.scalars(
        select(HealthAlert)
        .where(HealthAlert.source == source)
        .order_by(HealthAlert.sent_at.desc())
    ).first()
    return last is not None and last.sent_at > cutoff
