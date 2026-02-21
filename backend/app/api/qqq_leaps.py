"""QQQ LEAPS 策略回测 API

基于 "Options with Davis" 的 QQQ LEAPS 策略:
- 买入深度实值(ITM) CALL LEAPS，Delta ≈ 0.90
- 到期前 60-90 天滚仓(Roll)到下一个 1年+ 到期日
- 作为 QQQ 的杠杆替代持仓策略
- 同时对比 Buy & Hold QQQ 的表现

数据来源: yfinance (QQQ / XLK / SPY 历史价格)
期权定价: Black-Scholes 模型
"""
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from typing import List, Optional, Dict
from datetime import datetime, date, timedelta
import asyncio
import json
import math
import time

import yfinance as yf

from app.services.pricing import black_scholes_price, calculate_time_to_expiration

router = APIRouter(prefix="/api/qqq-leaps", tags=["qqq-leaps"])

RISK_FREE_RATE = 0.045
CONTRACT_MULT = 100

# ── Retry helper ─────────────────────────────────────────────────────
MAX_RETRIES = 3
RETRY_DELAYS = [5, 15, 30]


def _retry(fn, *args):
    for attempt in range(MAX_RETRIES + 1):
        try:
            return fn(*args)
        except Exception as e:
            msg = str(e).lower()
            retriable = any(k in msg for k in ("rate", "429", "timeout", "connection", "reset"))
            if retriable and attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)])
                continue
            raise


def _get_prices(ticker: str, start: date, end: date) -> Dict[date, float]:
    def _fetch():
        t = yf.Ticker(ticker)
        df = t.history(start=start.isoformat(),
                       end=(end + timedelta(days=1)).isoformat(), interval="1d")
        if df.empty:
            raise ValueError(f"No data for {ticker}")
        return {idx.date(): float(row["Close"]) for idx, row in df.iterrows()}
    return _retry(_fetch)


# ── US option expiry generation ──────────────────────────────────────

def _generate_expiries(today: date) -> List[date]:
    """Generate plausible US option expiry dates (3rd Friday of month)."""
    import calendar
    results = []
    for year_offset in range(0, 4):
        year = today.year + year_offset
        for month in range(1, 13):
            cal = calendar.monthcalendar(year, month)
            fridays = [w[calendar.FRIDAY] for w in cal if w[calendar.FRIDAY] != 0]
            if len(fridays) >= 3:
                exp = date(year, month, fridays[2])
                if exp > today:
                    results.append(exp)
    results.sort()
    return results


def _find_expiry(today: date, min_days: int = 365) -> Optional[date]:
    """Find nearest expiry at least min_days away."""
    target = today + timedelta(days=min_days)
    for exp in _generate_expiries(today):
        if exp >= target:
            return exp
    return None


# ── Strike helpers ───────────────────────────────────────────────────

def _strike_step(price: float) -> float:
    if price < 25: return 2.5
    if price < 200: return 5.0
    if price < 500: return 10.0
    return 25.0


def _nearest_strike(price: float, step: float) -> float:
    return round(round(price / step) * step, 2)


def _find_deep_itm_strike(spot: float, target_delta: float = 0.90) -> float:
    """Find a deep ITM strike for target delta ~0.90.
    Approximation: for delta=0.90, strike ≈ spot * (1 - offset).
    Typical offset for 1yr LEAPS at 0.90 delta is ~15-25% ITM."""
    step = _strike_step(spot)
    # Start from ATM, go deeper ITM until we find ~90 delta
    # For a rough BS delta approximation, we scan downward
    atm = _nearest_strike(spot, step)
    best_strike = atm
    best_delta_diff = 999.0

    for i in range(30):
        strike = atm - i * step
        if strike <= 0:
            break
        # Rough moneyness check: for deep ITM, delta approaches 1
        moneyness = spot / strike if strike > 0 else 999
        # Approximate delta for 1yr call: use BS
        T = 1.0  # approximate
        try:
            from scipy.stats import norm
            d1 = (math.log(spot / strike) + (RISK_FREE_RATE + 0.5 * 0.25**2) * T) / (0.25 * math.sqrt(T))
            delta = norm.cdf(d1)
        except Exception:
            delta = 0.5 + 0.5 * (moneyness - 1.0) if moneyness > 1 else 0.5

        diff = abs(delta - target_delta)
        if diff < best_delta_diff:
            best_delta_diff = diff
            best_strike = strike

    return best_strike


# ── Schemas ──────────────────────────────────────────────────────────

class QQQLeapsRequest(BaseModel):
    ticker: str = Field(default="QQQ", description="标的: QQQ, XLK, SPY")
    start_date: date
    end_date: date
    initial_capital: float = Field(default=50000)
    target_delta: float = Field(default=0.90, description="目标Delta (0.80-0.95)")
    default_iv: float = Field(default=0.25, description="默认IV")
    roll_dte: int = Field(default=75, description="剩余DTE触发滚仓 (60-90)")
    min_expiry_days: int = Field(default=365, description="最小到期天数")
    num_contracts: int = Field(default=1, description="合约数量")
    compare_tickers: List[str] = Field(default=["QQQ", "SPY", "XLK"],
                                        description="对比标的列表")


# ── Core backtest engine ─────────────────────────────────────────────

async def run_qqq_leaps_backtest(
    price_map: Dict[date, float],
    config: QQQLeapsRequest,
    progress_callback=None,
) -> dict:
    """Run QQQ LEAPS deep ITM CALL strategy backtest.

    Strategy rules (from "Options with Davis"):
    1. Buy deep ITM CALL with delta ≈ target_delta, expiry > 1 year
    2. When DTE drops to roll_dte (60-90), roll to next 1yr+ expiry
    3. Always maintain position — this is a stock replacement strategy
    """
    dates = sorted(price_map.keys())
    if not dates:
        raise HTTPException(status_code=400, detail="No price data")

    iv = config.default_iv
    mult = CONTRACT_MULT
    qty = config.num_contracts

    cash = config.initial_capital
    position = None  # {strike, expiry, quantity, open_price, open_date}
    trades = []
    equity_curve = []
    total_capital_used = config.initial_capital  # track max capital deployed
    total_topups = 0.0  # additional capital needed for rolls

    def _bs(spot, strike, expiry, today):
        T = calculate_time_to_expiration(expiry, today)
        if T <= 0.0001:
            return max(0.0, spot - strike)
        return black_scholes_price(spot, strike, T, RISK_FREE_RATE, iv, "CALL")

    def _bs_delta(spot, strike, expiry, today):
        """Calculate BS delta for a call option."""
        T = calculate_time_to_expiration(expiry, today)
        if T <= 0.0001:
            return 1.0 if spot > strike else 0.0
        try:
            from scipy.stats import norm
            d1 = (math.log(spot / strike) + (RISK_FREE_RATE + 0.5 * iv**2) * T) / (iv * math.sqrt(T))
            return norm.cdf(d1)
        except Exception:
            return 0.9 if spot > strike else 0.5

    def _find_strike_for_delta(spot, expiry, today, target_delta):
        """Find strike that gives approximately target_delta."""
        step = _strike_step(spot)
        atm = _nearest_strike(spot, step)
        best_strike = atm
        best_diff = 999.0

        for i in range(40):
            strike = atm - i * step
            if strike <= 0:
                break
            delta = _bs_delta(spot, strike, expiry, today)
            diff = abs(delta - target_delta)
            if diff < best_diff:
                best_diff = diff
                best_strike = strike
            # Once delta > target significantly, we've gone too deep
            if delta > target_delta + 0.05 and diff > best_diff:
                break

        return best_strike

    total_days = len(dates)

    for day_idx, today in enumerate(dates):
        spot = price_map[today]

        if progress_callback and day_idx % 20 == 0:
            await progress_callback(day_idx, total_days, today)

        # 1) Check if we need to roll
        if position is not None:
            days_left = (position["expiry"] - today).days

            if days_left <= config.roll_dte:
                # ROLL: close current, open new
                close_price = _bs(spot, position["strike"], position["expiry"], today)
                close_proceeds = close_price * position["quantity"] * mult
                pnl = (close_price - position["open_price"]) * position["quantity"] * mult
                cash += close_proceeds

                trades.append({
                    "date": today.isoformat(), "action": "ROLL_CLOSE",
                    "strike": round(position["strike"], 2),
                    "expiry": position["expiry"].isoformat(),
                    "spot": round(spot, 2),
                    "option_price": round(close_price, 2),
                    "quantity": position["quantity"],
                    "cash_flow": round(close_proceeds, 2),
                    "pnl": round(pnl, 2),
                    "delta": round(_bs_delta(spot, position["strike"], position["expiry"], today), 3),
                    "note": f"滚仓平仓(剩余{days_left}天), PnL=${pnl:.2f}",
                })

                # Open new position
                new_expiry = _find_expiry(today, config.min_expiry_days)
                if new_expiry:
                    new_strike = _find_strike_for_delta(spot, new_expiry, today, config.target_delta)
                    new_price = _bs(spot, new_strike, new_expiry, today)
                    new_cost = new_price * qty * mult

                    # Check if we need capital top-up
                    if new_cost > cash:
                        topup = new_cost - cash
                        total_topups += topup
                        cash += topup
                        total_capital_used += topup

                    cash -= new_cost
                    new_delta = _bs_delta(spot, new_strike, new_expiry, today)
                    position = {
                        "strike": new_strike, "expiry": new_expiry,
                        "quantity": qty, "open_price": new_price,
                        "open_date": today,
                    }
                    trades.append({
                        "date": today.isoformat(), "action": "ROLL_OPEN",
                        "strike": round(new_strike, 2),
                        "expiry": new_expiry.isoformat(),
                        "spot": round(spot, 2),
                        "option_price": round(new_price, 2),
                        "quantity": qty,
                        "cash_flow": round(-new_cost, 2),
                        "pnl": 0,
                        "delta": round(new_delta, 3),
                        "note": f"滚仓开仓 K={new_strike}, 到期{new_expiry}, Δ={new_delta:.3f}",
                    })

        # 2) Initial open
        if position is None and cash > 0:
            expiry = _find_expiry(today, config.min_expiry_days)
            if expiry:
                strike = _find_strike_for_delta(spot, expiry, today, config.target_delta)
                price = _bs(spot, strike, expiry, today)
                cost = price * qty * mult

                if cost > cash:
                    # Need more capital for initial position
                    topup = cost - cash
                    total_topups += topup
                    cash += topup
                    total_capital_used += topup

                if cost > 0:
                    cash -= cost
                    delta = _bs_delta(spot, strike, expiry, today)
                    position = {
                        "strike": strike, "expiry": expiry,
                        "quantity": qty, "open_price": price,
                        "open_date": today,
                    }
                    trades.append({
                        "date": today.isoformat(), "action": "OPEN",
                        "strike": round(strike, 2),
                        "expiry": expiry.isoformat(),
                        "spot": round(spot, 2),
                        "option_price": round(price, 2),
                        "quantity": qty,
                        "cash_flow": round(-cost, 2),
                        "pnl": 0,
                        "delta": round(delta, 3),
                        "note": f"初始开仓 K={strike}, 到期{expiry}, Δ={delta:.3f}",
                    })

        # 3) Mark-to-market
        holdings = 0.0
        cur_delta = 0.0
        if position is not None:
            mtm = _bs(spot, position["strike"], position["expiry"], today)
            holdings = mtm * position["quantity"] * mult
            cur_delta = _bs_delta(spot, position["strike"], position["expiry"], today)

        equity_curve.append({
            "date": today.isoformat(),
            "equity": round(cash + holdings, 2),
            "spot": round(spot, 2),
            "cash": round(cash, 2),
            "holdings": round(holdings, 2),
            "delta": round(cur_delta, 3),
            "has_position": position is not None,
            "dte": (position["expiry"] - today).days if position else 0,
        })

    # Close remaining
    if position is not None and dates:
        last = dates[-1]
        last_spot = price_map[last]
        cp = _bs(last_spot, position["strike"], position["expiry"], last)
        proceeds = cp * position["quantity"] * mult
        pnl = (cp - position["open_price"]) * position["quantity"] * mult
        cash += proceeds
        trades.append({
            "date": last.isoformat(), "action": "CLOSE",
            "strike": round(position["strike"], 2),
            "expiry": position["expiry"].isoformat(),
            "spot": round(last_spot, 2),
            "option_price": round(cp, 2),
            "quantity": position["quantity"],
            "cash_flow": round(proceeds, 2),
            "pnl": round(pnl, 2),
            "delta": 0,
            "note": "回测结束平仓",
        })
        if equity_curve:
            equity_curve[-1]["equity"] = round(cash, 2)
            equity_curve[-1]["holdings"] = 0.0

    # Summary stats
    final_equity = cash
    total_pnl = final_equity - total_capital_used
    days_total = (dates[-1] - dates[0]).days if len(dates) > 1 else 1
    years = max(days_total / 365.25, 0.01)

    # Max drawdown
    max_dd = 0.0
    peak = config.initial_capital
    for e in equity_curve:
        if e["equity"] > peak:
            peak = e["equity"]
        dd = (peak - e["equity"]) / peak if peak > 0 else 0
        max_dd = max(max_dd, dd)

    # Annualized return
    ann_ret = ((final_equity / total_capital_used) ** (1 / years) - 1) * 100 if final_equity > 0 else -100.0

    # Return on used capital
    return_on_capital = (final_equity - total_capital_used) / total_capital_used * 100

    # Sharpe ratio
    strategy_sharpe = 0.0
    if len(equity_curve) >= 2:
        rets = []
        for i in range(1, len(equity_curve)):
            prev = equity_curve[i - 1]["equity"]
            cur = equity_curve[i]["equity"]
            if prev > 0:
                rets.append(cur / prev - 1.0)
        if len(rets) >= 2:
            mean_r = sum(rets) / len(rets)
            var_r = sum((r - mean_r) ** 2 for r in rets) / (len(rets) - 1)
            std_r = math.sqrt(var_r) if var_r > 0 else 0.0
            if std_r > 0:
                strategy_sharpe = (mean_r / std_r) * math.sqrt(252)

    # Buy & hold comparison
    bh_start = equity_curve[0]["spot"] if equity_curve else 1
    bh_end = equity_curve[-1]["spot"] if equity_curve else 1
    bh_return = (bh_end / bh_start - 1) * 100 if bh_start > 0 else 0
    bh_ann = ((bh_end / bh_start) ** (1 / years) - 1) * 100 if bh_start > 0 else 0

    # Buy & hold max drawdown
    bh_max_dd = 0.0
    bh_peak = bh_start
    for e in equity_curve:
        if e["spot"] > bh_peak:
            bh_peak = e["spot"]
        dd = (bh_peak - e["spot"]) / bh_peak if bh_peak > 0 else 0
        bh_max_dd = max(bh_max_dd, dd)

    roll_count = sum(1 for t in trades if t["action"] == "ROLL_OPEN")

    summary = {
        "ticker": config.ticker,
        "initial_capital": config.initial_capital,
        "total_capital_used": round(total_capital_used, 2),
        "total_topups": round(total_topups, 2),
        "final_equity": round(final_equity, 2),
        "total_pnl": round(total_pnl, 2),
        "return_on_capital_pct": round(return_on_capital, 2),
        "annualized_return_pct": round(ann_ret, 2),
        "max_drawdown_pct": round(max_dd * 100, 2),
        "sharpe_ratio": round(strategy_sharpe, 3),
        "roll_count": roll_count,
        "total_trades": len(trades),
        "backtest_days": days_total,
        "default_iv": iv,
        "target_delta": config.target_delta,
        "roll_dte": config.roll_dte,
        # Buy & hold comparison
        "bh_return_pct": round(bh_return, 2),
        "bh_annualized_pct": round(bh_ann, 2),
        "bh_max_drawdown_pct": round(bh_max_dd * 100, 2),
        # Leverage ratio
        "leverage_ratio": round(return_on_capital / bh_return, 2) if bh_return != 0 else 0,
    }

    return {
        "equity_curve": equity_curve,
        "trades": trades,
        "summary": summary,
    }


# ── API Endpoints ────────────────────────────────────────────────────

@router.post("/backtest-stream")
async def qqq_leaps_stream(req: QQQLeapsRequest):
    """Run QQQ LEAPS backtest with SSE progress streaming."""
    if req.start_date >= req.end_date:
        raise HTTPException(status_code=400, detail="开始日期必须早于结束日期")

    async def event_generator():
        yield f"data: {json.dumps({'type': 'progress', 'pct': 0, 'status': f'正在获取 {req.ticker} 历史价格...'})}\n\n"

        try:
            price_map = await asyncio.get_event_loop().run_in_executor(
                None, _get_prices, req.ticker, req.start_date, req.end_date)
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': f'获取{req.ticker}价格失败: {str(e)}'})}\n\n"
            return

        if not price_map:
            yield f"data: {json.dumps({'type': 'error', 'message': '无价格数据'})}\n\n"
            return

        total = len(price_map)
        yield f"data: {json.dumps({'type': 'progress', 'pct': 5, 'status': f'获取到 {total} 天价格数据, 开始LEAPS回测...'})}\n\n"

        progress_queue = asyncio.Queue()

        async def progress_cb(idx, total_days, current_date, status="计算中"):
            pct = 5 + int(85 * idx / total_days)
            await progress_queue.put({
                'type': 'progress', 'pct': pct,
                'status': f'{current_date.isoformat()} ({idx}/{total_days})',
            })

        async def run_bt():
            return await run_qqq_leaps_backtest(price_map, req, progress_cb)

        task = asyncio.create_task(run_bt())

        while not task.done():
            try:
                msg = await asyncio.wait_for(progress_queue.get(), timeout=0.5)
                yield f"data: {json.dumps(msg)}\n\n"
            except asyncio.TimeoutError:
                continue

        while not progress_queue.empty():
            msg = await progress_queue.get()
            yield f"data: {json.dumps(msg)}\n\n"

        try:
            result = task.result()
        except Exception as e:
            import traceback
            traceback.print_exc()
            yield f"data: {json.dumps({'type': 'error', 'message': f'回测失败: {str(e)}'})}\n\n"
            yield "data: {\"type\": \"done\"}\n\n"
            return

        # Fetch comparison tickers
        compare_data = {}
        for ct in req.compare_tickers:
            if ct == req.ticker:
                continue
            yield f"data: {json.dumps({'type': 'progress', 'pct': 92, 'status': f'获取对比标的 {ct} 数据...'})}\n\n"
            try:
                cp = await asyncio.get_event_loop().run_in_executor(
                    None, _get_prices, ct, req.start_date, req.end_date)
                if cp:
                    sorted_dates = sorted(cp.keys())
                    first_price = cp[sorted_dates[0]]
                    compare_data[ct] = {
                        "prices": {d.isoformat(): round(v / first_price * 100, 2) for d, v in cp.items()},
                        "total_return_pct": round((cp[sorted_dates[-1]] / first_price - 1) * 100, 2),
                        "start_price": round(first_price, 2),
                        "end_price": round(cp[sorted_dates[-1]], 2),
                    }
            except Exception:
                pass

        result["compare"] = compare_data

        yield f"data: {json.dumps({'type': 'progress', 'pct': 100, 'status': '回测完成'})}\n\n"
        yield f"data: {json.dumps({'type': 'result', 'data': result})}\n\n"
        yield "data: {\"type\": \"done\"}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.get("/etf-compare")
async def etf_compare(
    tickers: str = "QQQ,SPY,XLK,IWM,DIA",
    years: int = 5,
):
    """Compare ETF performance over N years."""
    ticker_list = [t.strip() for t in tickers.split(",")]
    end = date.today()
    start = end - timedelta(days=years * 365)

    results = {}
    for t in ticker_list:
        try:
            prices = await asyncio.get_event_loop().run_in_executor(
                None, _get_prices, t, start, end)
            if prices:
                sorted_d = sorted(prices.keys())
                first = prices[sorted_d[0]]
                last = prices[sorted_d[-1]]
                total_ret = (last / first - 1) * 100
                ann_ret = ((last / first) ** (1 / years) - 1) * 100

                # Max drawdown
                peak = first
                max_dd = 0.0
                for d in sorted_d:
                    if prices[d] > peak:
                        peak = prices[d]
                    dd = (peak - prices[d]) / peak
                    max_dd = max(max_dd, dd)

                results[t] = {
                    "total_return_pct": round(total_ret, 2),
                    "annualized_pct": round(ann_ret, 2),
                    "max_drawdown_pct": round(max_dd * 100, 2),
                    "start_price": round(first, 2),
                    "end_price": round(last, 2),
                    "data_points": len(prices),
                }
        except Exception as e:
            results[t] = {"error": str(e)}

    return {"years": years, "data": results}
