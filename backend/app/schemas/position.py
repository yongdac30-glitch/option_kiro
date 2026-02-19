"""Position Pydantic schemas."""
from pydantic import BaseModel, Field, field_validator
from datetime import date


class PositionBase(BaseModel):
    """Base position schema."""
    portfolio_id: int
    underlying_symbol: str = Field(..., max_length=20)
    option_type: str = Field(..., pattern="^(PUT|CALL)$")
    strike_price: float = Field(..., gt=0)
    expiration_date: date
    quantity: float
    entry_price: float = Field(..., ge=0)

    @field_validator('quantity')
    @classmethod
    def quantity_not_zero(cls, v):
        if v == 0:
            raise ValueError('数量不能为0')
        return v


class PositionCreate(PositionBase):
    """Schema for creating a position."""
    pass


class PositionUpdate(PositionBase):
    """Schema for updating a position."""
    pass


class PositionResponse(PositionBase):
    """Schema for position response."""
    id: int
    
    class Config:
        from_attributes = True
