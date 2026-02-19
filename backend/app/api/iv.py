"""Implied Volatility calculation API endpoint."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from datetime import date
from app.core.config import settings
from app.services.pricing import calculate_time_to_expiration, implied_volatility

router = APIRouter(prefix="/api", tags=["iv"])


class IVRequest(BaseModel):
    """Request schema for IV calculation."""
    option_type: str = Field(..., pattern="^(PUT|CALL)$")
    option_price: float = Field(..., gt=0)
    underlying_price: float = Field(..., gt=0)
    strike_price: float = Field(..., gt=0)
    expiration_date: date
    current_date: date


class IVResponse(BaseModel):
    """Response schema for IV calculation."""
    implied_volatility: float
    implied_volatility_pct: float


@router.post("/calculate-iv", response_model=IVResponse)
def calculate_iv(request: IVRequest):
    """Calculate implied volatility from option market price."""
    T = calculate_time_to_expiration(request.expiration_date, request.current_date)

    if T <= 0.0001:
        raise HTTPException(status_code=400, detail="期权已到期或到期日无效")

    iv = implied_volatility(
        market_price=request.option_price,
        S=request.underlying_price,
        K=request.strike_price,
        T=T,
        r=settings.RISK_FREE_RATE,
        option_type=request.option_type,
    )

    if iv is None:
        raise HTTPException(status_code=400, detail="无法计算隐含波动率，请检查输入参数")

    return IVResponse(
        implied_volatility=round(iv, 6),
        implied_volatility_pct=round(iv * 100, 2),
    )
