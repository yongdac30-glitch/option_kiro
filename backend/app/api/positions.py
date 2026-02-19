"""Position API endpoints."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import List, Optional
from app.core.database import get_db
from app.schemas.position import PositionCreate, PositionUpdate, PositionResponse
from app.repositories.position_repository import PositionRepository


class BatchDeleteRequest(BaseModel):
    ids: List[int]

router = APIRouter(prefix="/api/positions", tags=["positions"])


@router.post("", response_model=PositionResponse, status_code=201)
def create_position(
    position_data: PositionCreate,
    db: Session = Depends(get_db)
):
    """Create a new position."""
    repo = PositionRepository(db)
    position = repo.create(position_data)
    return position


@router.get("", response_model=List[PositionResponse])
def get_positions(
    underlying_symbol: Optional[str] = None,
    portfolio_id: Optional[int] = None,
    db: Session = Depends(get_db)
):
    """Get all positions, optionally filtered by symbol and/or portfolio."""
    repo = PositionRepository(db)
    positions = repo.get_all(underlying_symbol=underlying_symbol, portfolio_id=portfolio_id)
    return positions


@router.get("/{position_id}", response_model=PositionResponse)
def get_position(
    position_id: int,
    db: Session = Depends(get_db)
):
    """Get a specific position by ID."""
    repo = PositionRepository(db)
    position = repo.get_by_id(position_id)
    if not position:
        raise HTTPException(status_code=404, detail="Position not found")
    return position


@router.put("/{position_id}", response_model=PositionResponse)
def update_position(
    position_id: int,
    position_data: PositionUpdate,
    db: Session = Depends(get_db)
):
    """Update a position."""
    repo = PositionRepository(db)
    position = repo.update(position_id, position_data)
    if not position:
        raise HTTPException(status_code=404, detail="Position not found")
    return position


@router.post("/batch-delete")
def batch_delete_positions(
    request: BatchDeleteRequest,
    db: Session = Depends(get_db)
):
    """Batch delete positions by IDs."""
    repo = PositionRepository(db)
    deleted = repo.batch_delete(request.ids)
    return {"deleted": deleted}


@router.delete("/{position_id}")
def delete_position(
    position_id: int,
    db: Session = Depends(get_db)
):
    """Delete a position."""
    repo = PositionRepository(db)
    success = repo.delete(position_id)
    if not success:
        raise HTTPException(status_code=404, detail="Position not found")
    return {"message": "Position deleted"}
