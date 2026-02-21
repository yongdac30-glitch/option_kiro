"""Options strategy backtesting API."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime, date, timedelta, timezone
import httpx
import math
import asyncio

from app.services.pricing import black_scholes_price, calculate_time_to_expiration
from app.core.config import create_http_client

router = APIRouter(prefix="/api/backtest", tags=["backtest"])

OKX_BASE = "https://www.okx.com"
TIMEOUT = 15.0


class StrategyLeg(BaseModel):
    """A single leg of the strategy."""
    option_type: str = Field(..., pattern="^(PUT|CALL)$")
    strike_offset_pct: float = Field(..., description="Strike offset from spot, e.g. -0.20 means 20% below")
    quantity: float = Field(..., description="Positive=buy, negative=sell")
    expiry_months: int = Field(default=1, description="Months to expiration")
    iv: float = Field(default=0.6, description="Assumed IV for this leg (decimal)")


class BacktestRequest(BaseModel):
    """Backtest request parameters."""
    underlying: str = Field(default="BTC-USD")
    start_date: date
    end_date: date
    risk_free_rate: float = Field(default=0.05)
    roll_day: int = Field(default=1, description="Day of month to roll positions (1-28)")
    close_days_before_expiry: int = Field(default=1, description="Close ITM positions N days before expiry")
    initial_capital: float = Field(default=10000.0)
    legs: List[StrategyLeg]
    contract_multiplier: float = Field(default=1.0, description="Contract multiplier (e.g. 0.01 for OKX BTC options)")


class TradeRecord(BaseModel):
    open_date: str
    close_date: str
    option_type: str
    strike: float
    quantity: float
    open_price: float
    close_price: float
    pnl: float
    close_reason: str


class BacktestResult(BaseModel):
    equity_curve: List[dict]
    trades: List[TradeRecord]
    summary: dict


async def fetch_daily_prices(underlying: str, start_date: date, end_date: date) -> List[dict]:
    """Fetch daily index candles from OKX."""
    base_ccy = underlying.split("-")[0]
    inst_id = f"{base_ccy}-USD"

    all_candles = []
    # OKX returns max 100 candles per request, paginate with 'after'
    end_ts = int(datetime.combine(end_date + timedelta(days=1), datetime.min.time(),
                                   tzinfo=timezone.utc).timestamp() * 1000)
    start_ts = int(datetime.combine(start_date, datetime.min.time(),
                                     tzinfo=timezone.utc).timestamp() * 1000)

    current_after = end_ts
    async with create_http_client() as client:
        for _ in range(50):  # max 50 pages = 5000 days
            url = f"{OKX_BASE}/api/v5/market/history-index-candles"
            params = {"instId": inst_id, "bar": "1D", "limit": "100", "after": str(current_after)}
            resp = await client.get(url, params=params, headers={"User-Agent": "OptionsBacktest/1.0"})
            if resp.status_code == 429:
                # Rate limited, wait and retry
                await asyncio.sleep(1.0)
                resp = await client.get(url, params=params, headers={"User-Agent": "OptionsBacktest/1.0"})
            resp.raise_for_status()
            data = resp.json()
            if data.get("code") != "0" or not data.get("data"):
                break
            for c in data["data"]:
                ts = int(c[0])
                if ts < start_ts:
                    continue
                dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).date()
                all_candles.append({
                    "date": dt,
                    "open": float(c[1]),
                    "high": float(c[2]),
                    "low": float(c[3]),
                    "close": float(c[4]),
                })
            oldest_ts = int(data["data"][-1][0])
            if oldest_ts <= start_ts:
                break
            current_after = oldest_ts
            # Rate limit: wait between requests
            await asyncio.sleep(0.25)

    all_candles.sort(key=lambda x: x["date"])
    return all_candles


def find_expiry_date(open_date: date, months: int) -> date:
    """Find the expiry date N months from open_date (last Friday of that month, or just +N months)."""
    # Simple: add N months
    year = open_date.year
    month = open_date.month + months
    while month > 12:
        month -= 12
        year += 1
    # Use same day or last day of month
    import calendar
    last_day = calendar.monthrange(year, month)[1]
    day = min(open_date.day, last_day)
    return date(year, month, day)


def run_backtest(
    prices: List[dict],
    legs: List[StrategyLeg],
    r: float,
    roll_day: int,
    close_days_before: int,
    initial_capital: float,
    contract_mult: float,
) -> BacktestResult:
    """Run the backtest simulation."""
    if not prices:
        raise HTTPException(status_code=400, detail="No price data available for the specified period")

    equity = initial_capital
    equity_curve = []
    trades = []
    open_positions = []  # list of dicts: {leg, strike, expiry, quantity, open_price, open_date, open_spot}

    price_map = {p["date"]: p["close"] for p in prices}
    dates = sorted(price_map.keys())

    # Per-leg roll tracking: leg index -> (year, month) of last roll
    leg_last_roll = {}
    # Track which months have been "visited" for roll_day check
    last_checked_month = None

    for today in dates:
        spot = price_map[today]

        # 1) Check for positions to close (expiry approaching or at expiry)
        still_open = []
        for pos in open_positions:
            days_to_exp = (pos["expiry"] - today).days
            should_close = False
            close_reason = ""

            if days_to_exp <= close_days_before:
                # Check if ITM
                if pos["option_type"] == "PUT" and spot < pos["strike"]:
                    should_close = True
                    close_reason = "到期前平仓(价内)"
                elif pos["option_type"] == "CALL" and spot > pos["strike"]:
                    should_close = True
                    close_reason = "到期前平仓(价内)"
                elif days_to_exp <= 0:
                    should_close = True
                    close_reason = "到期作废(价外)"
                else:
                    # OTM and within close window - let it expire
                    if days_to_exp <= 0:
                        should_close = True
                        close_reason = "到期作废(价外)"

            if should_close:
                # Calculate close price
                if close_reason.startswith("到期作废"):
                    close_price = 0.0
                else:
                    T_close = max(calculate_time_to_expiration(pos["expiry"], today), 0.0001)
                    close_price = black_scholes_price(spot, pos["strike"], T_close, r, pos["iv"], pos["option_type"])

                # PnL = (close_price - open_price) * quantity * contract_mult
                pnl = (close_price - pos["open_price"]) * pos["quantity"] * contract_mult
                equity += pnl

                trades.append(TradeRecord(
                    open_date=pos["open_date"].isoformat(),
                    close_date=today.isoformat(),
                    option_type=pos["option_type"],
                    strike=round(pos["strike"], 2),
                    quantity=pos["quantity"],
                    open_price=round(pos["open_price"], 4),
                    close_price=round(close_price, 4),
                    pnl=round(pnl, 2),
                    close_reason=close_reason,
                ))
            else:
                still_open.append(pos)

        open_positions = still_open

        # 2) Check if we should open new positions (per-leg rolling)
        current_month_key = (today.year, today.month)
        is_roll_day = today.day >= roll_day and last_checked_month != current_month_key
        if is_roll_day:
            last_checked_month = current_month_key

            for leg_idx, leg in enumerate(legs):
                # Check if enough months have passed since this leg's last roll
                last_roll = leg_last_roll.get(leg_idx)
                if last_roll is not None:
                    # Calculate months elapsed since last roll
                    months_elapsed = (today.year - last_roll[0]) * 12 + (today.month - last_roll[1])
                    if months_elapsed < leg.expiry_months:
                        continue  # Not yet time to roll this leg

                leg_last_roll[leg_idx] = current_month_key

                strike = round(spot * (1 + leg.strike_offset_pct), 2)
                expiry = find_expiry_date(today, leg.expiry_months)
                T_open = calculate_time_to_expiration(expiry, today)

                if T_open <= 0.0001:
                    continue

                open_price = black_scholes_price(spot, strike, T_open, r, leg.iv, leg.option_type)

                # For selling: we receive premium (negative quantity * positive price = negative cost)
                premium_flow = -open_price * leg.quantity * contract_mult
                equity += premium_flow  # Receive premium when selling, pay when buying

                open_positions.append({
                    "option_type": leg.option_type,
                    "strike": strike,
                    "expiry": expiry,
                    "quantity": leg.quantity,
                    "open_price": open_price,
                    "open_date": today,
                    "open_spot": spot,
                    "iv": leg.iv,
                })

        # 3) Mark-to-market: calculate unrealized PnL for equity curve
        unrealized = 0.0
        for pos in open_positions:
            T_mtm = max(calculate_time_to_expiration(pos["expiry"], today), 0.0001)
            current_price = black_scholes_price(spot, pos["strike"], T_mtm, r, pos["iv"], pos["option_type"])
            unrealized += (current_price - pos["open_price"]) * pos["quantity"] * contract_mult

        equity_curve.append({
            "date": today.isoformat(),
            "equity": round(equity + unrealized, 2),
            "spot": spot,
            "open_positions": len(open_positions),
        })

    # Close any remaining positions at end
    if open_positions and dates:
        last_date = dates[-1]
        last_spot = price_map[last_date]
        for pos in open_positions:
            T_final = max(calculate_time_to_expiration(pos["expiry"], last_date), 0.0001)
            close_price = black_scholes_price(last_spot, pos["strike"], T_final, r, pos["iv"], pos["option_type"])
            pnl = (close_price - pos["open_price"]) * pos["quantity"] * contract_mult
            equity += pnl
            trades.append(TradeRecord(
                open_date=pos["open_date"].isoformat(),
                close_date=last_date.isoformat(),
                option_type=pos["option_type"],
                strike=round(pos["strike"], 2),
                quantity=pos["quantity"],
                open_price=round(pos["open_price"], 4),
                close_price=round(close_price, 4),
                pnl=round(pnl, 2),
                close_reason="回测结束平仓",
            ))

    # Summary
    total_pnl = equity - initial_capital
    total_trades = len(trades)
    winning = [t for t in trades if t.pnl > 0]
    losing = [t for t in trades if t.pnl < 0]
    max_equity = max((e["equity"] for e in equity_curve), default=initial_capital)
    min_equity = min((e["equity"] for e in equity_curve), default=initial_capital)
    max_drawdown = 0.0
    peak = initial_capital
    for e in equity_curve:
        if e["equity"] > peak:
            peak = e["equity"]
        dd = (peak - e["equity"]) / peak if peak > 0 else 0
        if dd > max_drawdown:
            max_drawdown = dd

    summary = {
        "initial_capital": initial_capital,
        "final_equity": round(equity, 2),
        "total_pnl": round(total_pnl, 2),
        "total_return_pct": round(total_pnl / initial_capital * 100, 2),
        "max_equity": round(max_equity, 2),
        "min_equity": round(min_equity, 2),
        "max_drawdown_pct": round(max_drawdown * 100, 2),
        "total_trades": total_trades,
        "winning_trades": len(winning),
        "losing_trades": len(losing),
        "win_rate_pct": round(len(winning) / total_trades * 100, 2) if total_trades > 0 else 0,
        "avg_pnl": round(sum(t.pnl for t in trades) / total_trades, 2) if total_trades > 0 else 0,
    }

    return BacktestResult(equity_curve=equity_curve, trades=[t.model_dump() for t in trades], summary=summary)


@router.post("/run", response_model=BacktestResult)
async def run_backtest_api(request: BacktestRequest):
    """Run options strategy backtest."""
    if request.start_date >= request.end_date:
        raise HTTPException(status_code=400, detail="开始日期必须早于结束日期")

    if not request.legs:
        raise HTTPException(status_code=400, detail="至少需要一个策略腿")

    # Fetch historical prices — try DB cache first, then API
    try:
        from app.api.data_center import get_okx_cached_prices
        cached = get_okx_cached_prices(request.underlying, request.start_date, request.end_date)
        if cached and len(cached) >= (request.end_date - request.start_date).days * 0.8:
            prices = [{"date": d, "close": p} for d, p in sorted(cached.items())]
            print(f"[Backtest] Using {len(prices)} cached OKX prices")
        else:
            prices = await fetch_daily_prices(request.underlying, request.start_date, request.end_date)
            # Save to OKX cache for future use
            from app.api.data_center import _fetch_okx_prices_with_cache
            await _fetch_okx_prices_with_cache(request.underlying, request.start_date, request.end_date)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取历史数据失败: {str(e)}")

    if not prices:
        raise HTTPException(status_code=400, detail="无法获取指定时间段的历史价格数据")

    try:
        result = run_backtest(
            prices=prices,
            legs=request.legs,
            r=request.risk_free_rate,
            roll_day=request.roll_day,
            close_days_before=request.close_days_before_expiry,
            initial_capital=request.initial_capital,
            contract_mult=request.contract_multiplier,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"回测计算失败: {str(e)}")

    return result
