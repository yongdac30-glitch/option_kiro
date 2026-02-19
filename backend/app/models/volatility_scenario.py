"""Volatility scenario database model."""
from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime
from sqlalchemy.sql import func
from app.core.database import Base


class VolatilityScenario(Base):
    """Volatility scenario model for different IV assumptions."""
    
    __tablename__ = "volatility_scenarios"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(50), nullable=False)
    underlying_symbol = Column(String(20), nullable=False, index=True)
    implied_volatility = Column(Float, nullable=False)  # e.g., 0.25 for 25%
    is_default = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
