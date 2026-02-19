"""Market price database model."""
from sqlalchemy import Column, Integer, String, Float, DateTime
from sqlalchemy.sql import func
from app.core.database import Base


class MarketPrice(Base):
    """Market price model for underlying assets."""
    
    __tablename__ = "market_prices"
    
    id = Column(Integer, primary_key=True, index=True)
    underlying_symbol = Column(String(20), nullable=False, unique=True, index=True)
    current_price = Column(Float, nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
