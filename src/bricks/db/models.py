"""SQLAlchemy models, mirroring schema.sql column for column.

schema.sql stays the reference document. tests/test_schema_fidelity.py proves
that these models, the Alembic migration and schema.sql produce the same DDL,
so the three can never drift apart unnoticed.
"""

from datetime import datetime
from typing import Literal

from sqlalchemy import ForeignKey, Index, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from bricks.db.base import Base

# Columns annotated with these need an explicit Text type: left to itself,
# SQLAlchemy turns a Literal into an Enum and emits VARCHAR(n).
ResolutionMethod = Literal["set_number", "fuzzy_name", "manual"]
AlertReason = Literal["discount_threshold", "all_time_low"]
RunStatus = Literal["running", "ok", "error"]


class Set(Base):
    """A LEGO product identified by its official number. Never carries a price."""

    __tablename__ = "sets"
    __table_args__ = (
        Index("idx_sets_name_normalized", "name_normalized"),
        Index("idx_sets_theme", "theme"),
    )

    set_num: Mapped[str] = mapped_column(primary_key=True)
    name: Mapped[str]
    name_normalized: Mapped[str]
    theme: Mapped[str | None]
    year: Mapped[int | None]
    pieces: Mapped[int | None]
    rrp_eur: Mapped[float | None]
    image_url: Mapped[str | None]
    updated_at: Mapped[datetime]

    offers: Mapped[list["Offer"]] = relationship(back_populates="set", lazy="raise")


class Offer(Base):
    """A set on sale at a merchant, at a URL. set_num is NULL when unresolved."""

    __tablename__ = "offers"
    __table_args__ = (
        UniqueConstraint("source", "external_id"),
        Index("idx_offers_set_num", "set_num"),
        Index("idx_offers_active", "is_active", "last_seen_at"),
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    set_num: Mapped[str | None] = mapped_column(ForeignKey("sets.set_num"))
    resolution_score: Mapped[float | None]
    resolution_method: Mapped[ResolutionMethod | None] = mapped_column(Text)
    source: Mapped[str]
    external_id: Mapped[str]
    merchant: Mapped[str | None]
    title_raw: Mapped[str]
    url: Mapped[str]
    current_price_eur: Mapped[float | None]
    first_seen_at: Mapped[datetime]
    last_seen_at: Mapped[datetime]
    is_active: Mapped[bool] = mapped_column(server_default=text("1"))

    set: Mapped["Set | None"] = relationship(back_populates="offers", lazy="raise")
    price_points: Mapped[list["PricePoint"]] = relationship(
        back_populates="offer", lazy="raise"
    )
    alerts: Mapped[list["Alert"]] = relationship(back_populates="offer", lazy="raise")


class PricePoint(Base):
    """One observed price. Append-only: never UPDATE, never DELETE."""

    __tablename__ = "price_points"
    __table_args__ = (
        Index("idx_price_points_offer", "offer_id", "observed_at"),
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    offer_id: Mapped[int] = mapped_column(ForeignKey("offers.id"))
    price_eur: Mapped[float]
    observed_at: Mapped[datetime]

    offer: Mapped["Offer"] = relationship(back_populates="price_points", lazy="raise")


class Alert(Base):
    """A Discord message actually sent. Price is snapshotted at send time."""

    __tablename__ = "alerts"
    __table_args__ = (
        Index("idx_alerts_offer", "offer_id", "sent_at"),
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    offer_id: Mapped[int] = mapped_column(ForeignKey("offers.id"))
    guild_id: Mapped[str | None]
    channel_id: Mapped[str]
    price_eur: Mapped[float]

    # Percentage, 0-100.
    discount_pct: Mapped[float | None]

    reason: Mapped[AlertReason] = mapped_column(Text)
    sent_at: Mapped[datetime]

    offer: Mapped["Offer"] = relationship(back_populates="alerts", lazy="raise")


class Run(Base):
    """One pipeline execution. Written even when the run crashes."""

    __tablename__ = "runs"
    __table_args__ = (
        Index("idx_runs_source", "source", "started_at"),
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    source: Mapped[str]
    started_at: Mapped[datetime]
    finished_at: Mapped[datetime | None]
    items_found: Mapped[int] = mapped_column(server_default=text("0"))
    items_new: Mapped[int] = mapped_column(server_default=text("0"))
    items_resolved: Mapped[int] = mapped_column(server_default=text("0"))
    alerts_sent: Mapped[int] = mapped_column(server_default=text("0"))
    status: Mapped[RunStatus] = mapped_column(Text)
    error: Mapped[str | None]
