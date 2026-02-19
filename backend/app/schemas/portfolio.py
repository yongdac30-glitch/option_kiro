"""Portfolio Pydantic schemas."""
from pydantic import BaseModel, Field


class PortfolioCreate(BaseModel):
    """Schema for creating a portfolio."""
    name: str = Field(..., max_length=100)


class PortfolioResponse(BaseModel):
    """Schema for portfolio response."""
    id: int
    name: str
    
    class Config:
        from_attributes = True
