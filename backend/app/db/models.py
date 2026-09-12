from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Bookmaker(Base):
    __tablename__ = "bookmakers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    source: Mapped[str] = mapped_column(String(64))  # provider adapter that supplies this book
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_key: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    sport: Mapped[str] = mapped_column(String(32))
    league: Mapped[str] = mapped_column(String(128), default="")
    home_team: Mapped[str] = mapped_column(String(128))
    away_team: Mapped[str] = mapped_column(String(128))
    commence_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    odds_quotes: Mapped[list["OddsSnapshot"]] = relationship(back_populates="event")


class OddsSnapshot(Base):
    """One priced selection observed at one point in time.

    Kept append-only so odds movement can be replayed/audited later; the
    scanner only ever looks at the latest snapshot per
    (event, bookmaker, market, line, selection).
    """

    __tablename__ = "odds_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"), index=True)
    bookmaker_id: Mapped[int] = mapped_column(ForeignKey("bookmakers.id"), index=True)
    market: Mapped[str] = mapped_column(String(32))
    line: Mapped[float | None] = mapped_column(Float, nullable=True)
    selection: Mapped[str] = mapped_column(String(32))
    decimal_odds: Mapped[float] = mapped_column(Float)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)

    event: Mapped[Event] = relationship(back_populates="odds_quotes")
    bookmaker: Mapped[Bookmaker] = relationship()

    __table_args__ = (
        Index("ix_odds_snapshot_group", "event_id", "market", "line", "fetched_at"),
    )


class ArbitrageOpportunity(Base):
    """A guaranteed-profit combination the scanner detected, snapshotted at
    the moment of detection (odds can move a few seconds later)."""

    __tablename__ = "arbitrage_opportunities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"), index=True)
    market: Mapped[str] = mapped_column(String(32))
    line: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_implied_probability: Mapped[float] = mapped_column(Float)
    margin_percent: Mapped[float] = mapped_column(Float)
    push_possible: Mapped[bool] = mapped_column(Boolean, default=False)
    legs_json: Mapped[str] = mapped_column(Text)  # JSON list of {selection, bookmaker, decimal_odds}
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)

    event: Mapped[Event] = relationship()


class ValueEdge(Base):
    """A model-vs-market probability mismatch on an exotic market (correct
    score, winning margin). NOT a guaranteed-profit signal — see
    app/engine/scoreline_model.py docstring.
    """

    __tablename__ = "value_edges"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"), index=True)
    market: Mapped[str] = mapped_column(String(32))
    selection: Mapped[str] = mapped_column(String(32))
    bookmaker: Mapped[str] = mapped_column(String(64))
    quoted_decimal_odds: Mapped[float] = mapped_column(Float)
    model_probability: Mapped[float] = mapped_column(Float)
    implied_probability: Mapped[float] = mapped_column(Float)
    edge_percent: Mapped[float] = mapped_column(Float)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)

    event: Mapped[Event] = relationship()


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    api_key_hash: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    plan: Mapped[str] = mapped_column(String(32), default="trial")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
