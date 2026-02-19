"""Volatility scenario API endpoints."""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from typing import List, Optional
from app.core.database import get_db
from app.schemas.volatility import VolatilityScenarioCreate, VolatilityScenarioResponse
from app.repositories.volatility_repository import VolatilityRepository

router = APIRouter(prefix="/api/volatility-scenarios", tags=["volatility"])


@router.post("", response_model=VolatilityScenarioResponse, status_code=201)
def create_volatility_scenario(
    scenario_data: VolatilityScenarioCreate,
    db: Session = Depends(get_db)
):
    """Create a new volatility scenario."""
    repo = VolatilityRepository(db)
    scenario = repo.create(scenario_data)
    return scenario


@router.get("", response_model=List[VolatilityScenarioResponse])
def get_volatility_scenarios(
    underlying_symbol: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """Get all volatility scenarios, optionally filtered by symbol."""
    repo = VolatilityRepository(db)
    scenarios = repo.get_all(underlying_symbol)
    return scenarios
