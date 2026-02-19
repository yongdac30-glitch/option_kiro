"""Position repository for database operations."""
from sqlalchemy.orm import Session
from typing import List, Optional
from app.models.position import Position
from app.schemas.position import PositionCreate, PositionUpdate


class PositionRepository:
    """Repository for Position CRUD operations."""
    
    def __init__(self, db: Session):
        self.db = db
    
    def create(self, position_data: PositionCreate) -> Position:
        """Create a new position."""
        position = Position(**position_data.model_dump())
        self.db.add(position)
        self.db.commit()
        self.db.refresh(position)
        return position
    
    def get_by_id(self, position_id: int) -> Optional[Position]:
        """Get position by ID."""
        return self.db.query(Position).filter(Position.id == position_id).first()
    
    def get_all(self, underlying_symbol: Optional[str] = None, portfolio_id: Optional[int] = None) -> List[Position]:
        """Get all positions, optionally filtered by symbol and/or portfolio."""
        query = self.db.query(Position)
        if portfolio_id is not None:
            query = query.filter(Position.portfolio_id == portfolio_id)
        if underlying_symbol:
            query = query.filter(Position.underlying_symbol == underlying_symbol)
        return query.all()
    
    def update(self, position_id: int, position_data: PositionUpdate) -> Optional[Position]:
        """Update a position."""
        position = self.get_by_id(position_id)
        if not position:
            return None
        for key, value in position_data.model_dump().items():
            setattr(position, key, value)
        self.db.commit()
        self.db.refresh(position)
        return position
    
    def delete(self, position_id: int) -> bool:
        """Delete a position."""
        position = self.get_by_id(position_id)
        if not position:
            return False
        self.db.delete(position)
        self.db.commit()
        return True

    def batch_delete(self, ids: List[int]) -> int:
        """Batch delete positions by IDs. Returns count of deleted."""
        deleted = self.db.query(Position).filter(Position.id.in_(ids)).delete(synchronize_session='fetch')
        self.db.commit()
        return deleted
