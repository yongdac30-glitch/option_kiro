"""Position database model."""
from sqlalchemy import Column, Integer, String, Float, Date, DateTime, ForeignKey
from sqlalchemy.sql import func
from app.core.database import Base


class Position(Base):
    """Position model representing an options contract."""
    
    __tablename__ = "positions"
    
    id = Column(Integer, primary_key=True, index=True)
    portfolio_id = Column(Integer, ForeignKey("portfolios.id"), nullable=False, index=True)
    underlying_symbol = Column(String(20), nullable=False, index=True)
    option_type = Column(String(4), nullable=False)  # 'PUT' or 'CALL'
    strike_price = Column(Float, nullable=False)
    expiration_date = Column(Date, nullable=False, index=True)
    quantity = Column(Float, nullable=False)  # Negative for sold positions, supports decimals
    entry_price = Column(Float, nullable=False)  # Premium paid/received
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
