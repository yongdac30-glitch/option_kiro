"""LEAPS Rolling Strategy Backtest API.

Strategy based on "LEAPS无限续杯" concept:
- Buy deep ITM LEAPS call options on QQQ/SPY (or BTC for crypto)
- Roll Out: When approaching expiry, roll to further expiry
- Roll Up: When underlying rises significantly, roll strike up and extract profit
- Add on Dip: When underlying drops significantly, add more LEAPS at lower strikes
- Goal: Reduce cost basis to zero or negative over time
"""
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Tuple
from datetime import datetime, date, timedelta, timezone
import math
import asyncio
import json
import httpx

from app.services.pricing import black_scholes_price, calculate_time_to_expiration, implied_volatility
from app.core.config import create_http_client
from app.api.deribit import (
    fetch_deribit_index_prices,
    get_option_price_via_smile,
    find_deribit_quarterly_expiries,
    find_nearest_strike,
    get_strike_step,
    build_instrument_name,
    get_cached_iv_smile,
    save_cached_iv_smile,
    fetch_trades_for_instrument,
    _fetch_from_hf_db,
    DERIBIT_BASE,
    RATE_DELAY,
    RISK_FREE_RATE as DERIBIT_R,
)

router = APIRouter(prefix="/api/leaps", tags=["leaps"])

OKX_BASE = "https://www.okx.com"


# ── Schemas ──────────────────────────────────────────────────────────

class LeapsConfig(BaseModel):
    """LEAPS strategy configuration."""
    underlying: str = Field(default="BTC-USD")
    start_date: date
    end_date: date
    initial_capital: float = Field(default=100000, description="初始资金 USD")
    contract_multiplier: float = Field(default=0.01, description="合约乘数")
    risk_free_rate: float = Field(default=0.05)

    # LEAPS parameters
    leaps_delta_target: float = Field(default=0.80, description="目标Delta(深度实值), 0.7-0.9")
    leaps_expiry_months: int = Field(default=12, description="LEAPS到期月数, 通常12-24个月")
    iv: float = Field(default=0.6, description="假设IV")

    # Roll Out trigger
    roll_out_dte: int = Field(default=60, description="剩余天数<=此值时触发Roll Out")

    # Roll Up trigger
    roll_up_pct: float = Field(default=0.20, description="标的上涨超过此比例时Roll Up提取利润")

    # Add on Dip trigger
    add_on_dip_pct: float = Field(default=-0.15, description="标的下跌超过此比例时加仓(负数)")
    max_positions: int = Field(default=5, description="最大同时持仓数")

    # Position sizing
    position_size_pct: float = Field(default=0.20, description="每次开仓占当前权益比例")

    # Cooldown
    cooldown_days: int = Field(default=5, description="操作冷却期(天), 避免同一天/连续天重复操作")

    # Real data mode
    use_real_data: bool = Field(default=False, description="使用Deribit真实IV数据")
    use_hf_data: bool = Field(default=False, description="优先使用高频数据库")


class LeapsTradeRecord(BaseModel):
    date: str
    action: str  # OPEN / ROLL_OUT / ROLL_UP / ADD_DIP / CLOSE
    strike: float
    expiry: str
    spot: float
    option_price: float
    quantity: float
    total_quantity: float  # total quantity across all positions after this trade
    cash_flow: float  # positive = receive, negative = pay
    cost_basis: float  # cumulative cost basis after this trade
    note: str
    data_source: Optional[str] = None  # "iv_smile" / "model" / None (simulated)
    iv_used: Optional[float] = None


class LeapsBacktestResult(BaseModel):
    equity_curve: List[dict]
    trades: List[dict]
    summary: dict
    iv_smiles: Optional[List[dict]] = None


# ── Price fetching ───────────────────────────────────────────────────

async def fetch_daily_prices(underlying: str, start_date: date, end_date: date) -> List[dict]:
    base_ccy = underlying.split("-")[0]
    inst_id = f"{base_ccy}-USD"
    all_candles = []
    end_ts = int(datetime.combine(end_date + timedelta(days=1), datetime.min.time(),
                                   tzinfo=timezone.utc).timestamp() * 1000)
    start_ts = int(datetime.combine(start_date, datetime.min.time(),
                                     tzinfo=timezone.utc).timestamp() * 1000)
    current_after = end_ts
    async with create_http_client() as client:
        for _ in range(50):
            url = f"{OKX_BASE}/api/v5/market/history-index-candles"
            params = {"instId": inst_id, "bar": "1D", "limit": "100", "after": str(current_after)}
            resp = await client.get(url, params=params, headers={"User-Agent": "LeapsBacktest/1.0"})
            if resp.status_code == 429:
                await asyncio.sleep(1.0)
                resp = await client.get(url, params=params, headers={"User-Agent": "LeapsBacktest/1.0"})
            resp.raise_for_status()
            data = resp.json()
            if data.get("code") != "0" or not data.get("data"):
                break
            for c in data["data"]:
                ts = int(c[0])
                if ts < start_ts:
                    continue
                dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).date()
                all_candles.append({"date": dt, "open": float(c[1]), "high": float(c[2]),
                                    "low": float(c[3]), "close": float(c[4])})
            oldest_ts = int(data["data"][-1][0])
            if oldest_ts <= start_ts:
                break
            current_after = oldest_ts
            await asyncio.sleep(0.25)
    all_candles.sort(key=lambda x: x["date"])
    return all_candles


# ── Helper functions ─────────────────────────────────────────────────

def find_expiry_date(from_date: date, months: int) -> date:
    """Calculate expiry date N months from given date."""
    import calendar
    year = from_date.year
    month = from_date.month + months
    while month > 12:
        month -= 12
        year += 1
    last_day = calendar.monthrange(year, month)[1]
    day = min(from_date.day, last_day)
    return date(year, month, day)


def estimate_strike_for_delta(spot: float, delta_target: float, T: float, r: float, sigma: float) -> float:
    """Estimate strike price for a target delta (deep ITM call).
    For a call, delta ≈ N(d1). We want d1 such that N(d1) = delta_target.
    Then solve for K.
    """
    from scipy.stats import norm
    d1_target = norm.ppf(delta_target)
    # d1 = [ln(S/K) + (r + σ²/2)T] / (σ√T)
    # K = S * exp(-(d1 * σ√T - (r + σ²/2)T))
    ln_s_over_k = d1_target * sigma * math.sqrt(T) - (r + 0.5 * sigma ** 2) * T
    K = spot * math.exp(-ln_s_over_k)
    # Round to reasonable precision
    if spot > 1000:
        K = round(K / 100) * 100
    elif spot > 100:
        K = round(K / 10) * 10
    else:
        K = round(K, 2)
    return float(K)


def calc_position_market_value(pos: dict, spot: float, r: float, sigma: float, mult: float, today: date) -> float:
    """Calculate current market value of a single position."""
    T = max(calculate_time_to_expiration(pos["expiry"], today), 0.0001)
    cur_price = black_scholes_price(spot, pos["strike"], T, r, sigma, "CALL")
    return float(cur_price * pos["quantity"] * mult)


# ── Core backtest engine ─────────────────────────────────────────────

def run_leaps_backtest(prices: List[dict], config: LeapsConfig) -> LeapsBacktestResult:
    """Run the LEAPS rolling strategy backtest."""
    if not prices:
        raise HTTPException(status_code=400, detail="No price data")

    price_map = {p["date"]: p["close"] for p in prices}
    dates = sorted(price_map.keys())

    cash = config.initial_capital
    total_invested = 0.0  # total cash spent on opening/rolling
    total_extracted = 0.0  # total cash extracted from roll-ups
    cost_basis = 0.0  # net cost basis = invested - extracted

    positions = []  # {strike, expiry, quantity, open_price, open_spot, open_date}
    trades = []
    equity_curve = []

    r = config.risk_free_rate
    sigma = config.iv
    mult = config.contract_multiplier

    last_action_date = None  # cooldown tracking
    cooldown = config.cooldown_days

    def _record_trade(today, action, strike, expiry, spot, option_price, quantity, cash_flow, note):
        """Helper to record a trade with proper float conversion."""
        nonlocal cost_basis
        cost_basis = total_invested - total_extracted
        total_qty = sum(p["quantity"] for p in positions)
        trades.append(LeapsTradeRecord(
            date=today.isoformat(), action=action,
            strike=float(round(strike, 2)), expiry=expiry.isoformat() if isinstance(expiry, date) else expiry,
            spot=float(round(spot, 2)), option_price=float(round(option_price, 4)),
            quantity=float(round(quantity, 4)),
            total_quantity=float(round(total_qty, 4)),
            cash_flow=float(round(cash_flow, 2)), cost_basis=float(round(cost_basis, 2)),
            note=note,
        ).model_dump())

    def _in_cooldown(today):
        return last_action_date is not None and (today - last_action_date).days < cooldown

    for today in dates:
        spot = price_map[today]
        did_action = False  # only one action type per day (except Roll Out which is mandatory)

        # ── 1) Initial entry (only when no positions at all) ──
        if not positions and cash > 0:
            T = calculate_time_to_expiration(find_expiry_date(today, config.leaps_expiry_months), today)
            strike = estimate_strike_for_delta(spot, config.leaps_delta_target, T, r, sigma)
            expiry = find_expiry_date(today, config.leaps_expiry_months)
            price = black_scholes_price(spot, strike, T, r, sigma, "CALL")

            budget = cash * config.position_size_pct
            qty = budget / (price * mult) if price * mult > 0 else 0
            if qty <= 0:
                equity_curve.append(_make_equity_point(today, cash, spot, positions, cost_basis,
                                                        total_invested, total_extracted, r, sigma, mult))
                continue
            qty = round(qty, 4)
            cost = float(price * qty * mult)
            cash -= cost
            total_invested += cost
            cost_basis = total_invested - total_extracted

            positions.append({
                "strike": strike, "expiry": expiry, "quantity": qty,
                "open_price": float(price), "open_spot": spot, "open_date": today,
            })
            last_action_date = today

            _record_trade(today, "OPEN", strike, expiry, spot, price, qty, -cost,
                          f"初始建仓 Delta≈{config.leaps_delta_target}")

            equity_curve.append(_make_equity_point(today, cash, spot, positions, cost_basis,
                                                    total_invested, total_extracted, r, sigma, mult))
            continue

        if not positions:
            equity_curve.append(_make_equity_point(today, cash, spot, positions, cost_basis,
                                                    total_invested, total_extracted, r, sigma, mult))
            continue

        # ── 2) Roll Out (approaching expiry) — mandatory, ignores cooldown ──
        new_positions = []
        rolled_out_today = False
        for pos in positions:
            dte = (pos["expiry"] - today).days
            if dte <= config.roll_out_dte:
                # Close old position
                T_close = max(calculate_time_to_expiration(pos["expiry"], today), 0.0001)
                close_price = float(black_scholes_price(spot, pos["strike"], T_close, r, sigma, "CALL"))
                proceeds = close_price * pos["quantity"] * mult
                cash += proceeds

                # Open new position with same strike, further expiry
                new_expiry = find_expiry_date(today, config.leaps_expiry_months)
                T_new = calculate_time_to_expiration(new_expiry, today)
                new_price = float(black_scholes_price(spot, pos["strike"], T_new, r, sigma, "CALL"))
                new_cost = new_price * pos["quantity"] * mult

                # Cash protection: if not enough cash, reduce quantity
                available = cash
                if new_cost > available:
                    # Scale down quantity to what we can afford
                    affordable_qty = available / (new_price * mult) if new_price * mult > 0 else 0
                    if affordable_qty <= 0:
                        # Can't afford to roll — force close
                        total_extracted += proceeds
                        cost_basis = total_invested - total_extracted
                        _record_trade(today, "CLOSE", pos["strike"], pos["expiry"], spot,
                                      close_price, pos["quantity"], proceeds,
                                      f"无法续命(资金不足), 强制平仓")
                        rolled_out_today = True
                        continue
                    new_cost = new_price * affordable_qty * mult
                    pos["quantity"] = round(affordable_qty, 4)

                cash -= new_cost

                net_flow = proceeds - new_cost
                total_invested += max(0, -net_flow)
                total_extracted += max(0, net_flow)

                new_positions.append({
                    "strike": pos["strike"], "expiry": new_expiry, "quantity": pos["quantity"],
                    "open_price": new_price, "open_spot": spot, "open_date": today,
                })

                _record_trade(today, "ROLL_OUT", pos["strike"], new_expiry, spot, new_price,
                              pos["quantity"], net_flow, f"续命: DTE={dte}→新到期{new_expiry}")
                rolled_out_today = True
            else:
                new_positions.append(pos)
        positions = new_positions
        if rolled_out_today:
            last_action_date = today
            did_action = True

        # ── 3) Roll Up (per-position: underlying rallied vs position's open_spot) ──
        #    Only roll up positions where spot has risen >= roll_up_pct from that
        #    position's open_spot. Other positions stay untouched.
        if not did_action and not _in_cooldown(today):
            rolled_positions = []
            kept_positions = []
            any_rolled_up = False

            for pos in positions:
                pos_ref = pos["open_spot"]
                if pos_ref > 0 and spot >= pos_ref * (1 + config.roll_up_pct):
                    # This position qualifies for Roll Up
                    T_close = max(calculate_time_to_expiration(pos["expiry"], today), 0.0001)
                    close_price = float(black_scholes_price(spot, pos["strike"], T_close, r, sigma, "CALL"))
                    proceeds = close_price * pos["quantity"] * mult
                    cash += proceeds

                    # Open new at higher strike, keep same expiry if enough time
                    T_remaining = calculate_time_to_expiration(pos["expiry"], today)
                    if T_remaining < 0.25:  # less than ~3 months, also roll out
                        new_expiry = find_expiry_date(today, config.leaps_expiry_months)
                        T_new = calculate_time_to_expiration(new_expiry, today)
                    else:
                        new_expiry = pos["expiry"]
                        T_new = T_remaining

                    new_strike = estimate_strike_for_delta(spot, config.leaps_delta_target, T_new, r, sigma)
                    new_price = float(black_scholes_price(spot, new_strike, T_new, r, sigma, "CALL"))
                    new_cost = new_price * pos["quantity"] * mult

                    # Cash protection for Roll Up
                    if new_cost > cash:
                        affordable_qty = cash / (new_price * mult) if new_price * mult > 0 else 0
                        if affordable_qty <= 0:
                            total_extracted += proceeds
                            _record_trade(today, "CLOSE", pos["strike"], pos["expiry"], spot,
                                          close_price, pos["quantity"], proceeds,
                                          f"Roll Up资金不足, 平仓获利${round(proceeds,2)}")
                            any_rolled_up = True
                            continue
                        new_cost = new_price * affordable_qty * mult
                        pos_qty = round(affordable_qty, 4)
                    else:
                        pos_qty = pos["quantity"]

                    cash -= new_cost

                    net_flow = proceeds - new_cost
                    total_extracted += max(0, net_flow)
                    total_invested += max(0, -net_flow)

                    # New position's open_spot = today's spot (reset for next Roll Up calc)
                    rolled_positions.append({
                        "strike": new_strike, "expiry": new_expiry, "quantity": pos_qty,
                        "open_price": new_price, "open_spot": spot, "open_date": today,
                    })

                    gain_pct = round((spot / pos_ref - 1) * 100, 1)
                    _record_trade(today, "ROLL_UP", new_strike, new_expiry, spot, new_price,
                                  pos_qty, net_flow,
                                  f"提款: K {pos['strike']}→{new_strike}, 涨幅{gain_pct}%, 净收入${round(net_flow,2)}")
                    any_rolled_up = True
                else:
                    kept_positions.append(pos)

            if any_rolled_up:
                positions = kept_positions + rolled_positions
                last_action_date = today
                did_action = True

        # ── 4) Add on Dip — use highest open_spot among positions as reference ──
        if not did_action and not _in_cooldown(today) and positions and cash > 0:
            # Reference = highest open_spot among current positions
            dip_ref = max(pos["open_spot"] for pos in positions)
            if (dip_ref > 0
                    and spot <= dip_ref * (1 + config.add_on_dip_pct)
                    and len(positions) < config.max_positions):
                T = calculate_time_to_expiration(find_expiry_date(today, config.leaps_expiry_months), today)
                strike = estimate_strike_for_delta(spot, config.leaps_delta_target, T, r, sigma)
                expiry = find_expiry_date(today, config.leaps_expiry_months)
                price = float(black_scholes_price(spot, strike, T, r, sigma, "CALL"))

                budget = cash * config.position_size_pct
                qty = budget / (price * mult) if price * mult > 0 else 0
                if qty > 0:
                    qty = round(qty, 4)
                    cost = price * qty * mult
                    if cost > cash:
                        qty = round(cash / (price * mult), 4)
                        cost = price * qty * mult
                    if qty > 0 and cost <= cash:
                        cash -= cost
                        total_invested += cost

                        positions.append({
                            "strike": strike, "expiry": expiry, "quantity": qty,
                            "open_price": price, "open_spot": spot, "open_date": today,
                        })

                        _record_trade(today, "ADD_DIP", strike, expiry, spot, price, qty, -cost,
                                      f"逢跌加仓: 跌幅{round((spot / dip_ref - 1) * 100, 1)}%")

                        last_action_date = today
                        did_action = True

        # ── 5) Mark-to-market ──

        equity_curve.append(_make_equity_point(today, cash, spot, positions, cost_basis,
                                                total_invested, total_extracted, r, sigma, mult))

    # ── Close remaining positions at end ──
    if positions and dates:
        last_date = dates[-1]
        last_spot = price_map[last_date]
        for pos in positions:
            T_final = max(calculate_time_to_expiration(pos["expiry"], last_date), 0.0001)
            close_price = float(black_scholes_price(last_spot, pos["strike"], T_final, r, sigma, "CALL"))
            proceeds = close_price * pos["quantity"] * mult
            cash += proceeds
            total_extracted += proceeds
            _record_trade(last_date, "CLOSE", pos["strike"], pos["expiry"], last_spot,
                          close_price, pos["quantity"], proceeds, "回测结束平仓")

    # ── Summary ──
    final_equity = cash
    total_pnl = final_equity - config.initial_capital
    max_equity = max((e["equity"] for e in equity_curve), default=config.initial_capital)
    min_equity = min((e["equity"] for e in equity_curve), default=config.initial_capital)

    max_drawdown = 0.0
    peak = config.initial_capital
    for e in equity_curve:
        if e["equity"] > peak:
            peak = e["equity"]
        dd = (peak - e["equity"]) / peak if peak > 0 else 0
        if dd > max_drawdown:
            max_drawdown = dd

    roll_outs = sum(1 for t in trades if t.get("action") == "ROLL_OUT")
    roll_ups = sum(1 for t in trades if t.get("action") == "ROLL_UP")
    add_dips = sum(1 for t in trades if t.get("action") == "ADD_DIP")

    # Annualized return
    days = (dates[-1] - dates[0]).days if len(dates) > 1 else 1
    years = max(days / 365.25, 0.01)
    if final_equity > 0 and config.initial_capital > 0:
        annualized_return = ((final_equity / config.initial_capital) ** (1 / years) - 1) * 100
    else:
        # Total loss or negative equity — express as -100% annualized
        annualized_return = -100.0

    summary = {
        "initial_capital": config.initial_capital,
        "final_equity": float(round(final_equity, 2)),
        "total_pnl": float(round(total_pnl, 2)),
        "total_return_pct": float(round(total_pnl / config.initial_capital * 100, 2)),
        "annualized_return_pct": float(round(annualized_return, 2)),
        "max_equity": float(round(max_equity, 2)),
        "min_equity": float(round(min_equity, 2)),
        "max_drawdown_pct": float(round(max_drawdown * 100, 2)),
        "total_invested": float(round(total_invested, 2)),
        "total_extracted": float(round(total_extracted, 2)),
        "final_cost_basis": float(round(total_invested - total_extracted, 2)),
        "cost_basis_negative": bool((total_invested - total_extracted) < 0),
        "total_trades": len(trades),
        "roll_out_count": roll_outs,
        "roll_up_count": roll_ups,
        "add_dip_count": add_dips,
        "backtest_days": days,
    }

    return LeapsBacktestResult(equity_curve=equity_curve, trades=trades, summary=summary)


def _make_equity_point(today, cash, spot, positions, cost_basis,
                       total_invested, total_extracted, r, sigma, mult):
    """Build a single equity curve data point."""
    holdings_value = 0.0
    for pos in positions:
        holdings_value += calc_position_market_value(pos, spot, r, sigma, mult, today)
    return {
        "date": today.isoformat(),
        "equity": float(round(cash + holdings_value, 2)),
        "spot": float(spot),
        "positions": len(positions),
        "cost_basis": float(round(total_invested - total_extracted, 2)),
        "cash": float(round(cash, 2)),
        "total_invested": float(round(total_invested, 2)),
        "total_extracted": float(round(total_extracted, 2)),
    }


# ── API endpoint ─────────────────────────────────────────────────────

@router.post("/backtest", response_model=LeapsBacktestResult)
async def leaps_backtest(config: LeapsConfig):
    """Run LEAPS rolling strategy backtest (simulated IV or real data)."""
    if config.start_date >= config.end_date:
        raise HTTPException(status_code=400, detail="开始日期必须早于结束日期")

    if not config.use_real_data:
        # ── Simulated mode: OKX prices + fixed IV ──
        try:
            prices = await fetch_daily_prices(config.underlying, config.start_date, config.end_date)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"获取历史数据失败: {str(e)}")
        if not prices:
            raise HTTPException(status_code=400, detail="无法获取指定时间段的历史价格数据")
        try:
            result = run_leaps_backtest(prices, config)
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"LEAPS回测计算失败: {str(e)}")
        return result
    else:
        # ── Real data mode: redirect to non-streaming endpoint ──
        raise HTTPException(status_code=400,
                            detail="真实数据模式请使用 /api/leaps/real-backtest-stream 流式接口")


# ── Real data helpers ────────────────────────────────────────────────

def _find_quarterly_expiry(today: date, min_months: int) -> Optional[date]:
    """Find the nearest Deribit quarterly expiry >= min_months away."""
    min_target = today + timedelta(days=min_months * 30)
    expiries = find_deribit_quarterly_expiries(today, max_years_ahead=3)
    for exp in expiries:
        if exp >= min_target:
            return exp
    return expiries[-1] if expiries else None


def _find_roll_out_expiry(today: date, min_months: int) -> Optional[date]:
    """Find the next quarterly expiry for Roll Out (at least min_months away)."""
    return _find_quarterly_expiry(today, min_months)


def _snap_strike_to_grid(strike: float, underlying: str, spot: float) -> float:
    """Snap a strike to the nearest valid Deribit strike grid."""
    step = get_strike_step(underlying, spot)
    return float(find_nearest_strike(strike, step))


# ── Fast single-strike price lookup (avoids full smile scan) ─────────

async def _fetch_single_strike_price(
    client: httpx.AsyncClient,
    underlying: str,
    expiry: date,
    strike: float,
    spot: float,
    option_type: str,
    target_date: date,
    r: float = 0.05,
    use_hf_data: bool = False,
) -> Tuple[float, str, Optional[float]]:
    """Get option price for a single strike, optimized for speed.

    Strategy:
    0. Check HF option tick database (if enabled)
    1. Check DB cache first (instant)
    2. Try chart data for just this one strike (1 API call, ~0.3s)
    3. Try trade data for this strike (1-2 API calls, ~0.6s)
    4. Fall back to BS model with default IV

    Returns (price_usd, data_source, iv_used).
    """
    T = calculate_time_to_expiration(expiry, target_date)
    if T <= 0.0001:
        intrinsic = max(0, spot - strike) if option_type == "CALL" else max(0, strike - spot)
        return float(intrinsic), "intrinsic", None

    # 0) Check HF option tick database first (if enabled)
    if use_hf_data:
        hf_result = _fetch_from_hf_db(underlying, expiry, strike, spot, option_type, target_date)
        if hf_result is not None:
            price_usd, src, iv = hf_result
            return float(price_usd), src, iv

    # 1) Check DB cache for this specific strike
    cached = get_cached_iv_smile(underlying, expiry, option_type, target_date)
    for s, iv, price_usd in cached:
        if abs(s - strike) < 1.0:  # exact match (within $1)
            price = black_scholes_price(spot, strike, T, r, iv, option_type)
            return float(price), "iv_cache", float(iv)

    # If cache has nearby strikes, interpolate
    if len(cached) >= 2:
        import numpy as np
        strikes_arr = np.array([s for s, _, _ in cached if s > 0])
        ivs_arr = np.array([iv for _, iv, _ in cached if iv > 0])
        if len(strikes_arr) >= 2:
            order = np.argsort(strikes_arr)
            iv_interp = float(np.interp(strike, strikes_arr[order], ivs_arr[order]))
            iv_interp = max(0.05, min(iv_interp, 5.0))
            price = black_scholes_price(spot, strike, T, r, iv_interp, option_type)
            return float(price), "iv_cache_interp", float(iv_interp)

    # 2) Try chart data for just this one strike
    instrument = build_instrument_name(underlying, expiry, strike, option_type)
    days_from_now = (date.today() - target_date).days
    window_days = 10 if days_from_now > 365 else (7 if days_from_now > 180 else 5)

    start_ts = int(datetime.combine(
        target_date - timedelta(days=window_days), datetime.min.time(),
        tzinfo=timezone.utc).timestamp() * 1000)
    end_ts = int(datetime.combine(
        target_date + timedelta(days=window_days + 1), datetime.min.time(),
        tzinfo=timezone.utc).timestamp() * 1000)
    target_ts = int(datetime.combine(
        target_date, datetime.min.time(), tzinfo=timezone.utc).timestamp() * 1000)

    try:
        chart_url = f"{DERIBIT_BASE}/public/get_tradingview_chart_data"
        resp = await client.get(chart_url, params={
            "instrument_name": instrument,
            "start_timestamp": start_ts, "end_timestamp": end_ts, "resolution": "1D",
        })
        await asyncio.sleep(RATE_DELAY)

        if resp.status_code == 429:
            await asyncio.sleep(2.0)
            resp = await client.get(chart_url, params={
                "instrument_name": instrument,
                "start_timestamp": start_ts, "end_timestamp": end_ts, "resolution": "1D",
            })
            await asyncio.sleep(RATE_DELAY)

        if resp.status_code == 200:
            data = resp.json()
            result = data.get("result", {})
            if result and "error" not in data:
                ticks = result.get("ticks", [])
                closes = result.get("close", [])
                if ticks and closes:
                    best_idx = min(range(len(ticks)), key=lambda i: abs(ticks[i] - target_ts))
                    price_btc = closes[best_idx]
                    if price_btc and price_btc > 0:
                        price_usd = price_btc * spot
                        # Compute IV and cache it
                        iv = implied_volatility(price_usd, spot, strike, T, r, option_type)
                        if iv and 0.01 < iv < 10.0:
                            save_cached_iv_smile(underlying, expiry, option_type, target_date,
                                                 spot, [(strike, iv, price_usd, instrument)])
                            return float(price_usd), "chart_direct", float(iv)
                        else:
                            return float(price_usd), "chart_direct", None
    except Exception as e:
        print(f"[LEAPS Fast] Chart error for {instrument}: {e}")

    # 3) Try trade data
    try:
        trade = await fetch_trades_for_instrument(client, instrument, target_date, window_days=window_days)
        if trade:
            price_btc = trade.get("price", 0)
            if price_btc and price_btc > 0:
                price_usd = price_btc * spot
                iv = implied_volatility(price_usd, spot, strike, T, r, option_type)
                if iv and 0.01 < iv < 10.0:
                    save_cached_iv_smile(underlying, expiry, option_type, target_date,
                                         spot, [(strike, iv, price_usd, instrument)])
                    return float(price_usd), "trade_direct", float(iv)
                else:
                    return float(price_usd), "trade_direct", None
    except Exception as e:
        print(f"[LEAPS Fast] Trade error for {instrument}: {e}")

    # 4) Fall back to BS model
    default_iv = 0.6
    price = black_scholes_price(spot, strike, T, r, default_iv, option_type)
    print(f"[LEAPS Fast] Fallback model: {instrument}, IV={default_iv}, price={price:.2f}")
    return float(price), "model", float(default_iv)


# ── Real data backtest engine (async, uses Deribit IV smile) ─────────

async def run_real_leaps_backtest(
    price_map: Dict[date, float],
    config: LeapsConfig,
    underlying_symbol: str,
    progress_callback=None,
) -> LeapsBacktestResult:
    """Run LEAPS rolling strategy using Deribit real IV data.

    Optimized: uses fast single-strike lookups (1-2 API calls per strike)
    instead of full IV smile scans (14+ API calls per event).
    """

    dates = sorted(price_map.keys())
    if not dates:
        raise HTTPException(status_code=400, detail="No price data")

    cash = config.initial_capital
    total_invested = 0.0
    total_extracted = 0.0
    cost_basis = 0.0

    positions = []
    trades = []
    equity_curve = []
    iv_smiles = []

    r = config.risk_free_rate
    mult = config.contract_multiplier
    fallback_iv = config.iv

    last_action_date = None
    cooldown = config.cooldown_days
    total_days = len(dates)

    def _record_trade(today, action, strike, expiry, spot, option_price, quantity,
                      cash_flow, note, data_source=None, iv_used=None):
        nonlocal cost_basis
        cost_basis = total_invested - total_extracted
        total_qty = sum(p["quantity"] for p in positions)
        trades.append(LeapsTradeRecord(
            date=today.isoformat(), action=action,
            strike=float(round(strike, 2)),
            expiry=expiry.isoformat() if isinstance(expiry, date) else expiry,
            spot=float(round(spot, 2)), option_price=float(round(option_price, 4)),
            quantity=float(round(quantity, 4)),
            total_quantity=float(round(total_qty, 4)),
            cash_flow=float(round(cash_flow, 2)),
            cost_basis=float(round(cost_basis, 2)),
            note=note,
            data_source=data_source,
            iv_used=float(round(iv_used, 4)) if iv_used else None,
        ).model_dump())

    def _in_cooldown(today):
        return last_action_date is not None and (today - last_action_date).days < cooldown

    async def _progress(day_idx, today, status="计算中"):
        if progress_callback:
            await progress_callback(day_idx, total_days, today, status)

    async with create_http_client() as client:

        async def _get_price(expiry, strike, spot, today):
            """Fast single-strike price lookup."""
            return await _fetch_single_strike_price(
                client, underlying_symbol, expiry, strike, spot, "CALL", today, r,
                use_hf_data=config.use_hf_data)

        for day_idx, today in enumerate(dates):
            spot = price_map[today]
            did_action = False

            # Progress every day
            await _progress(day_idx, today)

            # ── 1) Initial entry ──
            if not positions and cash > 0:
                expiry = _find_quarterly_expiry(today, config.leaps_expiry_months)
                if expiry is None:
                    equity_curve.append(_make_equity_point_real(
                        today, cash, spot, positions, cost_basis,
                        total_invested, total_extracted, r, fallback_iv, mult))
                    continue

                T = calculate_time_to_expiration(expiry, today)
                raw_strike = estimate_strike_for_delta(spot, config.leaps_delta_target, T, r, fallback_iv)
                strike = _snap_strike_to_grid(raw_strike, underlying_symbol, spot)

                await _progress(day_idx, today, "获取IV数据(建仓)")
                price, src, iv_used = await _get_price(expiry, strike, spot, today)

                if price <= 0:
                    equity_curve.append(_make_equity_point_real(
                        today, cash, spot, positions, cost_basis,
                        total_invested, total_extracted, r, fallback_iv, mult))
                    continue

                budget = cash * config.position_size_pct
                qty = budget / (price * mult) if price * mult > 0 else 0
                if qty <= 0:
                    equity_curve.append(_make_equity_point_real(
                        today, cash, spot, positions, cost_basis,
                        total_invested, total_extracted, r, fallback_iv, mult))
                    continue

                qty = round(qty, 4)
                cost_val = float(price * qty * mult)
                cash -= cost_val
                total_invested += cost_val
                cost_basis = total_invested - total_extracted

                positions.append({
                    "strike": strike, "expiry": expiry, "quantity": qty,
                    "open_price": float(price), "open_spot": spot, "open_date": today,
                    "iv_used": iv_used, "data_source": src,
                })
                last_action_date = today

                _record_trade(today, "OPEN", strike, expiry, spot, price, qty, -cost_val,
                              f"初始建仓 Delta≈{config.leaps_delta_target}", src, iv_used)

                equity_curve.append(_make_equity_point_real(
                    today, cash, spot, positions, cost_basis,
                    total_invested, total_extracted, r, fallback_iv, mult))
                continue

            if not positions:
                equity_curve.append(_make_equity_point_real(
                    today, cash, spot, positions, cost_basis,
                    total_invested, total_extracted, r, fallback_iv, mult))
                continue

            # ── 2) Roll Out ──
            new_positions = []
            rolled_out_today = False
            for pos in positions:
                dte = (pos["expiry"] - today).days
                if dte <= config.roll_out_dte:
                    await _progress(day_idx, today, "获取IV数据(Roll Out)")
                    close_price, close_src, close_iv = await _get_price(
                        pos["expiry"], pos["strike"], spot, today)
                    proceeds = close_price * pos["quantity"] * mult
                    cash += proceeds

                    # Open new with same strike, further expiry
                    new_expiry = _find_roll_out_expiry(today, config.leaps_expiry_months)
                    if new_expiry is None:
                        total_extracted += proceeds
                        cost_basis = total_invested - total_extracted
                        _record_trade(today, "CLOSE", pos["strike"], pos["expiry"], spot,
                                      close_price, pos["quantity"], proceeds,
                                      "无法续命(无可用到期日)", close_src, close_iv)
                        rolled_out_today = True
                        continue

                    new_strike = _snap_strike_to_grid(pos["strike"], underlying_symbol, spot)
                    new_price, new_src, new_iv = await _get_price(
                        new_expiry, new_strike, spot, today)
                    new_cost = new_price * pos["quantity"] * mult

                    if new_cost > cash:
                        affordable_qty = cash / (new_price * mult) if new_price * mult > 0 else 0
                        if affordable_qty <= 0:
                            total_extracted += proceeds
                            cost_basis = total_invested - total_extracted
                            _record_trade(today, "CLOSE", pos["strike"], pos["expiry"], spot,
                                          close_price, pos["quantity"], proceeds,
                                          "无法续命(资金不足)", close_src, close_iv)
                            rolled_out_today = True
                            continue
                        new_cost = new_price * affordable_qty * mult
                        pos["quantity"] = round(affordable_qty, 4)

                    cash -= new_cost
                    net_flow = proceeds - new_cost
                    total_invested += max(0, -net_flow)
                    total_extracted += max(0, net_flow)

                    new_positions.append({
                        "strike": new_strike, "expiry": new_expiry, "quantity": pos["quantity"],
                        "open_price": float(new_price), "open_spot": spot, "open_date": today,
                        "iv_used": new_iv, "data_source": new_src,
                    })
                    _record_trade(today, "ROLL_OUT", new_strike, new_expiry, spot, new_price,
                                  pos["quantity"], net_flow,
                                  f"续命: DTE={dte}→{new_expiry}", new_src, new_iv)
                    rolled_out_today = True
                else:
                    new_positions.append(pos)

            positions = new_positions
            if rolled_out_today:
                last_action_date = today
                did_action = True

            # ── 3) Roll Up (per-position) ──
            if not did_action and not _in_cooldown(today):
                rolled_positions = []
                kept_positions = []
                any_rolled_up = False

                for pos in positions:
                    pos_ref = pos["open_spot"]
                    if pos_ref > 0 and spot >= pos_ref * (1 + config.roll_up_pct):
                        await _progress(day_idx, today, "获取IV数据(Roll Up)")
                        close_price, close_src, close_iv = await _get_price(
                            pos["expiry"], pos["strike"], spot, today)
                        proceeds = close_price * pos["quantity"] * mult
                        cash += proceeds

                        T_remaining = calculate_time_to_expiration(pos["expiry"], today)
                        if T_remaining < 0.25:
                            new_expiry = _find_quarterly_expiry(today, config.leaps_expiry_months)
                            if new_expiry is None:
                                new_expiry = pos["expiry"]
                        else:
                            new_expiry = pos["expiry"]

                        T_new = calculate_time_to_expiration(new_expiry, today)
                        raw_strike = estimate_strike_for_delta(spot, config.leaps_delta_target, T_new, r, fallback_iv)
                        new_strike = _snap_strike_to_grid(raw_strike, underlying_symbol, spot)

                        new_price, new_src, new_iv = await _get_price(
                            new_expiry, new_strike, spot, today)
                        new_cost = new_price * pos["quantity"] * mult

                        if new_cost > cash:
                            affordable_qty = cash / (new_price * mult) if new_price * mult > 0 else 0
                            if affordable_qty <= 0:
                                total_extracted += proceeds
                                _record_trade(today, "CLOSE", pos["strike"], pos["expiry"], spot,
                                              close_price, pos["quantity"], proceeds,
                                              f"Roll Up资金不足, 平仓", close_src, close_iv)
                                any_rolled_up = True
                                continue
                            new_cost = new_price * affordable_qty * mult
                            pos_qty = round(affordable_qty, 4)
                        else:
                            pos_qty = pos["quantity"]

                        cash -= new_cost
                        net_flow = proceeds - new_cost
                        total_extracted += max(0, net_flow)
                        total_invested += max(0, -net_flow)

                        rolled_positions.append({
                            "strike": new_strike, "expiry": new_expiry, "quantity": pos_qty,
                            "open_price": float(new_price), "open_spot": spot, "open_date": today,
                            "iv_used": new_iv, "data_source": new_src,
                        })

                        gain_pct = round((spot / pos_ref - 1) * 100, 1)
                        _record_trade(today, "ROLL_UP", new_strike, new_expiry, spot, new_price,
                                      pos_qty, net_flow,
                                      f"提款: K {pos['strike']}→{new_strike}, 涨幅{gain_pct}%, 净收入${round(net_flow,2)}",
                                      new_src, new_iv)

                        any_rolled_up = True
                    else:
                        kept_positions.append(pos)

                if any_rolled_up:
                    positions = kept_positions + rolled_positions
                    last_action_date = today
                    did_action = True

            # ── 4) Add on Dip ──
            if not did_action and not _in_cooldown(today) and positions and cash > 0:
                dip_ref = max(pos["open_spot"] for pos in positions)
                if (dip_ref > 0
                        and spot <= dip_ref * (1 + config.add_on_dip_pct)
                        and len(positions) < config.max_positions):

                    expiry = _find_quarterly_expiry(today, config.leaps_expiry_months)
                    if expiry:
                        T = calculate_time_to_expiration(expiry, today)
                        raw_strike = estimate_strike_for_delta(spot, config.leaps_delta_target, T, r, fallback_iv)
                        strike = _snap_strike_to_grid(raw_strike, underlying_symbol, spot)

                        await _progress(day_idx, today, "获取IV数据(加仓)")
                        price, src, iv_used = await _get_price(expiry, strike, spot, today)

                        if price > 0:
                            budget = cash * config.position_size_pct
                            qty = budget / (price * mult) if price * mult > 0 else 0
                            if qty > 0:
                                qty = round(qty, 4)
                                cost_val = price * qty * mult
                                if cost_val > cash:
                                    qty = round(cash / (price * mult), 4)
                                    cost_val = price * qty * mult
                                if qty > 0 and cost_val <= cash:
                                    cash -= cost_val
                                    total_invested += cost_val

                                    positions.append({
                                        "strike": strike, "expiry": expiry, "quantity": qty,
                                        "open_price": float(price), "open_spot": spot, "open_date": today,
                                        "iv_used": iv_used, "data_source": src,
                                    })
                                    _record_trade(today, "ADD_DIP", strike, expiry, spot, price, qty,
                                                  -cost_val,
                                                  f"逢跌加仓: 跌幅{round((spot / dip_ref - 1) * 100, 1)}%",
                                                  src, iv_used)
                                    last_action_date = today
                                    did_action = True

            # ── 5) Mark-to-market ──
            equity_curve.append(_make_equity_point_real(
                today, cash, spot, positions, cost_basis,
                total_invested, total_extracted, r, fallback_iv, mult))

        # ── Close remaining positions ──
        if positions and dates:
            last_date = dates[-1]
            last_spot = price_map[last_date]
            await _progress(total_days - 1, last_date, "平仓结算")
            for pos in positions:
                close_price, close_src, close_iv = await _get_price(
                    pos["expiry"], pos["strike"], last_spot, last_date)
                proceeds = close_price * pos["quantity"] * mult
                cash += proceeds
                total_extracted += proceeds
                _record_trade(last_date, "CLOSE", pos["strike"], pos["expiry"], last_spot,
                              close_price, pos["quantity"], proceeds, "回测结束平仓",
                              close_src, close_iv)

    # ── Summary ──
    final_equity = cash
    total_pnl = final_equity - config.initial_capital
    max_equity = max((e["equity"] for e in equity_curve), default=config.initial_capital)
    min_equity = min((e["equity"] for e in equity_curve), default=config.initial_capital)

    max_drawdown = 0.0
    peak = config.initial_capital
    for e in equity_curve:
        if e["equity"] > peak:
            peak = e["equity"]
        dd = (peak - e["equity"]) / peak if peak > 0 else 0
        if dd > max_drawdown:
            max_drawdown = dd

    roll_outs = sum(1 for t in trades if t.get("action") == "ROLL_OUT")
    roll_ups = sum(1 for t in trades if t.get("action") == "ROLL_UP")
    add_dips = sum(1 for t in trades if t.get("action") == "ADD_DIP")

    days = (dates[-1] - dates[0]).days if len(dates) > 1 else 1
    years = max(days / 365.25, 0.01)
    if final_equity > 0 and config.initial_capital > 0:
        annualized_return = ((final_equity / config.initial_capital) ** (1 / years) - 1) * 100
    else:
        annualized_return = -100.0

    summary = {
        "initial_capital": config.initial_capital,
        "final_equity": float(round(final_equity, 2)),
        "total_pnl": float(round(total_pnl, 2)),
        "total_return_pct": float(round(total_pnl / config.initial_capital * 100, 2)),
        "annualized_return_pct": float(round(annualized_return, 2)),
        "max_equity": float(round(max_equity, 2)),
        "min_equity": float(round(min_equity, 2)),
        "max_drawdown_pct": float(round(max_drawdown * 100, 2)),
        "total_invested": float(round(total_invested, 2)),
        "total_extracted": float(round(total_extracted, 2)),
        "final_cost_basis": float(round(total_invested - total_extracted, 2)),
        "cost_basis_negative": bool((total_invested - total_extracted) < 0),
        "total_trades": len(trades),
        "roll_out_count": roll_outs,
        "roll_up_count": roll_ups,
        "add_dip_count": add_dips,
        "backtest_days": days,
        "data_mode": "real",
    }

    return LeapsBacktestResult(
        equity_curve=equity_curve, trades=trades, summary=summary, iv_smiles=iv_smiles)


def _make_equity_point_real(today, cash, spot, positions, cost_basis,
                            total_invested, total_extracted, r, fallback_iv, mult):
    """Build equity curve point (real data mode uses fallback IV for MTM)."""
    holdings_value = 0.0
    for pos in positions:
        iv = pos.get("iv_used") or fallback_iv
        holdings_value += calc_position_market_value(pos, spot, r, iv, mult, today)
    return {
        "date": today.isoformat(),
        "equity": float(round(cash + holdings_value, 2)),
        "spot": float(spot),
        "positions": len(positions),
        "cost_basis": float(round(total_invested - total_extracted, 2)),
        "cash": float(round(cash, 2)),
        "total_invested": float(round(total_invested, 2)),
        "total_extracted": float(round(total_extracted, 2)),
    }


# ── Streaming endpoint for real data ────────────────────────────────

@router.post("/real-backtest-stream")
async def leaps_real_backtest_stream(config: LeapsConfig):
    """Run LEAPS real data backtest with SSE progress streaming."""
    if config.start_date >= config.end_date:
        raise HTTPException(status_code=400, detail="开始日期必须早于结束日期")

    # Extract underlying symbol (e.g. "BTC" from "BTC-USD")
    underlying_symbol = config.underlying.split("-")[0]

    async def event_generator():
        try:
            price_map = await fetch_deribit_index_prices(
                underlying_symbol, config.start_date, config.end_date)
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': f'获取Deribit价格失败: {str(e)}'})}\n\n"
            return

        if not price_map:
            yield f"data: {json.dumps({'type': 'error', 'message': '无法获取Deribit价格数据'})}\n\n"
            return

        total = len(price_map)
        yield f"data: {json.dumps({'type': 'progress', 'day': 0, 'total': total, 'date': config.start_date.isoformat(), 'pct': 0})}\n\n"

        progress_queue = asyncio.Queue()

        async def progress_callback(day_idx, total_days, current_date, status="计算中"):
            await progress_queue.put({
                'type': 'progress', 'day': day_idx, 'total': total_days,
                'date': current_date.isoformat(),
                'pct': round(day_idx / total_days * 100, 1),
                'status': status,
            })

        async def run_bt():
            return await run_real_leaps_backtest(
                price_map=price_map, config=config,
                underlying_symbol=underlying_symbol,
                progress_callback=progress_callback)

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
            result_dict = result.model_dump() if hasattr(result, 'model_dump') else result.dict()
            yield f"data: {json.dumps({'type': 'result', 'data': result_dict})}\n\n"
        except Exception as e:
            import traceback
            traceback.print_exc()
            yield f"data: {json.dumps({'type': 'error', 'message': f'LEAPS真实数据回测失败: {str(e)}'})}\n\n"

        yield "data: {\"type\": \"done\"}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")
