import os
from pathlib import Path
from dotenv import load_dotenv
from sqlalchemy import create_engine, String, Date, DateTime, Integer, BigInteger, Numeric, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from datetime import date, datetime, timezone

load_dotenv(Path(__file__).resolve().parents[2] / '.env', override=False)
DATABASE_URL = os.getenv('DATABASE_URL', 'sqlite:///./margin.db')
engine = create_engine(DATABASE_URL, connect_args={'check_same_thread': False} if DATABASE_URL.startswith('sqlite') else {}, pool_pre_ping=True)
Session = sessionmaker(bind=engine)
class Base(DeclarativeBase): pass
class RawResponse(Base):
    __tablename__ = 'raw_responses'
    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(500))
    trading_date: Mapped[date] = mapped_column(Date)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    payload: Mapped[str] = mapped_column(Text)
class Observation(Base):
    __tablename__ = 'observations'
    __table_args__ = (UniqueConstraint('market','code','trading_date'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    market: Mapped[str] = mapped_column(String(8), index=True)
    code: Mapped[str] = mapped_column(String(20), index=True)
    name: Mapped[str] = mapped_column(String(100))
    trading_date: Mapped[date] = mapped_column(Date, index=True)
    open: Mapped[object | None] = mapped_column(Numeric(20,4), nullable=True)
    high: Mapped[object | None] = mapped_column(Numeric(20,4), nullable=True)
    low: Mapped[object | None] = mapped_column(Numeric(20,4), nullable=True)
    close: Mapped[object | None] = mapped_column(Numeric(20,4), nullable=True)
    volume: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    turnover: Mapped[object | None] = mapped_column(Numeric(24,0), nullable=True)
    margin_buy: Mapped[int] = mapped_column(Integer)
    margin_sell: Mapped[int] = mapped_column(Integer)
    repayment: Mapped[int] = mapped_column(Integer)
    previous_balance: Mapped[int] = mapped_column(Integer)
    balance: Mapped[int] = mapped_column(Integer)
    limit: Mapped[int] = mapped_column(Integer)
    quality: Mapped[str] = mapped_column(String(100), default='unreviewed_corporate_actions')
class Watch(Base):
    __tablename__='watchlist'
    code: Mapped[str] = mapped_column(String(20),primary_key=True)
    maintenance_threshold: Mapped[object] = mapped_column(Numeric(10,2), default=140)
    distance_threshold: Mapped[object] = mapped_column(Numeric(10,2), default=5)


class ProviderResponse(Base):
    """Immutable raw provider responses; credentials are never saved in the URL."""
    __tablename__ = 'provider_responses'
    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(30), index=True)
    dataset: Mapped[str] = mapped_column(String(100))
    code: Mapped[str] = mapped_column(String(20), index=True)
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    capture_mode: Mapped[str] = mapped_column(String(20))
    source_url: Mapped[str] = mapped_column(String(1000))
    http_status: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    payload: Mapped[str] = mapped_column(Text)


class ProviderObservation(Base):
    """Research-only observations. Official observations remain untouched."""
    __tablename__ = 'provider_observations'
    __table_args__ = (UniqueConstraint('provider', 'code', 'trading_date'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(30), index=True)
    code: Mapped[str] = mapped_column(String(20), index=True)
    trading_date: Mapped[date] = mapped_column(Date, index=True)
    open: Mapped[object | None] = mapped_column(Numeric(20, 6), nullable=True)
    high: Mapped[object | None] = mapped_column(Numeric(20, 6), nullable=True)
    low: Mapped[object | None] = mapped_column(Numeric(20, 6), nullable=True)
    close: Mapped[object | None] = mapped_column(Numeric(20, 6), nullable=True)
    volume: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    turnover: Mapped[object | None] = mapped_column(Numeric(24, 0), nullable=True)
    margin_buy: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    margin_sell: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    repayment: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    previous_balance: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    balance: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    limit: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

# Register the durable warehouse tables on the same migration metadata.
from . import warehouse_schema as warehouse_schema  # noqa: E402,F401

