"""P&L calculation API endpoints."""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.config import settings
from app.schemas.pnl import PnLCalculationRequest, PnLCalculationResponse
from app.repositories.position_repository import PositionRepository
from app.services.pricing import (
    generate_price_points,
    calculate_portfolio_pnl,
    find_max_loss,
    find_max_profit
)

router = APIRouter(prefix="/api", tags=["pnl"])


@router.post("/calculate-pnl", response_model=PnLCalculationResponse)
def calculate_pnl(
    request: PnLCalculationRequest,
    db: Session = Depends(get_db)
):
    """Calculate portfolio P&L across price range."""
    # Get positions for the symbol (and portfolio if specified)
    repo = PositionRepository(db)
    positions = repo.get_all(underlying_symbol=request.underlying_symbol, portfolio_id=request.portfolio_id)
    
    if not positions:
        # Return empty result if no positions
        return PnLCalculationResponse(
            underlying_symbol=request.underlying_symbol,
            current_price=request.current_price,
            price_points=[],
            max_loss={'amount': 0.0, 'at_price': request.current_price},
            max_profit={'amount': 0.0, 'at_price': request.current_price}
        )
    
    # Convert positions to dicts
    position_dicts = [
        {
            'id': p.id,
            'underlying_symbol': p.underlying_symbol,
            'option_type': p.option_type,
            'strike_price': p.strike_price,
            'expiration_date': p.expiration_date,
            'quantity': p.quantity,
            'entry_price': p.entry_price
        }
        for p in positions
    ]
    
    # Generate price points
    price_points = generate_price_points(
        request.current_price,
        request.price_range_percent,
        positions=position_dicts
    )
    
    # Calculate P&L for each price point
    pnl_data = calculate_portfolio_pnl(
        positions=position_dicts,
        price_points=price_points,
        implied_volatility=request.implied_volatility,
        risk_free_rate=settings.RISK_FREE_RATE,
        target_date=request.target_date,
        contract_multiplier=request.contract_multiplier,
    )
    
    # Find max loss and profit
    max_loss = find_max_loss(pnl_data)
    max_profit = find_max_profit(pnl_data)
    
    return PnLCalculationResponse(
        underlying_symbol=request.underlying_symbol,
        current_price=request.current_price,
        price_points=pnl_data,
        max_loss=max_loss,
        max_profit=max_profit
    )
