"""P&L calculation Pydantic schemas."""
from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import date


class PositionValue(BaseModel):
    """Position value at a specific price point."""
    position_id: int
    current_value: float
    pnl: float


class PricePoint(BaseModel):
    """P&L data at a specific price point."""
    price: float
    total_pnl: float
    position_values: List[PositionValue]


class MaxLossProfit(BaseModel):
    """Maximum loss or profit information."""
    amount: float
    at_price: float


class PnLCalculationRequest(BaseModel):
    """Request schema for P&L calculation."""
    underlying_symbol: str = Field(..., max_length=20)
    current_price: float = Field(..., gt=0)
    implied_volatility: float = Field(..., gt=0, le=5.0)
    price_range_percent: float = Field(default=0.5, gt=0, le=10.0)
    target_date: Optional[date] = None
    portfolio_id: Optional[int] = None
    contract_multiplier: float = Field(default=1.0, gt=0)


class PnLCalculationResponse(BaseModel):
    """Response schema for P&L calculation."""
    underlying_symbol: str
    current_price: float
    price_points: List[PricePoint]
    max_loss: MaxLossProfit
    max_profit: MaxLossProfit
