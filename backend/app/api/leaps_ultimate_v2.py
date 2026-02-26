"""LEAPS终极2.0 策略回测 API

在LEAPS终极版基础上增加换仓逻辑：
每个检查日，如果持仓不需要平仓，则扫描更远期到期日的合约，
判断 (远期合约时间价值 - 当前合约剩余时间价值) 的年化值是否低于换仓阈值。
如果满足则换仓到远期合约。扫描顺序：从远期ATM往低价方向，选首个满足的合约。
"""
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from typing import List, Optional, Dict
from datetime import datetime, date, timedelta, timezone
import asyncio
import json
import math
import httpx

from app.services.pricing import black_scholes_price, calculate_time_to_expiration
from app.core.config import create_http_client
from app.api.deribit import (
    fetch_deribit_index_prices,
    find_nearest_strike,
    get_strike_step,
    build_instrument_name,
    _fetch_single_strike_price_fast,
    DERIBIT_BASE,
    RATE_DELAY,
    RISK_FREE_RATE,
)
from app.api.leaps_ultimate import (
    get_available_expiries_at_date,
    _find_best_available_expiry_sync,
    fetch_okx_prices,
    TradeRecord,
    BacktestResult,
)

router = APIRouter(prefix="/api/leaps-ultimate-v2", tags=["leaps-ultimate-v2"])


# ── Config ───────────────────────────────────────────────────────────

class LeapsV2Config(BaseModel):
    """LEAPS终极2.0 策略配置"""
    underlying: str = Field(default="BTC")
    start_date: date
    end_date: date
    initial_capital: float = Field(default=100000)
    contract_multiplier: float = Field(default=0.01)
    max_annual_tv_pct: float = Field(default=10.0, description="开仓最大年化TV%")
    roll_annual_tv_pct: float = Field(default=8.0, description="换仓年化TV差值阈值%")
    min_expiry_months: int = Field(default=12)
    close_days_before: int = Field(default=30)
    quantity: float = Field(default=1.0)
    num_strikes: int = Field(default=15)
    open_interval_days: int = Field(default=30)
    use_hf_data: bool = Field(default=False, description="优先使用高频数据库")


# ── Core backtest engine ─────────────────────────────────────────────

async def run_leaps_v2(
    price_map: Dict[date, float],
    config: LeapsV2Config,
    use_real_data: bool = True,
    progress_callback=None,
) -> BacktestResult:
    """Run LEAPS终极2.0 strategy backtest with roll logic."""
    dates = sorted(price_map.keys())
    if not dates:
        raise HTTPException(status_code=400, detail="No price data")

    underlying = config.underlying
    mult = config.contract_multiplier
    qty = abs(config.quantity)
    _total_observe = [len(dates)]

    cash = config.initial_capital
    position = None
    trades = []
    equity_curve = []
    scan_logs = []

    async def _progress(day_idx, today, status="计算中"):
        if progress_callback:
            await progress_callback(day_idx, _total_observe[0], today, status)

    async with create_http_client() as client:

        async def _get_price(expiry, strike, spot, today):
            if use_real_data:
                price, src, iv, _ = await _fetch_single_strike_price_fast(
                    client, underlying, expiry, strike, spot, "CALL", today,
                    use_hf_data=config.use_hf_data)
                return float(price), src, iv
            else:
                T = calculate_time_to_expiration(expiry, today)
                if T <= 0.0001:
                    return float(max(0, spot - strike)), "intrinsic", None
                default_iv = 0.6
                price = black_scholes_price(spot, strike, T, RISK_FREE_RATE, default_iv, "CALL")
                return float(price), "model", float(default_iv)

        async def _scan_best_strike(today, spot, expiry):
            """Scan from ATM downward. Pick closest-to-ATM that meets TV% threshold.
            Fallback: lowest TV% ITM."""
            step = get_strike_step(underlying, spot)
            atm = find_nearest_strike(spot, step)
            days_to_exp = max((expiry - today).days, 1)

            candidates = []
            for i in range(config.num_strikes + 1):
                s = atm - i * step
                if s > 0:
                    candidates.append(s)

            best = None
            scanned = []

            for strike in candidates:
                price, src, iv = await _get_price(expiry, strike, spot, today)
                instrument = build_instrument_name(underlying, expiry, strike, "CALL")
                if price <= 0:
                    scanned.append({
                        "strike": float(strike), "instrument": instrument,
                        "price": 0, "iv": None, "data_source": src,
                        "intrinsic": 0, "time_value": 0, "annual_tv_pct": None,
                        "selected": False, "note": "无数据",
                    })
                    continue
                intrinsic = max(0.0, spot - strike)
                tv = max(0.0, price - intrinsic)
                annual_tv_pct = (tv / strike) * (365.0 / days_to_exp) * 100.0

                entry = {
                    "strike": float(round(strike, 2)),
                    "instrument": instrument,
                    "price": float(round(price, 2)),
                    "iv": float(round(iv, 4)) if iv else None,
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
                        best = (strike, price, iv, src, annual_tv_pct, intrinsic, tv)
                    else:
                        best_dist = abs(best[0] - spot)
                        if dist < best_dist or (dist == best_dist and annual_tv_pct < best[4]):
                            best = (strike, price, iv, src, annual_tv_pct, intrinsic, tv)
                    entry["note"] = "满足阈值"
                else:
                    entry["note"] = f"年化TV%={annual_tv_pct:.2f}% > {config.max_annual_tv_pct}%"

                scanned.append(entry)

            # Fallback
            fallback_used = False
            if best is None:
                fb_pool = [
                    e for e in scanned
                    if isinstance(e.get("annual_tv_pct"), (int, float))
                    and e["price"] > 0 and e["strike"] <= spot
                ]
                if not fb_pool:
                    fb_pool = [
                        e for e in scanned
                        if isinstance(e.get("annual_tv_pct"), (int, float))
                        and e["price"] > 0
                    ]
                if fb_pool:
                    fb_pool.sort(key=lambda x: x["annual_tv_pct"])
                    fb = fb_pool[0]
                    best = (
                        fb["strike"], fb["price"], fb.get("iv"),
                        fb["data_source"], fb["annual_tv_pct"],
                        fb["intrinsic"], fb["time_value"],
                    )
                    fallback_used = True

            if best is not None:
                for s in scanned:
                    if abs(s["strike"] - best[0]) < 1.0:
                        s["selected"] = True
                        if fallback_used:
                            s["note"] = "✓ 选中(回退: 最低年化TV% ITM)"
                        else:
                            s["note"] = "✓ 选中(最靠近ATM)"

            return best, scanned

        async def _check_roll(today, spot, position):
            """Check if we should roll to a further-dated contract.

            For each available expiry further than current position's expiry,
            scan from ATM downward. For each candidate strike on the far expiry:
              roll_tv_diff = far_tv - current_remaining_tv
              annual_roll_cost = (roll_tv_diff / far_strike) * (365 / far_days) * 100
            If annual_roll_cost < roll_annual_tv_pct threshold, roll to it.
            Pick the FIRST one found (closest to ATM on the furthest expiry first).

            Returns (roll_target, roll_scanned) or (None, roll_scanned).
            roll_target = {strike, expiry, price, iv, src, annual_roll_cost, far_tv, cur_tv}
            """
            cur_expiry = position["expiry"]
            cur_strike = position["strike"]

            # Get current position's remaining time value
            cur_price, cur_src, cur_iv = await _get_price(
                cur_expiry, cur_strike, spot, today)

            # Guard: if current price is bad, skip roll check
            if cur_price <= 0:
                return None, []

            cur_intrinsic = max(0.0, spot - cur_strike)
            cur_tv = max(0.0, cur_price - cur_intrinsic)

            available = get_available_expiries_at_date(today)
            # Only consider expiries further than current, with at least 30 days extension
            further_expiries = [e for e in available
                                if e > cur_expiry and (e - cur_expiry).days >= 30]
            further_expiries.sort(reverse=True)  # furthest first

            step = get_strike_step(underlying, spot)
            atm = find_nearest_strike(spot, step)

            roll_scanned = []
            roll_target = None

            for far_expiry in further_expiries:
                far_days = max((far_expiry - today).days, 1)

                # Scan from ATM downward
                for i in range(config.num_strikes + 1):
                    far_strike = atm - i * step
                    if far_strike <= 0:
                        continue

                    far_price, far_src, far_iv = await _get_price(
                        far_expiry, far_strike, spot, today)
                    instrument = build_instrument_name(underlying, far_expiry, far_strike, "CALL")

                    if far_price <= 0:
                        roll_scanned.append({
                            "strike": float(far_strike), "instrument": instrument,
                            "expiry": far_expiry.isoformat(),
                            "price": 0, "far_tv": 0, "cur_tv": float(round(cur_tv, 2)),
                            "tv_diff": 0, "annual_roll_cost": None,
                            "selected": False, "note": "无数据",
                        })
                        continue

                    far_intrinsic = max(0.0, spot - far_strike)
                    far_tv = max(0.0, far_price - far_intrinsic)
                    tv_diff = far_tv - cur_tv
                    # Annualize the TV difference cost
                    annual_roll_cost = (tv_diff / far_strike) * (365.0 / far_days) * 100.0 if far_strike > 0 else 999.0

                    entry = {
                        "strike": float(round(far_strike, 2)),
                        "instrument": instrument,
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
                                "price": far_price, "iv": far_iv, "src": far_src,
                                "annual_roll_cost": annual_roll_cost,
                                "far_tv": far_tv, "cur_tv": cur_tv,
                                "cur_price": cur_price,
                            }
                            entry["selected"] = True
                            entry["note"] = f"✓ 换仓目标(年化换仓成本={annual_roll_cost:.2f}%)"
                        else:
                            entry["note"] = f"满足阈值(已选更优)"
                    else:
                        entry["note"] = f"年化换仓成本={annual_roll_cost:.2f}% > {config.roll_annual_tv_pct}%"

                    roll_scanned.append(entry)

                # If we found a target on this expiry, stop (furthest first)
                if roll_target is not None:
                    break

            return roll_target, roll_scanned

        # Build observation dates
        observe_interval = config.open_interval_days
        observe_dates = []
        if dates:
            observe_dates.append(dates[0])
            last_observe = dates[0]
            for d in dates[1:]:
                if (d - last_observe).days >= observe_interval:
                    observe_dates.append(d)
                    last_observe = d
            if observe_dates[-1] != dates[-1]:
                observe_dates.append(dates[-1])

        total_observe = len(observe_dates)
        _total_observe[0] = total_observe
        print(f"[LEAPS2.0] {len(dates)} trading days → {total_observe} obs (every {observe_interval}d)")

        roll_count = 0

        for day_idx, today in enumerate(observe_dates):
            spot = price_map[today]
            await _progress(day_idx, today)

            # ── 1) Check close ──
            if position is not None:
                days_left = (position["expiry"] - today).days
                if days_left <= config.close_days_before:
                    await _progress(day_idx, today, "平仓")
                    close_price, close_src, close_iv = await _get_price(
                        position["expiry"], position["strike"], spot, today)
                    proceeds = close_price * position["quantity"] * mult
                    cash += proceeds
                    pnl = (close_price - position["open_price"]) * position["quantity"] * mult
                    intrinsic = max(0.0, spot - position["strike"])
                    tv = max(0.0, close_price - intrinsic)
                    days_held = max((today - position["open_date"]).days, 1)
                    annual_tv = (tv / position["strike"]) * (365.0 / max(days_left, 1)) * 100.0

                    trades.append(TradeRecord(
                        date=today.isoformat(), action="CLOSE",
                        strike=float(round(position["strike"], 2)),
                        expiry=position["expiry"].isoformat(),
                        spot=float(round(spot, 2)),
                        option_price=float(round(close_price, 2)),
                        quantity=float(position["quantity"]),
                        intrinsic=float(round(intrinsic, 2)),
                        time_value=float(round(tv, 2)),
                        annual_tv_pct=float(round(annual_tv, 2)),
                        cash_flow=float(round(proceeds, 2)),
                        equity_after=float(round(cash, 2)),
                        data_source=close_src,
                        iv_used=float(round(close_iv, 4)) if close_iv else None,
                        note=f"到期前{days_left}天平仓, 持有{days_held}天, PnL=${pnl:.2f}",
                    ).model_dump())
                    position = None

            # ── 2) If holding and not closing, check roll ──
            if position is not None:
                await _progress(day_idx, today, "检查换仓")
                roll_target, roll_scanned = await _check_roll(today, spot, position)

                scan_entry = {
                    "date": today.isoformat(),
                    "spot": float(round(spot, 2)),
                    "expiry": position["expiry"].isoformat(),
                    "days_to_expiry": (position["expiry"] - today).days,
                    "candidates": [],
                    "selected_strike": None,
                    "selected_expiry": None,
                    "roll_candidates": roll_scanned,
                }

                if roll_target is not None:
                    # Execute roll: close current, open new
                    old_strike = position["strike"]
                    old_expiry = position["expiry"]

                    # Close current
                    cur_price = roll_target["cur_price"]
                    close_proceeds = cur_price * position["quantity"] * mult
                    cash += close_proceeds
                    pnl_close = (cur_price - position["open_price"]) * position["quantity"] * mult

                    trades.append(TradeRecord(
                        date=today.isoformat(), action="CLOSE",
                        strike=float(round(old_strike, 2)),
                        expiry=old_expiry.isoformat(),
                        spot=float(round(spot, 2)),
                        option_price=float(round(cur_price, 2)),
                        quantity=float(position["quantity"]),
                        intrinsic=float(round(max(0, spot - old_strike), 2)),
                        time_value=float(round(roll_target["cur_tv"], 2)),
                        annual_tv_pct=0.0,
                        cash_flow=float(round(close_proceeds, 2)),
                        equity_after=float(round(cash, 2)),
                        data_source="roll",
                        iv_used=None,
                        note=f"换仓平仓 K={old_strike}, PnL=${pnl_close:.2f}",
                    ).model_dump())

                    # Open new
                    new_strike = roll_target["strike"]
                    new_expiry = roll_target["expiry"]
                    new_price = roll_target["price"]
                    new_cost = new_price * qty * mult

                    if new_cost <= cash and new_cost > 0:
                        cash -= new_cost
                        far_intrinsic = max(0.0, spot - new_strike)
                        far_tv = roll_target["far_tv"]

                        position = {
                            "strike": new_strike, "expiry": new_expiry,
                            "quantity": qty, "open_price": float(new_price),
                            "open_spot": spot, "open_date": today,
                            "iv_used": roll_target.get("iv"),
                            "data_source": roll_target.get("src"),
                        }

                        trades.append(TradeRecord(
                            date=today.isoformat(), action="ROLL",
                            strike=float(round(new_strike, 2)),
                            expiry=new_expiry.isoformat(),
                            spot=float(round(spot, 2)),
                            option_price=float(round(new_price, 2)),
                            quantity=float(qty),
                            intrinsic=float(round(far_intrinsic, 2)),
                            time_value=float(round(far_tv, 2)),
                            annual_tv_pct=float(round(roll_target["annual_roll_cost"], 2)),
                            cash_flow=float(round(-new_cost, 2)),
                            equity_after=float(round(cash, 2)),
                            data_source=roll_target.get("src"),
                            iv_used=float(round(roll_target["iv"], 4)) if roll_target.get("iv") else None,
                            note=f"换仓至 K={new_strike}, 到期{new_expiry}, "
                                 f"年化换仓成本={roll_target['annual_roll_cost']:.2f}%",
                        ).model_dump())

                        roll_count += 1
                        scan_entry["selected_strike"] = float(round(new_strike, 2))
                        scan_entry["selected_expiry"] = new_expiry.isoformat()
                        scan_entry["result"] = f"换仓(K={old_strike}→{new_strike}, 到期{old_expiry}→{new_expiry})"
                        print(f"[LEAPS2.0] ROLL K={old_strike}→{new_strike}, "
                              f"expiry={old_expiry}→{new_expiry}, "
                              f"annual_roll_cost={roll_target['annual_roll_cost']:.2f}%")
                    else:
                        scan_entry["result"] = f"换仓资金不足(需${new_cost:.2f}, 有${cash:.2f})"
                else:
                    days_left = (position["expiry"] - today).days
                    scan_entry["result"] = f"持仓中(K={position['strike']}, 剩余{days_left}天, 无需换仓)"

                scan_logs.append(scan_entry)

            # ── 3) Open new position if empty ──
            if position is None and cash > 0:
                await _progress(day_idx, today, "查找可用合约")

                expiry = _find_best_available_expiry_sync(today, config.min_expiry_months)

                if expiry is not None:
                    T = calculate_time_to_expiration(expiry, today)
                    if T > 0.1:
                        await _progress(day_idx, today, f"扫描 expiry={expiry}")
                        result, scanned1 = await _scan_best_strike(today, spot, expiry)

                        available_exp = get_available_expiries_at_date(today)
                        scan_entry = {
                            "date": today.isoformat(),
                            "spot": float(round(spot, 2)),
                            "expiry": expiry.isoformat(),
                            "days_to_expiry": (expiry - today).days,
                            "available_expiries": [e.isoformat() for e in available_exp],
                            "candidates": scanned1,
                            "selected_strike": None,
                            "selected_expiry": None,
                        }

                        # Also try next expiry
                        next_expiry = _find_best_available_expiry_sync(today, config.min_expiry_months + 3)
                        best_overall = None
                        if result is not None:
                            strike, price, iv, src, atv, intr, tv = result
                            best_overall = (strike, price, iv, src, atv, intr, tv, expiry)

                        scanned2 = []
                        if next_expiry is not None and next_expiry != expiry:
                            T2 = calculate_time_to_expiration(next_expiry, today)
                            if T2 > 0.1:
                                result2, scanned2 = await _scan_best_strike(today, spot, next_expiry)
                                if result2 is not None:
                                    s2, p2, iv2, src2, atv2, intr2, tv2 = result2
                                    if best_overall is None:
                                        best_overall = (s2, p2, iv2, src2, atv2, intr2, tv2, next_expiry)
                                    else:
                                        dist2 = abs(s2 - spot)
                                        dist1 = abs(best_overall[0] - spot)
                                        if dist2 < dist1 or (dist2 == dist1 and atv2 < best_overall[4]):
                                            best_overall = (s2, p2, iv2, src2, atv2, intr2, tv2, next_expiry)

                        if scanned2:
                            scan_entry["expiry2"] = next_expiry.isoformat() if next_expiry else None
                            scan_entry["days_to_expiry2"] = (next_expiry - today).days if next_expiry else None
                            scan_entry["candidates2"] = scanned2

                        if best_overall is not None:
                            strike, price, iv, src, atv, intr, tv, expiry = best_overall
                            scan_entry["selected_strike"] = float(round(strike, 2))
                            scan_entry["selected_expiry"] = expiry.isoformat()
                            cost = price * qty * mult
                            scan_entry["result"] = f"开仓(cost=${cost:.2f})"
                            if cost <= cash and cost > 0:
                                cash -= cost
                                position = {
                                    "strike": strike, "expiry": expiry, "quantity": qty,
                                    "open_price": float(price), "open_spot": spot,
                                    "open_date": today, "iv_used": iv, "data_source": src,
                                }
                                trades.append(TradeRecord(
                                    date=today.isoformat(), action="OPEN",
                                    strike=float(round(strike, 2)),
                                    expiry=expiry.isoformat(),
                                    spot=float(round(spot, 2)),
                                    option_price=float(round(price, 2)),
                                    quantity=float(qty),
                                    intrinsic=float(round(intr, 2)),
                                    time_value=float(round(tv, 2)),
                                    annual_tv_pct=float(round(atv, 2)),
                                    cash_flow=float(round(-cost, 2)),
                                    equity_after=float(round(cash, 2)),
                                    data_source=src,
                                    iv_used=float(round(iv, 4)) if iv else None,
                                    note=f"买入CALL, 年化TV%={atv:.2f}%, "
                                         f"到期{expiry}, {(expiry-today).days}天",
                                ).model_dump())
                            else:
                                scan_entry["result"] = f"资金不足(需${cost:.2f}, 有${cash:.2f})"
                        else:
                            scan_entry["result"] = "未找到满足条件的合约"

                        scan_logs.append(scan_entry)
                    else:
                        scan_logs.append({
                            "date": today.isoformat(), "spot": float(round(spot, 2)),
                            "expiry": expiry.isoformat(), "days_to_expiry": (expiry - today).days,
                            "candidates": [], "selected_strike": None,
                            "selected_expiry": None, "result": f"到期日太近(T={T:.3f}年)",
                        })
                else:
                    scan_logs.append({
                        "date": today.isoformat(), "spot": float(round(spot, 2)),
                        "expiry": None, "days_to_expiry": 0,
                        "candidates": [], "selected_strike": None,
                        "selected_expiry": None, "result": "无可用到期日",
                    })

            # ── 4) Mark-to-market ──
            holdings = 0.0
            if position is not None:
                T = calculate_time_to_expiration(position["expiry"], today)
                if T > 0.0001:
                    iv_mtm = position.get("iv_used") or 0.6
                    mtm_price = black_scholes_price(spot, position["strike"], T,
                                                     RISK_FREE_RATE, iv_mtm, "CALL")
                    holdings = float(mtm_price * position["quantity"] * mult)
                else:
                    holdings = float(max(0, spot - position["strike"]) * position["quantity"] * mult)

            equity_curve.append({
                "date": today.isoformat(),
                "equity": float(round(cash + holdings, 2)),
                "spot": float(spot),
                "cash": float(round(cash, 2)),
                "holdings": float(round(holdings, 2)),
                "has_position": position is not None,
            })

        # ── Close remaining position at end ──
        if position is not None and dates:
            last_date = dates[-1]
            last_spot = price_map[last_date]
            close_price, close_src, close_iv = await _get_price(
                position["expiry"], position["strike"], last_spot, last_date)
            proceeds = close_price * position["quantity"] * mult
            cash += proceeds
            pnl = (close_price - position["open_price"]) * position["quantity"] * mult
            intrinsic = max(0.0, last_spot - position["strike"])
            tv = max(0.0, close_price - intrinsic)

            trades.append(TradeRecord(
                date=last_date.isoformat(), action="CLOSE",
                strike=float(round(position["strike"], 2)),
                expiry=position["expiry"].isoformat(),
                spot=float(round(last_spot, 2)),
                option_price=float(round(close_price, 2)),
                quantity=float(position["quantity"]),
                intrinsic=float(round(intrinsic, 2)),
                time_value=float(round(tv, 2)),
                annual_tv_pct=0.0,
                cash_flow=float(round(proceeds, 2)),
                equity_after=float(round(cash, 2)),
                data_source=close_src,
                iv_used=float(round(close_iv, 4)) if close_iv else None,
                note=f"回测结束平仓, PnL=${pnl:.2f}",
            ).model_dump())
            position = None

            if equity_curve and equity_curve[-1]["date"] == last_date.isoformat():
                equity_curve[-1]["equity"] = float(round(cash, 2))
                equity_curve[-1]["cash"] = float(round(cash, 2))
                equity_curve[-1]["holdings"] = 0.0
                equity_curve[-1]["has_position"] = False

    # ── Summary ──
    final_equity = cash
    total_pnl = final_equity - config.initial_capital

    max_drawdown = 0.0
    peak = config.initial_capital
    for e in equity_curve:
        if e["equity"] > peak:
            peak = e["equity"]
        dd = (peak - e["equity"]) / peak if peak > 0 else 0
        if dd > max_drawdown:
            max_drawdown = dd

    days = (dates[-1] - dates[0]).days if len(dates) > 1 else 1
    years = max(days / 365.25, 0.01)
    if final_equity > 0 and config.initial_capital > 0:
        ann_ret = ((final_equity / config.initial_capital) ** (1 / years) - 1) * 100
    else:
        ann_ret = -100.0

    open_count = sum(1 for t in trades if t.get("action") == "OPEN")
    close_count = sum(1 for t in trades if t.get("action") == "CLOSE")

    # Sharpe ratio
    ann_factor = 365.0 / max(config.open_interval_days, 1)
    strategy_sharpe = 0.0
    if len(equity_curve) >= 2:
        strat_returns = []
        for i in range(1, len(equity_curve)):
            prev_eq = equity_curve[i - 1]["equity"]
            cur_eq = equity_curve[i]["equity"]
            if prev_eq > 0:
                strat_returns.append(cur_eq / prev_eq - 1.0)
        if len(strat_returns) >= 2:
            mean_r = sum(strat_returns) / len(strat_returns)
            var_r = sum((r - mean_r) ** 2 for r in strat_returns) / (len(strat_returns) - 1)
            std_r = math.sqrt(var_r) if var_r > 0 else 0.0
            if std_r > 0:
                strategy_sharpe = (mean_r / std_r) * math.sqrt(ann_factor)

    spot_sharpe = 0.0
    spot_return_pct = 0.0
    if len(equity_curve) >= 2:
        first_spot = equity_curve[0]["spot"]
        last_spot_val = equity_curve[-1]["spot"]
        if first_spot > 0:
            spot_return_pct = (last_spot_val / first_spot - 1.0) * 100.0
        spot_returns = []
        for i in range(1, len(equity_curve)):
            prev_s = equity_curve[i - 1]["spot"]
            cur_s = equity_curve[i]["spot"]
            if prev_s > 0:
                spot_returns.append(cur_s / prev_s - 1.0)
        if len(spot_returns) >= 2:
            mean_s = sum(spot_returns) / len(spot_returns)
            var_s = sum((r - mean_s) ** 2 for r in spot_returns) / (len(spot_returns) - 1)
            std_s = math.sqrt(var_s) if var_s > 0 else 0.0
            if std_s > 0:
                spot_sharpe = (mean_s / std_s) * math.sqrt(ann_factor)

    summary = {
        "initial_capital": float(config.initial_capital),
        "final_equity": float(round(final_equity, 2)),
        "total_pnl": float(round(total_pnl, 2)),
        "total_return_pct": float(round(total_pnl / config.initial_capital * 100, 2)),
        "annualized_return_pct": float(round(ann_ret, 2)),
        "max_drawdown_pct": float(round(max_drawdown * 100, 2)),
        "total_trades": len(trades),
        "open_count": open_count,
        "close_count": close_count,
        "roll_count": roll_count,
        "backtest_days": days,
        "use_real_data": use_real_data,
        "sharpe_ratio": float(round(strategy_sharpe, 3)),
        "spot_sharpe_ratio": float(round(spot_sharpe, 3)),
        "spot_return_pct": float(round(spot_return_pct, 2)),
    }

    return BacktestResult(equity_curve=equity_curve, trades=trades, summary=summary, scan_logs=scan_logs)


# ── API Endpoints ────────────────────────────────────────────────────

class BacktestV2Request(BaseModel):
    underlying: str = Field(default="BTC")
    start_date: date
    end_date: date
    initial_capital: float = Field(default=100000)
    contract_multiplier: float = Field(default=0.01)
    max_annual_tv_pct: float = Field(default=10.0)
    roll_annual_tv_pct: float = Field(default=8.0)
    min_expiry_months: int = Field(default=12)
    close_days_before: int = Field(default=30)
    quantity: float = Field(default=1.0)
    num_strikes: int = Field(default=15)
    open_interval_days: int = Field(default=30)
    use_real_data: bool = Field(default=True)
    use_hf_data: bool = Field(default=False)


@router.post("/backtest-stream")
async def leaps_v2_stream(req: BacktestV2Request):
    """Run LEAPS终极2.0 backtest with SSE progress streaming."""
    if req.start_date >= req.end_date:
        raise HTTPException(status_code=400, detail="开始日期必须早于结束日期")

    config = LeapsV2Config(
        underlying=req.underlying,
        start_date=req.start_date,
        end_date=req.end_date,
        initial_capital=req.initial_capital,
        contract_multiplier=req.contract_multiplier,
        max_annual_tv_pct=req.max_annual_tv_pct,
        roll_annual_tv_pct=req.roll_annual_tv_pct,
        min_expiry_months=req.min_expiry_months,
        close_days_before=req.close_days_before,
        quantity=req.quantity,
        num_strikes=req.num_strikes,
        open_interval_days=req.open_interval_days,
        use_hf_data=req.use_hf_data,
    )

    async def event_generator():
        try:
            if req.use_real_data:
                price_map = await fetch_deribit_index_prices(
                    req.underlying, req.start_date, req.end_date)
            else:
                price_map = await fetch_okx_prices(
                    req.underlying, req.start_date, req.end_date)
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': f'获取价格数据失败: {str(e)}'})}\n\n"
            return

        if not price_map:
            yield f"data: {json.dumps({'type': 'error', 'message': '无法获取价格数据'})}\n\n"
            return

        total = len(price_map)
        yield f"data: {json.dumps({'type': 'progress', 'day': 0, 'total': total, 'date': req.start_date.isoformat(), 'pct': 0})}\n\n"

        progress_queue = asyncio.Queue()

        async def progress_callback(day_idx, total_days, current_date, status="计算中"):
            await progress_queue.put({
                'type': 'progress', 'day': day_idx, 'total': total_days,
                'date': current_date.isoformat(),
                'pct': round(day_idx / total_days * 100, 1),
                'status': status,
            })

        async def run_bt():
            return await run_leaps_v2(
                price_map=price_map, config=config,
                use_real_data=req.use_real_data,
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
            yield f"data: {json.dumps({'type': 'error', 'message': f'LEAPS2.0回测失败: {str(e)}'})}\n\n"

        yield "data: {\"type\": \"done\"}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")
