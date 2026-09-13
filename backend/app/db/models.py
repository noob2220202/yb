from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, String, Text
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
    match browser only ever looks at the latest snapshot per
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


class SentHedgeBox(Base):
    """A record of every hedge box actually sent to Telegram -- doubles as
    the auto-incrementing box number shown to channel members and the
    backing data for the public "박스 상세보기" link a box's Telegram
    button points to.
    """

    __tablename__ = "sent_hedge_boxes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)

    event: Mapped[str] = mapped_column(String(200))
    league: Mapped[str] = mapped_column(String(128), default="")
    commence_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    leg_a_label: Mapped[str] = mapped_column(String(80))
    leg_a_odds: Mapped[float] = mapped_column(Float)
    leg_a_stake: Mapped[float] = mapped_column(Float)

    leg_b_label: Mapped[str] = mapped_column(String(80))
    leg_b_odds: Mapped[float] = mapped_column(Float)
    leg_b_stake: Mapped[float] = mapped_column(Float)

    total_stake: Mapped[float] = mapped_column(Float)
    guaranteed_profit: Mapped[float] = mapped_column(Float)
    profit_percent: Mapped[float] = mapped_column(Float)
    implied_hit_rate_percent: Mapped[float | None] = mapped_column(Float, nullable=True)

    message_text: Mapped[str] = mapped_column(Text)
