"""Volatility scenario Pydantic schemas."""
from pydantic import BaseModel, Field


class VolatilityScenarioCreate(BaseModel):
    """Schema for creating volatility scenario."""
    name: str = Field(..., max_length=50)
    underlying_symbol: str = Field(..., max_length=20)
    implied_volatility: float = Field(..., gt=0, le=5.0)
    is_default: bool = False


class VolatilityScenarioResponse(BaseModel):
    """Schema for volatility scenario response."""
    id: int
    name: str
    underlying_symbol: str
    implied_volatility: float
    is_default: bool
    
    class Config:
        from_attributes = True
