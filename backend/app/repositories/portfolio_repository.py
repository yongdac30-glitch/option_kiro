"""Portfolio repository for database operations."""
from sqlalchemy.orm import Session
from typing import List, Optional
from app.models.portfolio import Portfolio
from app.schemas.portfolio import PortfolioCreate


class PortfolioRepository:
    """Repository for Portfolio CRUD operations."""
    
    def __init__(self, db: Session):
        self.db = db
    
    def create(self, data: PortfolioCreate) -> Portfolio:
        """Create a new portfolio."""
        portfolio = Portfolio(**data.model_dump())
        self.db.add(portfolio)
        self.db.commit()
        self.db.refresh(portfolio)
        return portfolio
    
    def get_by_id(self, portfolio_id: int) -> Optional[Portfolio]:
        """Get portfolio by ID."""
        return self.db.query(Portfolio).filter(Portfolio.id == portfolio_id).first()
    
    def get_all(self) -> List[Portfolio]:
        """Get all portfolios."""
        return self.db.query(Portfolio).all()
    
    def delete(self, portfolio_id: int) -> bool:
        """Delete a portfolio and its positions."""
        from app.models.position import Position
        portfolio = self.get_by_id(portfolio_id)
        if not portfolio:
            return False
        # Delete all positions in this portfolio
        self.db.query(Position).filter(Position.portfolio_id == portfolio_id).delete()
        self.db.delete(portfolio)
        self.db.commit()
        return True
