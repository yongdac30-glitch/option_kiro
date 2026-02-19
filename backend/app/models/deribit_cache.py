"""Deribit historical data cache models."""
from sqlalchemy import Column, Integer, String, Float, Date, DateTime, UniqueConstraint
from sqlalchemy.sql import func
from app.core.database import Base


class DeribitPriceCache(Base):
    """Cache for Deribit daily index prices."""
    __tablename__ = "deribit_price_cache"

    id = Column(Integer, primary_key=True, index=True)
    underlying = Column(String(10), nullable=False)
    trade_date = Column(Date, nullable=False)
    close_price = Column(Float, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint('underlying', 'trade_date', name='uq_deribit_price'),
    )


class DeribitIVCache(Base):
    """Cache for Deribit IV smile data points.
    Each row = one (strike, iv) point from a smile fetch."""
    __tablename__ = "deribit_iv_cache"

    id = Column(Integer, primary_key=True, index=True)
    underlying = Column(String(10), nullable=False)
    expiry_date = Column(Date, nullable=False)
    option_type = Column(String(4), nullable=False)  # PUT or CALL
    target_date = Column(Date, nullable=False)  # the date we queried around
    spot_price = Column(Float, nullable=False)
    strike = Column(Float, nullable=False)
    iv = Column(Float, nullable=False)
    trade_price_usd = Column(Float, nullable=False)
    instrument = Column(String(50), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint('underlying', 'expiry_date', 'option_type',
                         'target_date', 'strike', name='uq_deribit_iv'),
    )
