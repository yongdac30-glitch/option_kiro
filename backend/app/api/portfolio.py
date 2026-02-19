"""Portfolio API endpoints."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
from app.core.database import get_db
from app.schemas.portfolio import PortfolioCreate, PortfolioResponse
from app.repositories.portfolio_repository import PortfolioRepository

router = APIRouter(prefix="/api/portfolios", tags=["portfolios"])


@router.post("", response_model=PortfolioResponse, status_code=201)
def create_portfolio(
    data: PortfolioCreate,
    db: Session = Depends(get_db)
):
    """Create a new portfolio."""
    repo = PortfolioRepository(db)
    return repo.create(data)


@router.get("", response_model=List[PortfolioResponse])
def get_portfolios(db: Session = Depends(get_db)):
    """Get all portfolios."""
    repo = PortfolioRepository(db)
    return repo.get_all()


@router.delete("/{portfolio_id}")
def delete_portfolio(
    portfolio_id: int,
    db: Session = Depends(get_db)
):
    """Delete a portfolio and all its positions."""
    repo = PortfolioRepository(db)
    success = repo.delete(portfolio_id)
    if not success:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    return {"message": "Portfolio deleted"}
