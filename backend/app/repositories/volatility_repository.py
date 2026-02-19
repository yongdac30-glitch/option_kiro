"""Volatility scenario repository for database operations."""
from sqlalchemy.orm import Session
from typing import List, Optional
from app.models.volatility_scenario import VolatilityScenario
from app.schemas.volatility import VolatilityScenarioCreate


class VolatilityRepository:
    """Repository for VolatilityScenario CRUD operations."""
    
    def __init__(self, db: Session):
        self.db = db
    
    def create(self, scenario_data: VolatilityScenarioCreate) -> VolatilityScenario:
        """Create a new volatility scenario."""
        scenario = VolatilityScenario(**scenario_data.model_dump())
        self.db.add(scenario)
        self.db.commit()
        self.db.refresh(scenario)
        return scenario
    
    def get_all(self, underlying_symbol: Optional[str] = None) -> List[VolatilityScenario]:
        """Get all volatility scenarios, optionally filtered by symbol."""
        query = self.db.query(VolatilityScenario)
        if underlying_symbol:
            query = query.filter(VolatilityScenario.underlying_symbol == underlying_symbol)
        return query.all()
    
    def get_default(self, underlying_symbol: str) -> Optional[VolatilityScenario]:
        """Get default volatility scenario for a symbol."""
        return self.db.query(VolatilityScenario).filter(
            VolatilityScenario.underlying_symbol == underlying_symbol,
            VolatilityScenario.is_default == True
        ).first()
