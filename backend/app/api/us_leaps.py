"""美股 LEAPS 策略 API

使用 yfinance 获取美股期权数据：
- 实时扫描：获取当前期权链，筛选最优 LEAPS CALL
- 回测：yfinance 历史股价 + BS 模型估算期权价格
- 支持任意美股标的（AAPL, MSFT, SPY, QQQ 等）

美股期权特点：
- 合约乘数固定为 100（1张合约 = 100股）
- 到期日为每月第三个周五（月度），LEAPS 通常为1月到期
- 行权价间距根据股价不同而不同
"""
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from typing import List, Optional, Dict
from datetime import datetime, date, timedelta, timezone
import asyncio
import json
import math

import yfinance as yf

from app.services.pricing import black_scholes_price, calculate_time_to_expiration

router = APIRouter(prefix="/api/us-leaps", tags=["us-leaps"])

# US options: risk-free rate ~ 4.5% (current T-bill rate)
US_RISK_FREE_RATE = 0.045
US_CONTRACT_MULTIPLIER = 100  # 1 contract = 100 shares


def _safe_float(val, default=0.0):
    """Safely convert a value to float, handling NaN/None/inf."""
    if val is None:
        return default
    try:
        f = float(val)
        if math.isnan(f) or math.isinf(f):
            return default
        return f
    except (ValueError, TypeError):
        return default


def _safe_int(val, default=0):
    """Safely convert a value to int, handling NaN/None."""
    if val is None:
        return default
    try:
        f = float(val)
        if math.isnan(f) or math.isinf(f):
            return default
        return int(f)
    except (ValueError, TypeError):
        return default


# ── yfinance helpers ─────────────────────────────────────────────────

def _get_spot_price(ticker_symbol: str) -> float:
    """Get current spot price via yfinance."""
    t = yf.Ticker(ticker_symbol)
    info = t.fast_info
    price = _safe_float(info.get("lastPrice", 0)) or _safe_float(info.get("previousClose", 0))
    return price


def _get_historical_prices(ticker_symbol: str, start: date, end: date) -> Dict[date, float]:
    """Get historical daily close prices via yfinance."""
    t = yf.Ticker(ticker_symbol)
    df = t.history(start=start.isoformat(), end=(end + timedelta(days=1)).isoformat(),
                   interval="1d")
    prices = {}
    for idx, row in df.iterrows():
        d = idx.date() if hasattr(idx, 'date') else idx
        prices[d] = float(row["Close"])
    return prices


def _get_option_chain(ticker_symbol: str, expiry_str: str):
    """Get option chain for a specific expiry date.
    Returns (calls_df, puts_df) or (None, None) if not available."""
    try:
        t = yf.Ticker(ticker_symbol)
        chain = t.option_chain(expiry_str)
        return chain.calls, chain.puts
    except Exception:
        return None, None


def _get_available_expiries(ticker_symbol: str) -> List[str]:
    """Get all available option expiry dates."""
    try:
        t = yf.Ticker(ticker_symbol)
        return list(t.options)
    except Exception:
        return []


def _find_leaps_expiries(ticker_symbol: str, min_months: int = 12) -> List[str]:
    """Find expiry dates that are at least min_months away (LEAPS)."""
    all_expiries = _get_available_expiries(ticker_symbol)
    min_date = date.today() + timedelta(days=min_months * 30)
    return [e for e in all_expiries if date.fromisoformat(e) >= min_date]


# ── Schemas ──────────────────────────────────────────────────────────

class USLeapsConfig(BaseModel):
    ticker: str = Field(default="AAPL", description="美股标的代码")
    start_date: date
    end_date: date
    initial_capital: float = Field(default=100000)
    max_annual_tv_pct: float = Field(default=10.0, description="最大年化TV%")
    min_expiry_months: int = Field(default=12)
    close_days_before: int = Field(default=30)
    num_contracts: int = Field(default=1, description="每次买入合约数")
    num_strikes: int = Field(default=15, description="扫描行权价数量")
    open_interval_days: int = Field(default=30)
    default_iv: float = Field(default=0.3, description="回测默认IV")
    enable_roll: bool = Field(default=False, description="是否启用换仓逻辑")
    roll_annual_tv_pct: float = Field(default=8.0, description="换仓年化TV差值阈值%")


# ── US stock strike step logic ───────────────────────────────────────

def _us_strike_step(price: float) -> float:
    """Determine strike price step for US stock options."""
    if price < 25:
        return 2.5
    elif price < 200:
        return 5.0
    elif price < 500:
        return 10.0
    else:
        return 25.0


def _nearest_strike(price: float, step: float) -> float:
    return round(round(price / step) * step, 2)


# ── US LEAPS expiry generation for backtest ──────────────────────────

def _generate_us_leaps_expiries(today: date, min_months: int) -> List[date]:
    """Generate plausible US LEAPS expiry dates for historical backtest.

    US LEAPS typically expire on the 3rd Friday of January.
    Monthly options expire on the 3rd Friday of each month.
    """
    import calendar
    results = []
    min_target = today + timedelta(days=min_months * 30)

    # Generate January LEAPS for next 3 years
    for year_offset in range(0, 4):
        year = today.year + year_offset
        # January LEAPS: 3rd Friday of January
        jan_cal = calendar.monthcalendar(year, 1)
        # Find 3rd Friday
        fridays = [week[calendar.FRIDAY] for week in jan_cal if week[calendar.FRIDAY] != 0]
        if len(fridays) >= 3:
            exp = date(year, 1, fridays[2])
            if exp > today:
                results.append(exp)

    # Also add quarterly monthlies (3rd Friday of Mar, Jun, Sep, Dec)
    for year_offset in range(0, 3):
        year = today.year + year_offset
        for month in [3, 6, 9, 12]:
            cal = calendar.monthcalendar(year, month)
            fridays = [week[calendar.FRIDAY] for week in cal if week[calendar.FRIDAY] != 0]
            if len(fridays) >= 3:
                exp = date(year, month, fridays[2])
                if exp > today and exp not in results:
                    results.append(exp)

    results.sort()
    return results


def _find_best_us_expiry(today: date, min_months: int) -> Optional[date]:
    """Find the nearest US LEAPS expiry >= min_months away."""
    min_target = today + timedelta(days=min_months * 30)
    expiries = _generate_us_leaps_expiries(today, min_months)
    for exp in expiries:
        if exp >= min_target:
            return exp
    return expiries[-1] if expiries else None


# ── Core backtest engine (BS model) ─────────────────────────────────

async def run_us_leaps_backtest(
    price_map: Dict[date, float],
    config: USLeapsConfig,
    progress_callback=None,
) -> dict:
    """Run US LEAPS CALL backtest using BS model for option pricing."""
    dates = sorted(price_map.keys())
    if not dates:
        raise HTTPException(status_code=400, detail="No price data")

    mult = US_CONTRACT_MULTIPLIER
    qty = config.num_contracts
    iv = config.default_iv
    _total = [len(dates)]

    cash = config.initial_capital
    position = None
    trades = []
    equity_curve = []
    scan_logs = []

    async def _progress(idx, today, status="计算中"):
        if progress_callback:
            await progress_callback(idx, _total[0], today, status)

    def _bs_price(spot, strike, expiry, today):
        T = calculate_time_to_expiration(expiry, today)
        if T <= 0.0001:
            return float(max(0, spot - strike)), "intrinsic"
        price = black_scholes_price(spot, strike, T, US_RISK_FREE_RATE, iv, "CALL")
        return float(price), "BS模型"

    def _scan_strikes(today, spot, expiry):
        """Scan from ATM downward, return (best, scanned_list)."""
        step = _us_strike_step(spot)
        atm = _nearest_strike(spot, step)
        days_to_exp = max((expiry - today).days, 1)

        candidates = []
        for i in range(config.num_strikes + 1):
            s = atm - i * step
            if s > 0:
                candidates.append(s)

        best = None
        scanned = []

        for strike in candidates:
            price, src = _bs_price(spot, strike, expiry, today)
            if price <= 0:
                scanned.append({
                    "strike": float(strike), "price": 0, "data_source": src,
                    "intrinsic": 0, "time_value": 0, "annual_tv_pct": None,
                    "selected": False, "note": "无数据",
                })
                continue

            intrinsic = max(0.0, spot - strike)
            tv = max(0.0, price - intrinsic)
            annual_tv_pct = (tv / strike) * (365.0 / days_to_exp) * 100.0

            entry = {
                "strike": float(round(strike, 2)),
                "price": float(round(price, 2)),
                "data_source": src,
                "intrinsic": float(round(intrinsic, 2)),
                "time_value": float(round(tv, 2)),
                "annual_tv_pct": float(round(annual_tv_pct, 2)),
                "selected": False,
                "note": "",
            }

            if annual_tv_pct < config.max_annual_tv_pct:
                dist = abs(strike - spot)
                if best is None:
                    best = (strike, price, annual_tv_pct, intrinsic, tv)
                else:
                    best_dist = abs(best[0] - spot)
                    if dist < best_dist or (dist == best_dist and annual_tv_pct < best[2]):
                        best = (strike, price, annual_tv_pct, intrinsic, tv)
                entry["note"] = "满足阈值"
            else:
                entry["note"] = f"年化TV%={annual_tv_pct:.2f}% > {config.max_annual_tv_pct}%"

            scanned.append(entry)

        # Fallback: lowest TV% ITM
        if best is None:
            fb = [e for e in scanned if isinstance(e.get("annual_tv_pct"), (int, float))
                  and e["price"] > 0 and e["strike"] <= spot]
            if not fb:
                fb = [e for e in scanned if isinstance(e.get("annual_tv_pct"), (int, float))
                      and e["price"] > 0]
            if fb:
                fb.sort(key=lambda x: x["annual_tv_pct"])
                f = fb[0]
                best = (f["strike"], f["price"], f["annual_tv_pct"], f["intrinsic"], f["time_value"])

        if best is not None:
            for s in scanned:
                if abs(s["strike"] - best[0]) < 0.01:
                    s["selected"] = True
                    s["note"] = "✓ 选中"

        return best, scanned

    def _check_roll(today, spot, position):
        """Check if we should roll to a further-dated contract (BS model).
        Same logic as LEAPS终极2.0: scan further expiries from ATM downward,
        if (far_tv - cur_tv) annualized < threshold, roll."""
        cur_expiry = position["expiry"]
        cur_strike = position["strike"]

        cur_price, _ = _bs_price(spot, cur_strike, cur_expiry, today)
        if cur_price <= 0:
            return None, []

        cur_intrinsic = max(0.0, spot - cur_strike)
        cur_tv = max(0.0, cur_price - cur_intrinsic)

        # Generate further expiries (at least 30 days beyond current)
        all_expiries = _generate_us_leaps_expiries(today, 0)
        further = [e for e in all_expiries
                   if e > cur_expiry and (e - cur_expiry).days >= 30]
        further.sort(reverse=True)  # furthest first

        step = _us_strike_step(spot)
        atm = _nearest_strike(spot, step)

        roll_scanned = []
        roll_target = None

        for far_expiry in further:
            far_days = max((far_expiry - today).days, 1)

            for i in range(config.num_strikes + 1):
                far_strike = atm - i * step
                if far_strike <= 0:
                    continue

                far_price, far_src = _bs_price(spot, far_strike, far_expiry, today)

                if far_price <= 0:
                    roll_scanned.append({
                        "strike": float(far_strike),
                        "expiry": far_expiry.isoformat(),
                        "price": 0, "far_tv": 0, "cur_tv": float(round(cur_tv, 2)),
                        "tv_diff": 0, "annual_roll_cost": None,
                        "selected": False, "note": "无数据",
                    })
                    continue

                far_intrinsic = max(0.0, spot - far_strike)
                far_tv = max(0.0, far_price - far_intrinsic)
                tv_diff = far_tv - cur_tv
                annual_roll_cost = (tv_diff / far_strike) * (365.0 / far_days) * 100.0 if far_strike > 0 else 999.0

                entry = {
                    "strike": float(round(far_strike, 2)),
                    "expiry": far_expiry.isoformat(),
                    "price": float(round(far_price, 2)),
                    "far_tv": float(round(far_tv, 2)),
                    "cur_tv": float(round(cur_tv, 2)),
                    "tv_diff": float(round(tv_diff, 2)),
                    "annual_roll_cost": float(round(annual_roll_cost, 2)),
                    "selected": False,
                    "note": "",
                }

                if annual_roll_cost < config.roll_annual_tv_pct:
                    if roll_target is None:
                        roll_target = {
                            "strike": far_strike, "expiry": far_expiry,
                            "price": far_price, "src": far_src,
                            "annual_roll_cost": annual_roll_cost,
                            "far_tv": far_tv, "cur_tv": cur_tv,
                            "cur_price": cur_price,
                        }
                        entry["selected"] = True
                        entry["note"] = f"✓ 换仓目标(年化成本={annual_roll_cost:.2f}%)"
                    else:
                        entry["note"] = f"满足阈值(已选更优)"
                else:
                    entry["note"] = f"年化成本={annual_roll_cost:.2f}% > {config.roll_annual_tv_pct}%"

                roll_scanned.append(entry)

            if roll_target is not None:
                break

        return roll_target, roll_scanned

    # Build observation dates
    observe_dates = []
    if dates:
        observe_dates.append(dates[0])
        last_obs = dates[0]
        for d in dates[1:]:
            if (d - last_obs).days >= config.open_interval_days:
                observe_dates.append(d)
                last_obs = d
        if observe_dates[-1] != dates[-1]:
            observe_dates.append(dates[-1])

    _total[0] = len(observe_dates)
    roll_count = 0

    for day_idx, today in enumerate(observe_dates):
        spot = price_map[today]
        await _progress(day_idx, today)

        # 1) Check close
        if position is not None:
            days_left = (position["expiry"] - today).days
            if days_left <= config.close_days_before:
                close_price, close_src = _bs_price(spot, position["strike"], position["expiry"], today)
                proceeds = close_price * position["quantity"] * mult
                cash += proceeds
                pnl = (close_price - position["open_price"]) * position["quantity"] * mult

                trades.append({
                    "date": today.isoformat(), "action": "CLOSE",
                    "strike": float(round(position["strike"], 2)),
                    "expiry": position["expiry"].isoformat(),
                    "spot": float(round(spot, 2)),
                    "option_price": float(round(close_price, 2)),
                    "quantity": position["quantity"],
                    "cash_flow": float(round(proceeds, 2)),
                    "equity_after": float(round(cash, 2)),
                    "data_source": close_src,
                    "note": f"到期前{days_left}天平仓, PnL=${pnl:.2f}",
                })
                position = None

        # 1.5) If holding and not closing, check roll
        if position is not None and config.enable_roll:
            roll_target, roll_scanned = _check_roll(today, spot, position)

            roll_scan_entry = {
                "date": today.isoformat(),
                "spot": float(round(spot, 2)),
                "expiry": position["expiry"].isoformat(),
                "days_to_expiry": (position["expiry"] - today).days,
                "candidates": [],
                "selected_strike": None,
                "selected_expiry": None,
                "roll_candidates": roll_scanned,
                "result": "",
            }

            if roll_target is not None:
                old_strike = position["strike"]
                old_expiry = position["expiry"]

                # Close current position
                cur_price = roll_target["cur_price"]
                close_proceeds = cur_price * position["quantity"] * mult
                cash += close_proceeds
                pnl_close = (cur_price - position["open_price"]) * position["quantity"] * mult

                trades.append({
                    "date": today.isoformat(), "action": "CLOSE",
                    "strike": float(round(old_strike, 2)),
                    "expiry": old_expiry.isoformat(),
                    "spot": float(round(spot, 2)),
                    "option_price": float(round(cur_price, 2)),
                    "quantity": position["quantity"],
                    "cash_flow": float(round(close_proceeds, 2)),
                    "equity_after": float(round(cash, 2)),
                    "data_source": "roll",
                    "note": f"换仓平仓 K={old_strike}, PnL=${pnl_close:.2f}",
                })

                # Open new position
                new_strike = roll_target["strike"]
                new_expiry = roll_target["expiry"]
                new_price = roll_target["price"]
                new_cost = new_price * qty * mult

                actual_qty = qty
                if new_cost > cash and new_price * mult > 0:
                    actual_qty = int(cash / (new_price * mult))
                    new_cost = new_price * actual_qty * mult

                if actual_qty > 0 and new_cost <= cash and new_cost > 0:
                    cash -= new_cost
                    position = {
                        "strike": new_strike, "expiry": new_expiry,
                        "quantity": actual_qty, "open_price": float(new_price),
                        "open_date": today,
                    }
                    qty_note = f"(原计划{qty}张,实际{actual_qty}张)" if actual_qty != qty else ""
                    trades.append({
                        "date": today.isoformat(), "action": "ROLL",
                        "strike": float(round(new_strike, 2)),
                        "expiry": new_expiry.isoformat(),
                        "spot": float(round(spot, 2)),
                        "option_price": float(round(new_price, 2)),
                        "quantity": actual_qty,
                        "cash_flow": float(round(-new_cost, 2)),
                        "equity_after": float(round(cash, 2)),
                        "data_source": "BS模型",
                        "note": f"换仓至 K={new_strike}{qty_note}, 到期{new_expiry}, "
                                f"年化换仓成本={roll_target['annual_roll_cost']:.2f}%",
                    })
                    roll_count += 1
                    roll_scan_entry["selected_strike"] = float(round(new_strike, 2))
                    roll_scan_entry["selected_expiry"] = new_expiry.isoformat()
                    roll_scan_entry["result"] = f"换仓(K={old_strike}→{new_strike}, 到期{old_expiry}→{new_expiry})"
                else:
                    roll_scan_entry["result"] = f"换仓资金不足(需${new_cost:.2f}, 有${cash:.2f})"
                    position = None  # already closed above
            else:
                days_left = (position["expiry"] - today).days
                roll_scan_entry["result"] = f"持仓中(K={position['strike']}, 剩余{days_left}天, 无需换仓)"

            if roll_scanned:
                scan_logs.append(roll_scan_entry)

        # 2) Open new position
        if position is None and cash > 0:
            expiry = _find_best_us_expiry(today, config.min_expiry_months)
            if expiry is not None:
                T = calculate_time_to_expiration(expiry, today)
                if T > 0.1:
                    best, scanned = _scan_strikes(today, spot, expiry)

                    scan_entry = {
                        "date": today.isoformat(),
                        "spot": float(round(spot, 2)),
                        "expiry": expiry.isoformat(),
                        "days_to_expiry": (expiry - today).days,
                        "candidates": scanned,
                        "selected_strike": None,
                        "result": "",
                    }

                    if best is not None:
                        strike, price, atv, intr, tv = best
                        cost_per_contract = price * mult
                        cost = cost_per_contract * qty
                        scan_entry["selected_strike"] = float(round(strike, 2))

                        # 如果资金不足以买指定数量，自动减少合约数
                        actual_qty = qty
                        if cost > cash and cost_per_contract > 0:
                            actual_qty = int(cash / cost_per_contract)
                            cost = cost_per_contract * actual_qty

                        if actual_qty > 0 and cost <= cash and cost > 0:
                            cash -= cost
                            position = {
                                "strike": strike, "expiry": expiry,
                                "quantity": actual_qty, "open_price": float(price),
                                "open_date": today,
                            }
                            qty_note = f"(原计划{qty}张,实际{actual_qty}张)" if actual_qty != qty else ""
                            trades.append({
                                "date": today.isoformat(), "action": "OPEN",
                                "strike": float(round(strike, 2)),
                                "expiry": expiry.isoformat(),
                                "spot": float(round(spot, 2)),
                                "option_price": float(round(price, 2)),
                                "quantity": actual_qty,
                                "cash_flow": float(round(-cost, 2)),
                                "equity_after": float(round(cash, 2)),
                                "data_source": "BS模型",
                                "note": f"买入CALL{qty_note}, 年化TV%={atv:.2f}%, 到期{expiry}",
                            })
                            scan_entry["result"] = f"开仓({actual_qty}张, cost=${cost:.2f})"
                        else:
                            scan_entry["result"] = f"资金不足(需${cost_per_contract:.2f}/张, 有${cash:.2f})"
                    else:
                        scan_entry["result"] = "未找到合约"

                    scan_logs.append(scan_entry)

        # 3) Mark-to-market
        holdings = 0.0
        if position is not None:
            mtm_price, _ = _bs_price(spot, position["strike"], position["expiry"], today)
            holdings = float(mtm_price * position["quantity"] * mult)

        equity_curve.append({
            "date": today.isoformat(),
            "equity": float(round(cash + holdings, 2)),
            "spot": float(spot),
            "cash": float(round(cash, 2)),
            "holdings": float(round(holdings, 2)),
            "has_position": position is not None,
        })

    # Close remaining
    if position is not None and dates:
        last_date = dates[-1]
        last_spot = price_map[last_date]
        close_price, _ = _bs_price(last_spot, position["strike"], position["expiry"], last_date)
        proceeds = close_price * position["quantity"] * mult
        cash += proceeds
        pnl = (close_price - position["open_price"]) * position["quantity"] * mult
        trades.append({
            "date": last_date.isoformat(), "action": "CLOSE",
            "strike": float(round(position["strike"], 2)),
            "expiry": position["expiry"].isoformat(),
            "spot": float(round(last_spot, 2)),
            "option_price": float(round(close_price, 2)),
            "quantity": position["quantity"],
            "cash_flow": float(round(proceeds, 2)),
            "equity_after": float(round(cash, 2)),
            "data_source": "BS模型",
            "note": f"回测结束平仓, PnL=${pnl:.2f}",
        })
        position = None
        if equity_curve and equity_curve[-1]["date"] == last_date.isoformat():
            equity_curve[-1]["equity"] = float(round(cash, 2))
            equity_curve[-1]["cash"] = float(round(cash, 2))
            equity_curve[-1]["holdings"] = 0.0
            equity_curve[-1]["has_position"] = False

    # Summary
    final_equity = cash
    total_pnl = final_equity - config.initial_capital
    max_dd = 0.0
    peak = config.initial_capital
    for e in equity_curve:
        if e["equity"] > peak:
            peak = e["equity"]
        dd = (peak - e["equity"]) / peak if peak > 0 else 0
        if dd > max_dd:
            max_dd = dd

    days_total = (dates[-1] - dates[0]).days if len(dates) > 1 else 1
    years = max(days_total / 365.25, 0.01)
    ann_ret = ((final_equity / config.initial_capital) ** (1 / years) - 1) * 100 if final_equity > 0 else -100.0

    # Sharpe
    ann_factor = 365.0 / max(config.open_interval_days, 1)
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
                strategy_sharpe = (mean_r / std_r) * math.sqrt(ann_factor)

    spot_sharpe = 0.0
    spot_return_pct = 0.0
    if len(equity_curve) >= 2:
        fs = equity_curve[0]["spot"]
        ls = equity_curve[-1]["spot"]
        if fs > 0:
            spot_return_pct = (ls / fs - 1.0) * 100.0
        srets = []
        for i in range(1, len(equity_curve)):
            ps = equity_curve[i - 1]["spot"]
            cs = equity_curve[i]["spot"]
            if ps > 0:
                srets.append(cs / ps - 1.0)
        if len(srets) >= 2:
            ms = sum(srets) / len(srets)
            vs = sum((r - ms) ** 2 for r in srets) / (len(srets) - 1)
            ss = math.sqrt(vs) if vs > 0 else 0.0
            if ss > 0:
                spot_sharpe = (ms / ss) * math.sqrt(ann_factor)

    summary = {
        "ticker": config.ticker,
        "initial_capital": float(config.initial_capital),
        "final_equity": float(round(final_equity, 2)),
        "total_pnl": float(round(total_pnl, 2)),
        "total_return_pct": float(round(total_pnl / config.initial_capital * 100, 2)),
        "annualized_return_pct": float(round(ann_ret, 2)),
        "max_drawdown_pct": float(round(max_dd * 100, 2)),
        "open_count": sum(1 for t in trades if t["action"] == "OPEN"),
        "close_count": sum(1 for t in trades if t["action"] == "CLOSE"),
        "roll_count": roll_count,
        "backtest_days": days_total,
        "default_iv": float(iv),
        "sharpe_ratio": float(round(strategy_sharpe, 3)),
        "spot_sharpe_ratio": float(round(spot_sharpe, 3)),
        "spot_return_pct": float(round(spot_return_pct, 2)),
    }

    return {
        "equity_curve": equity_curve,
        "trades": trades,
        "summary": summary,
        "scan_logs": scan_logs,
    }


# ── API Endpoints ────────────────────────────────────────────────────

class BacktestRequest(BaseModel):
    ticker: str = Field(default="AAPL")
    start_date: date
    end_date: date
    initial_capital: float = Field(default=100000)
    max_annual_tv_pct: float = Field(default=10.0)
    min_expiry_months: int = Field(default=12)
    close_days_before: int = Field(default=30)
    num_contracts: int = Field(default=1)
    num_strikes: int = Field(default=15)
    open_interval_days: int = Field(default=30)
    default_iv: float = Field(default=0.3)
    enable_roll: bool = Field(default=False)
    roll_annual_tv_pct: float = Field(default=8.0)


@router.post("/backtest-stream")
async def us_leaps_stream(req: BacktestRequest):
    """Run US LEAPS backtest with SSE progress streaming."""
    if req.start_date >= req.end_date:
        raise HTTPException(status_code=400, detail="开始日期必须早于结束日期")

    config = USLeapsConfig(
        ticker=req.ticker,
        start_date=req.start_date,
        end_date=req.end_date,
        initial_capital=req.initial_capital,
        max_annual_tv_pct=req.max_annual_tv_pct,
        min_expiry_months=req.min_expiry_months,
        close_days_before=req.close_days_before,
        num_contracts=req.num_contracts,
        num_strikes=req.num_strikes,
        open_interval_days=req.open_interval_days,
        default_iv=req.default_iv,
        enable_roll=req.enable_roll,
        roll_annual_tv_pct=req.roll_annual_tv_pct,
    )

    async def event_generator():
        # Step 1: Fetch historical prices
        yield f"data: {json.dumps({'type': 'progress', 'day': 0, 'total': 1, 'date': req.start_date.isoformat(), 'pct': 0, 'status': f'正在从yfinance获取{req.ticker}历史价格...'})}\n\n"

        try:
            price_map = await asyncio.get_event_loop().run_in_executor(
                None, _get_historical_prices, req.ticker, req.start_date, req.end_date)
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': f'获取{req.ticker}历史价格失败: {str(e)}'})}\n\n"
            return

        if not price_map:
            yield f"data: {json.dumps({'type': 'error', 'message': f'无法获取{req.ticker}价格数据'})}\n\n"
            return

        total = len(price_map)
        dates_sorted = sorted(price_map.keys())
        price_range = f"${min(price_map.values()):.2f} - ${max(price_map.values()):.2f}"
        yield f"data: {json.dumps({'type': 'progress', 'day': 0, 'total': total, 'date': req.start_date.isoformat(), 'pct': 0, 'status': f'获取到{total}天价格数据({price_range}), 开始回测...'})}\n\n"

        progress_queue = asyncio.Queue()

        async def progress_callback(day_idx, total_days, current_date, status="计算中"):
            await progress_queue.put({
                'type': 'progress', 'day': day_idx, 'total': total_days,
                'date': current_date.isoformat(),
                'pct': round(day_idx / total_days * 100, 1),
                'status': status,
            })

        async def run_bt():
            return await run_us_leaps_backtest(
                price_map=price_map, config=config,
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
            yield f"data: {json.dumps({'type': 'result', 'data': result})}\n\n"
        except Exception as e:
            import traceback
            traceback.print_exc()
            yield f"data: {json.dumps({'type': 'error', 'message': f'美股LEAPS回测失败: {str(e)}'})}\n\n"

        yield "data: {\"type\": \"done\"}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


# ── Live scan endpoint ───────────────────────────────────────────────

class LiveScanRequest(BaseModel):
    ticker: str = Field(default="AAPL")
    max_annual_tv_pct: float = Field(default=10.0)
    min_expiry_months: int = Field(default=12)
    num_strikes: int = Field(default=15)


@router.post("/live-scan")
async def us_leaps_live_scan(req: LiveScanRequest):
    """Scan current market for the best US LEAPS contract using yfinance real data.
    Returns SSE stream with step-by-step progress."""

    async def event_generator():
        # Step 1: Get spot price
        yield f"data: {json.dumps({'type': 'step', 'step': 1, 'total_steps': 5, 'message': f'获取{req.ticker}当前价格...'})}\n\n"

        try:
            spot = await asyncio.get_event_loop().run_in_executor(
                None, _get_spot_price, req.ticker)
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': f'获取{req.ticker}价格失败: {str(e)}'})}\n\n"
            return

        if spot <= 0:
            yield f"data: {json.dumps({'type': 'error', 'message': f'无法获取{req.ticker}价格'})}\n\n"
            return

        yield f"data: {json.dumps({'type': 'step', 'step': 2, 'total_steps': 5, 'message': f'{req.ticker}现价 ${spot:.2f}, 获取可用到期日...'})}\n\n"

        # Step 2: Get expiries
        try:
            def _get_expiries():
                leaps = _find_leaps_expiries(req.ticker, req.min_expiry_months)
                all_exp = _get_available_expiries(req.ticker)
                return leaps, all_exp
            leaps_expiries, all_expiries = await asyncio.get_event_loop().run_in_executor(
                None, _get_expiries)
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': f'获取到期日失败: {str(e)}'})}\n\n"
            return

        if not leaps_expiries:
            yield f"data: {json.dumps({'type': 'result', 'data': {'error': '无可用LEAPS到期日', 'spot': float(round(spot, 2)), 'ticker': req.ticker, 'date': date.today().isoformat(), 'all_expiries': all_expiries}})}\n\n"
            yield "data: {\"type\": \"done\"}\n\n"
            return

        yield f"data: {json.dumps({'type': 'step', 'step': 3, 'total_steps': 5, 'message': f'找到{len(leaps_expiries)}个LEAPS到期日, 获取{leaps_expiries[0]}期权链...'})}\n\n"

        # Step 3: Get option chain for first expiry
        expiry_str = leaps_expiries[0]
        expiry_date = date.fromisoformat(expiry_str)

        try:
            calls_df, _ = await asyncio.get_event_loop().run_in_executor(
                None, _get_option_chain, req.ticker, expiry_str)
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': f'获取期权链失败: {str(e)}'})}\n\n"
            return

        if calls_df is None or calls_df.empty:
            yield f"data: {json.dumps({'type': 'error', 'message': f'无法获取{expiry_str}期权链'})}\n\n"
            return

        days_to_exp = max((expiry_date - date.today()).days, 1)
        step = _us_strike_step(spot)
        atm = _nearest_strike(spot, step)

        yield f"data: {json.dumps({'type': 'step', 'step': 4, 'total_steps': 5, 'message': f'扫描{len(calls_df)}个合约, ATM={atm}, 步长={step}...'})}\n\n"

        # Step 4: Scan strikes
        scanned = []
        best = None
        strike_candidates = []
        for i in range(req.num_strikes + 1):
            s = atm - i * step
            if s > 0:
                strike_candidates.append(s)

        for strike in strike_candidates:
            row = calls_df[abs(calls_df["strike"] - strike) < 0.01]
            if row.empty:
                closest_idx = (calls_df["strike"] - strike).abs().idxmin()
                row = calls_df.loc[[closest_idx]]
                if abs(row.iloc[0]["strike"] - strike) > step * 0.6:
                    scanned.append({
                        "strike": float(strike), "price": 0,
                        "bid": 0, "ask": 0, "iv": None,
                        "volume": 0, "open_interest": 0,
                        "intrinsic": 0, "time_value": 0,
                        "annual_tv_pct": None, "selected": False,
                        "note": "无此行权价",
                    })
                    continue

            r = row.iloc[0]
            actual_strike = float(r["strike"])
            bid = _safe_float(r.get("bid", 0))
            ask = _safe_float(r.get("ask", 0))
            price = (bid + ask) / 2 if bid > 0 and ask > 0 else _safe_float(r.get("lastPrice", 0))

            if price <= 0:
                scanned.append({
                    "strike": float(round(actual_strike, 2)), "price": 0,
                    "bid": float(round(bid, 2)), "ask": float(round(ask, 2)),
                    "iv": None, "volume": 0, "open_interest": 0,
                    "intrinsic": 0, "time_value": 0,
                    "annual_tv_pct": None, "selected": False,
                    "note": "无报价",
                })
                continue

            iv_val = _safe_float(r.get("impliedVolatility", 0))
            volume = _safe_int(r.get("volume", 0))
            oi = _safe_int(r.get("openInterest", 0))
            intrinsic = max(0.0, spot - actual_strike)
            tv = max(0.0, price - intrinsic)
            annual_tv_pct = (tv / actual_strike) * (365.0 / days_to_exp) * 100.0

            entry = {
                "strike": float(round(actual_strike, 2)),
                "price": float(round(price, 2)),
                "bid": float(round(bid, 2)),
                "ask": float(round(ask, 2)),
                "iv": float(round(iv_val, 4)) if iv_val else None,
                "volume": volume,
                "open_interest": oi,
                "intrinsic": float(round(intrinsic, 2)),
                "time_value": float(round(tv, 2)),
                "annual_tv_pct": float(round(annual_tv_pct, 2)),
                "selected": False,
                "note": "",
            }

            if annual_tv_pct < req.max_annual_tv_pct:
                dist = abs(actual_strike - spot)
                if best is None:
                    best = entry
                else:
                    best_dist = abs(best["strike"] - spot)
                    if dist < best_dist or (dist == best_dist and annual_tv_pct < best["annual_tv_pct"]):
                        best = entry
                entry["note"] = "满足阈值"
            else:
                entry["note"] = f"年化TV%={annual_tv_pct:.2f}% > {req.max_annual_tv_pct}%"

            scanned.append(entry)

        # Fallback
        fallback_used = False
        if best is None:
            fb = [e for e in scanned if isinstance(e.get("annual_tv_pct"), (int, float))
                  and e["price"] > 0 and e["strike"] <= spot]
            if not fb:
                fb = [e for e in scanned if isinstance(e.get("annual_tv_pct"), (int, float))
                      and e["price"] > 0]
            if fb:
                fb.sort(key=lambda x: x["annual_tv_pct"])
                best = fb[0]
                fallback_used = True

        if best is not None:
            for s in scanned:
                if abs(s["strike"] - best["strike"]) < 0.01:
                    s["selected"] = True
                    s["note"] = "✓ 推荐持仓" + ("(回退)" if fallback_used else "")

        # Step 5: Scan second expiry
        scanned2 = []
        expiry2_str = None
        if len(leaps_expiries) > 1:
            expiry2_str = leaps_expiries[1]
            yield f"data: {json.dumps({'type': 'step', 'step': 5, 'total_steps': 5, 'message': f'扫描第二个到期日 {expiry2_str}...'})}\n\n"

            try:
                calls2, _ = await asyncio.get_event_loop().run_in_executor(
                    None, _get_option_chain, req.ticker, expiry2_str)
            except Exception:
                calls2 = None

            if calls2 is not None and not calls2.empty:
                exp2_date = date.fromisoformat(expiry2_str)
                days2 = max((exp2_date - date.today()).days, 1)
                for strike in strike_candidates:
                    row2 = calls2[abs(calls2["strike"] - strike) < 0.01]
                    if row2.empty:
                        continue
                    r2 = row2.iloc[0]
                    bid2 = _safe_float(r2.get("bid", 0))
                    ask2 = _safe_float(r2.get("ask", 0))
                    p2 = (bid2 + ask2) / 2 if bid2 > 0 and ask2 > 0 else _safe_float(r2.get("lastPrice", 0))
                    if p2 <= 0:
                        continue
                    intr2 = max(0.0, spot - float(r2["strike"]))
                    tv2 = max(0.0, p2 - intr2)
                    atv2 = (tv2 / float(r2["strike"])) * (365.0 / days2) * 100.0
                    scanned2.append({
                        "strike": float(round(float(r2["strike"]), 2)),
                        "price": float(round(p2, 2)),
                        "bid": float(round(bid2, 2)),
                        "ask": float(round(ask2, 2)),
                        "iv": float(round(_safe_float(r2.get("impliedVolatility", 0)), 4)),
                        "volume": _safe_int(r2.get("volume", 0)),
                        "open_interest": _safe_int(r2.get("openInterest", 0)),
                        "intrinsic": float(round(intr2, 2)),
                        "time_value": float(round(tv2, 2)),
                        "annual_tv_pct": float(round(atv2, 2)),
                        "selected": False,
                        "note": "满足阈值" if atv2 < req.max_annual_tv_pct else f"TV%={atv2:.2f}%",
                    })
        else:
            yield f"data: {json.dumps({'type': 'step', 'step': 5, 'total_steps': 5, 'message': '分析完成'})}\n\n"

        result = {
            "date": date.today().isoformat(),
            "ticker": req.ticker,
            "spot": float(round(spot, 2)),
            "all_expiries": all_expiries,
            "leaps_expiries": leaps_expiries,
            "expiry1": expiry_str,
            "days_to_expiry1": (expiry_date - date.today()).days,
            "candidates1": scanned,
            "expiry2": expiry2_str,
            "days_to_expiry2": (date.fromisoformat(expiry2_str) - date.today()).days if expiry2_str else None,
            "candidates2": scanned2 if scanned2 else None,
            "recommended": best,
            "fallback_used": fallback_used,
        }

        yield f"data: {json.dumps({'type': 'result', 'data': result})}\n\n"
        yield "data: {\"type\": \"done\"}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")
