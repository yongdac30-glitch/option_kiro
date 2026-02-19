"""Hedge suggestion API endpoint."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from typing import Optional, List
from datetime import date
from app.core.database import get_db
from app.core.config import settings
from app.repositories.position_repository import PositionRepository
from app.services.pricing import (
    generate_price_points,
    calculate_portfolio_pnl,
    black_scholes_price,
    calculate_time_to_expiration,
    find_max_loss,
)

router = APIRouter(prefix="/api", tags=["hedge"])


class HedgeRequest(BaseModel):
    """Request for hedge suggestion."""
    underlying_symbol: str = Field(..., max_length=20)
    current_price: float = Field(..., gt=0)
    implied_volatility: float = Field(..., gt=0, le=5.0)
    portfolio_id: Optional[int] = None
    target_date: Optional[date] = None
    target_max_loss: Optional[float] = None   # negative, e.g. -5000
    max_hedge_cost: Optional[float] = None    # positive budget, e.g. 500
    hedge_expiration_date: Optional[date] = None  # user-specified expiration for hedge
    hedge_iv: Optional[float] = None              # user-specified IV for hedge pricing (decimal)


class HedgeSuggestion(BaseModel):
    """Suggested hedge position."""
    option_type: str
    strike_price: float
    expiration_date: str
    quantity: float
    estimated_premium: float
    total_premium_cost: float
    original_max_loss: float
    hedged_max_loss: float
    reduction: float


def _evaluate_hedge(position_dicts, hedge_type, strike, hedge_qty, hedge_exp,
                    request, original_max_loss, hedge_iv):
    """Evaluate a single hedge candidate and return its metrics."""
    T = calculate_time_to_expiration(hedge_exp, request.target_date)
    # Use hedge-specific IV for pricing the hedge option
    pricing_iv = hedge_iv if hedge_iv else request.implied_volatility
    premium = black_scholes_price(
        S=request.current_price, K=strike, T=T,
        r=settings.RISK_FREE_RATE, sigma=pricing_iv,
        option_type=hedge_type,
    )
    total_cost = premium * hedge_qty

    hedge_position = {
        "id": -1,
        "underlying_symbol": request.underlying_symbol,
        "option_type": hedge_type,
        "strike_price": strike,
        "expiration_date": hedge_exp,
        "quantity": hedge_qty,
        "entry_price": premium,
    }

    combined = position_dicts + [hedge_position]
    combined_points = generate_price_points(request.current_price, 1.5, positions=combined)
    combined_pnl = calculate_portfolio_pnl(
        combined, combined_points, request.implied_volatility,
        settings.RISK_FREE_RATE, request.target_date,
    )
    hedged_info = find_max_loss(combined_pnl)
    hedged_max_loss = hedged_info["amount"]
    reduction = hedged_max_loss - original_max_loss

    return {
        "strike": strike,
        "premium": premium,
        "total_premium_cost": total_cost,
        "hedged_max_loss": hedged_max_loss,
        "reduction": reduction,
    }


@router.post("/suggest-hedge", response_model=HedgeSuggestion)
def suggest_hedge(request: HedgeRequest, db: Session = Depends(get_db)):
    """
    Suggest a hedge position to reduce max loss.

    Priority: max_hedge_cost > target_max_loss
    - Never exceed the budget (max_hedge_cost)
    - Within budget, minimize max loss (try to reach target_max_loss)
    - If neither is set, pick the best overall reduction
    """
    repo = PositionRepository(db)
    positions = repo.get_all(
        underlying_symbol=request.underlying_symbol,
        portfolio_id=request.portfolio_id,
    )

    if not positions:
        raise HTTPException(status_code=400, detail="没有找到持仓数据")

    position_dicts = [
        {
            "id": p.id,
            "underlying_symbol": p.underlying_symbol,
            "option_type": p.option_type,
            "strike_price": p.strike_price,
            "expiration_date": p.expiration_date,
            "quantity": p.quantity,
            "entry_price": p.entry_price,
        }
        for p in positions
    ]

    sell_put_qty = sum(
        abs(p["quantity"]) for p in position_dicts
        if p["option_type"] == "PUT" and p["quantity"] < 0
    )
    sell_call_qty = sum(
        abs(p["quantity"]) for p in position_dicts
        if p["option_type"] == "CALL" and p["quantity"] < 0
    )

    is_put_dominated = sell_put_qty >= sell_call_qty
    hedge_type = "PUT" if is_put_dominated else "CALL"
    hedge_qty = sell_put_qty if is_put_dominated else sell_call_qty

    if hedge_qty == 0:
        raise HTTPException(status_code=400, detail="没有需要对冲的卖出仓位")

    nearest_exp = min(p["expiration_date"] for p in position_dicts)

    # Use user-specified hedge expiration, or fall back to nearest existing
    hedge_exp = request.hedge_expiration_date if request.hedge_expiration_date else nearest_exp
    hedge_iv = request.hedge_iv  # None means use portfolio IV

    # Calculate original max loss
    price_points = generate_price_points(request.current_price, 1.5, positions=position_dicts)
    original_pnl = calculate_portfolio_pnl(
        position_dicts, price_points, request.implied_volatility,
        settings.RISK_FREE_RATE, request.target_date,
    )
    original_max_loss = find_max_loss(original_pnl)["amount"]

    # Generate a dense set of strike candidates
    if is_put_dominated:
        # Strikes from 1% to 99% of current price
        strike_candidates = [
            round(request.current_price * (p / 1000), 2)
            for p in range(10, 1000, 5)
        ]
    else:
        strike_candidates = [
            round(request.current_price * (p / 1000), 2)
            for p in range(1005, 3000, 5)
        ]

    # Evaluate all candidates
    evaluations = []
    for strike in strike_candidates:
        ev = _evaluate_hedge(
            position_dicts, hedge_type, strike, hedge_qty,
            hedge_exp, request, original_max_loss, hedge_iv
        )
        evaluations.append(ev)

    has_budget = request.max_hedge_cost is not None
    has_target = request.target_max_loss is not None
    budget = request.max_hedge_cost if has_budget else float('inf')
    target = request.target_max_loss if has_target else None

    # Filter by budget first (primary constraint)
    within_budget = [e for e in evaluations if e["total_premium_cost"] <= budget]

    if not within_budget:
        # Nothing fits the budget — pick the cheapest option overall
        within_budget = evaluations
        within_budget.sort(key=lambda e: e["total_premium_cost"])
        within_budget = [within_budget[0]]

    if has_target and target is not None:
        # Among budget-feasible options, find those meeting the target
        meets_target = [e for e in within_budget if e["hedged_max_loss"] >= target]
        if meets_target:
            # Among those meeting target, pick the cheapest
            best = min(meets_target, key=lambda e: e["total_premium_cost"])
        else:
            # Can't meet target within budget — pick the one with best max loss reduction
            best = max(within_budget, key=lambda e: e["hedged_max_loss"])
    else:
        # No target specified — within budget, pick best max loss reduction
        best = max(within_budget, key=lambda e: e["hedged_max_loss"])

    return HedgeSuggestion(
        option_type=hedge_type,
        strike_price=best["strike"],
        expiration_date=str(hedge_exp),
        quantity=hedge_qty,
        estimated_premium=round(best["premium"], 8),
        total_premium_cost=round(best["total_premium_cost"], 8),
        original_max_loss=round(original_max_loss, 2),
        hedged_max_loss=round(best["hedged_max_loss"], 2),
        reduction=round(best["reduction"], 2),
    )
