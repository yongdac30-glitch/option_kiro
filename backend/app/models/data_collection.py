"""Data collection tracking models."""
from sqlalchemy import Column, Integer, String, Float, Date, DateTime, Boolean, UniqueConstraint
from sqlalchemy.sql import func
from app.core.database import Base


class OkxPriceCache(Base):
    """Cache for OKX daily index prices (BTC-USD, ETH-USD, etc.)."""
    __tablename__ = "okx_price_cache"

    id = Column(Integer, primary_key=True, index=True)
    underlying = Column(String(20), nullable=False)  # e.g. "BTC-USD", "ETH-USD"
    trade_date = Column(Date, nullable=False)
    open_price = Column(Float, nullable=True)
    high_price = Column(Float, nullable=True)
    low_price = Column(Float, nullable=True)
    close_price = Column(Float, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint('underlying', 'trade_date', name='uq_okx_price'),
    )


class DataCollectionLog(Base):
    """Track data collection attempts and failures."""
    __tablename__ = "data_collection_log"

    id = Column(Integer, primary_key=True, index=True)
    source = Column(String(20), nullable=False)  # "deribit", "okx", "yfinance"
    data_type = Column(String(20), nullable=False)  # "price", "iv_smile"
    underlying = Column(String(20), nullable=False)
    target_date = Column(Date, nullable=True)
    expiry_date = Column(Date, nullable=True)
    option_type = Column(String(4), nullable=True)
    status = Column(String(20), nullable=False)  # "success", "no_data", "error"
    retry_count = Column(Integer, default=0)
    no_data_confirmed = Column(Boolean, default=False)  # True = tried 3 times, confirmed no data
    error_message = Column(String(500), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint('source', 'data_type', 'underlying', 'target_date',
                         'expiry_date', 'option_type', name='uq_collection_log'),
    )
