"""High-frequency option tick data model."""
from sqlalchemy import Column, Integer, String, Float, DateTime, Index
from sqlalchemy.sql import func
from app.core.database import Base


class HFOptionTick(Base):
    """High-frequency option tick snapshot.
    Each row = one instrument at one minute snapshot."""
    __tablename__ = "hf_option_tick"

    id = Column(Integer, primary_key=True, index=True)
    underlying = Column(String(10), nullable=False)       # BTC, ETH
    instrument_name = Column(String(60), nullable=False)   # BTC-28MAR26-100000-P
    expiry_date = Column(String(12), nullable=False)       # 2026-03-28
    option_type = Column(String(4), nullable=False)        # PUT, CALL
    strike = Column(Float, nullable=False)
    snapshot_time = Column(DateTime(timezone=True), nullable=False)  # minute-level timestamp
    spot_price = Column(Float, nullable=False)             # underlying index price
    bid_price = Column(Float, nullable=True)               # best bid (in coin, e.g. BTC)
    ask_price = Column(Float, nullable=True)               # best ask (in coin)
    last_price = Column(Float, nullable=True)              # last traded price (in coin)
    mark_price = Column(Float, nullable=True)              # mark price (in coin)
    bid_usd = Column(Float, nullable=True)                 # bid * spot
    ask_usd = Column(Float, nullable=True)                 # ask * spot
    last_usd = Column(Float, nullable=True)                # last * spot
    mark_usd = Column(Float, nullable=True)                # mark * spot
    volume_24h = Column(Float, nullable=True)              # 24h volume (in coin)
    open_interest = Column(Float, nullable=True)           # open interest (in coin)
    iv_mark = Column(Float, nullable=True)                 # implied vol from mark price
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index('ix_hf_tick_lookup', 'underlying', 'snapshot_time', 'expiry_date', 'strike'),
        Index('ix_hf_tick_time', 'underlying', 'snapshot_time'),
    )
