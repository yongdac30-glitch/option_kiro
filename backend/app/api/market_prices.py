"""Market price API endpoints."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.schemas.market_price import MarketPriceUpdate, MarketPriceResponse
from app.repositories.market_price_repository import MarketPriceRepository

router = APIRouter(prefix="/api/market-prices", tags=["market-prices"])


@router.post("", response_model=MarketPriceResponse)
def update_market_price(
    price_data: MarketPriceUpdate,
    db: Session = Depends(get_db)
):
    """Update market price for a symbol."""
    repo = MarketPriceRepository(db)
    market_price = repo.upsert(
        price_data.underlying_symbol,
        price_data.current_price
    )
    return market_price


@router.get("/{symbol}", response_model=MarketPriceResponse)
def get_market_price(
    symbol: str,
    db: Session = Depends(get_db)
):
    """Get market price for a symbol."""
    repo = MarketPriceRepository(db)
    market_price = repo.get_by_symbol(symbol)
    if not market_price:
        raise HTTPException(status_code=404, detail="Market price not found")
    return market_price
