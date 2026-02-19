"""Market price Pydantic schemas."""
from pydantic import BaseModel, Field


class MarketPriceUpdate(BaseModel):
    """Schema for updating market price."""
    underlying_symbol: str = Field(..., max_length=20)
    current_price: float = Field(..., gt=0)


class MarketPriceResponse(BaseModel):
    """Schema for market price response."""
    underlying_symbol: str
    current_price: float
    
    class Config:
        from_attributes = True
