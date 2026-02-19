"""Market price repository for database operations."""
from sqlalchemy.orm import Session
from typing import Optional
from app.models.market_price import MarketPrice


class MarketPriceRepository:
    """Repository for MarketPrice CRUD operations."""
    
    def __init__(self, db: Session):
        self.db = db
    
    def upsert(self, underlying_symbol: str, current_price: float) -> MarketPrice:
        """Create or update market price."""
        market_price = self.db.query(MarketPrice).filter(
            MarketPrice.underlying_symbol == underlying_symbol
        ).first()
        
        if market_price:
            market_price.current_price = current_price
        else:
            market_price = MarketPrice(
                underlying_symbol=underlying_symbol,
                current_price=current_price
            )
            self.db.add(market_price)
        
        self.db.commit()
        self.db.refresh(market_price)
        return market_price
    
    def get_by_symbol(self, symbol: str) -> Optional[MarketPrice]:
        """Get market price by symbol."""
        return self.db.query(MarketPrice).filter(
            MarketPrice.underlying_symbol == symbol
        ).first()
