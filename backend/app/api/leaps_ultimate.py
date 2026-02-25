"""LEAPS终极 策略回测 API

原理：买入到期日≥1年的深度实值或平值CALL期权，以较低的时间价值成本获取标的资产的长期上涨敞口。
策略核心在于筛选年化时间价值占比低的合约，降低持有成本。
持有至到期前N天平仓，然后滚动到下一个符合条件的长期合约。

合约筛选优先级：
1) 最靠近ATM优先（满足年化TV%阈值的前提下）
2) 年化时间价值%更低作为平局打破条件
3) 到期日更远优先（对比两个到期日时）
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
    last_friday_of_month,
    DERIBIT_BASE,
    RATE_DELAY,
    RISK_FREE_RATE,
)

router = APIRouter(prefix="/api/leaps-ultimate", tags=["leaps-ultimate"])

OKX_BASE = "https://www.okx.com"

# ── Smart contract discovery ─────────────────────────────────────────
# Deribit Options Expiry Schedule (BTC/ETH inverse):
#   Daily:     4 dailies (today + next 3 days) — since Feb 2020
#   Weekly:    3 weeklies (every Friday 08:00 UTC)
#   Monthly:   3 monthlies (last Friday of month)
#   Quarterly: 4 quarterlies (last Friday of Mar/Jun/Sep/Dec)
# Overlap rule: higher timeframe expiry suppresses lower timeframe duplicates.
# For LEAPS (≥12 months), only quarterly expiries matter (monthlies only go ~3 months out).


def get_available_expiries_at_date(today: date) -> List[date]:
    """Return the list of option expiry dates that would have been available
    on Deribit at a given historical date, following Deribit's contract
    introduction policy.

    Returns expiries sorted ascending, including:
    - 3 monthly expiries (last Friday of next 3 months, skipping quarterly overlaps)
    - 4 quarterly expiries (last Friday of Mar/Jun/Sep/Dec)
    """
    quarterly_months = {3, 6, 9, 12}
    expiries = set()

    # ── 4 quarterly expiries (next 4 quarterly dates after today) ──
    quarterly_list = []
    y, m = today.year, today.month
    while len(quarterly_list) < 4:
        # Find next quarterly month
        for qm in sorted(quarterly_months):
            if qm >= m:
                candidate_y = y
                candidate_m = qm
                exp = last_friday_of_month(candidate_y, candidate_m)
                if exp > today and exp not in expiries:
                    quarterly_list.append(exp)
                    expiries.add(exp)
                if len(quarterly_list) >= 4:
                    break
        # Move to next year's first quarter
        y += 1
        m = 1

    # ── 3 monthly expiries (next 3 months, skip if overlaps with quarterly) ──
    monthly_count = 0
    for m_offset in range(1, 12):  # search up to 12 months to find 3 non-overlapping
        ym = today.year
        mm = today.month + m_offset
        while mm > 12:
            mm -= 12
            ym += 1
        exp = last_friday_of_month(ym, mm)
        if exp > today and exp not in expiries:
            expiries.add(exp)
            monthly_count += 1
            if monthly_count >= 3:
                break

    result = sorted(expiries)
    return result


def _find_best_available_expiry_sync(
    today: date,
    min_months: int,
) -> Optional[date]:
    """Find the best available Deribit expiry for LEAPS at a given historical date.

    Simulates which expiries would have been listed on Deribit at `today`,
    then returns the nearest one that is >= min_months away.
    """
    min_target = today + timedelta(days=min_months * 30)
    available = get_available_expiries_at_date(today)

    # First pass: find the nearest expiry >= min_target
    for expiry in available:
        if expiry >= min_target:
            print(f"[LEAPS终极] {today}: Selected expiry {expiry} "
                  f"({(expiry - today).days}d away, >= {min_months}m)")
            return expiry

    # Fallback: return the furthest available expiry
    if available:
        expiry = available[-1]
        print(f"[LEAPS终极] {today}: Fallback expiry {expiry} "
              f"({(expiry - today).days}d away, closest to {min_months}m target)")
        return expiry

    print(f"[LEAPS终极] {today}: No available expiry found")
    return None


# ── Schemas ──────────────────────────────────────────────────────────

class LeapsUltimateConfig(BaseModel):
    """LEAPS终极 策略配置"""
    underlying: str = Field(default="BTC")
    start_date: date
    end_date: date
    initial_capital: float = Field(default=100000, description="初始资金 USD")
    contract_multiplier: float = Field(default=1.0, description="合约乘数")

    # 核心筛选参数
    max_annual_tv_pct: float = Field(default=10.0, description="最大年化时间价值%")
    max_open_annual_tv_pct: float = Field(default=16.0, description="开仓年化TV%限制，超过则保持空仓")
    min_expiry_months: int = Field(default=12, description="最短到期月数")
    close_days_before: int = Field(default=30, description="到期前N天平仓")
    quantity: float = Field(default=1.0, description="每次买入数量")
    num_strikes: int = Field(default=15, description="扫描行权价数量")

    # 开仓频率
    open_interval_days: int = Field(default=30, description="每隔N天检查开仓(月度)")


class TradeRecord(BaseModel):
    date: str
    action: str  # OPEN / CLOSE / ROLL
    strike: float
    expiry: str
    spot: float
    option_price: float
    quantity: float
    intrinsic: float
    time_value: float
    annual_tv_pct: float
    cash_flow: float
    equity_after: float
    data_source: Optional[str] = None
    iv_used: Optional[float] = None
    note: str


class BacktestResult(BaseModel):
    equity_curve: List[dict]
    trades: List[dict]
    summary: dict
    scan_logs: Optional[List[dict]] = None  # per-observation scan details


# ── OKX price fetcher ────────────────────────────────────────────────

async def fetch_okx_prices(underlying: str, start_date: date, end_date: date) -> Dict[date, float]:
    """Fetch daily prices from OKX, using DB cache first."""
    # Try DB cache first
    try:
        from app.api.data_center import get_okx_cached_prices
        cached = get_okx_cached_prices(underlying, start_date, end_date)
        if cached and len(cached) >= (end_date - start_date).days * 0.8:
            print(f"[OKX] Using {len(cached)} cached prices for {underlying}")
            return cached
    except Exception:
        pass

    # Fetch from API
    inst_id = f"{underlying}-USD"
    all_candles = {}
    end_ts = int(datetime.combine(end_date + timedelta(days=1), datetime.min.time(),
                                   tzinfo=timezone.utc).timestamp() * 1000)
    start_ts = int(datetime.combine(start_date, datetime.min.time(),
                                     tzinfo=timezone.utc).timestamp() * 1000)
    current_after = end_ts
    async with create_http_client() as client:
        for _ in range(50):
            url = f"{OKX_BASE}/api/v5/market/history-index-candles"
            params = {"instId": inst_id, "bar": "1D", "limit": "100", "after": str(current_after)}
            resp = await client.get(url, params=params, headers={"User-Agent": "LeapsUltimate/1.0"})
            if resp.status_code == 429:
                await asyncio.sleep(1.0)
                resp = await client.get(url, params=params, headers={"User-Agent": "LeapsUltimate/1.0"})
            resp.raise_for_status()
            data = resp.json()
            if data.get("code") != "0" or not data.get("data"):
                break
            for c in data["data"]:
                ts = int(c[0])
                if ts < start_ts:
                    continue
                dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).date()
                all_candles[dt] = float(c[4])  # close price
            oldest_ts = int(data["data"][-1][0])
            if oldest_ts <= start_ts:
                break
            current_after = oldest_ts
            await asyncio.sleep(0.25)

    # Save to OKX cache for future use
    try:
        from app.api.data_center import _fetch_okx_prices_with_cache
        await _fetch_okx_prices_with_cache(underlying, start_date, end_date)
    except Exception:
        pass

    return all_candles


# ── Core backtest engine ─────────────────────────────────────────────

async def run_leaps_ultimate(
    price_map: Dict[date, float],
    config: LeapsUltimateConfig,
    use_real_data: bool = True,
    progress_callback=None,
) -> BacktestResult:
    """Run LEAPS终极 strategy backtest.

    每月开仓日，扫描所有到期日≥最短月数的CALL期权。
    对每个候选合约计算年化时间价值%，选择最低且满足阈值的合约买入。
    持有至到期前N天平仓，然后滚动到下一个符合条件的长期合约。
    """
    dates = sorted(price_map.keys())
    if not dates:
        raise HTTPException(status_code=400, detail="No price data")

    underlying = config.underlying
    mult = config.contract_multiplier
    qty = abs(config.quantity)
    total_days = len(dates)
    _total_observe = [total_days]  # mutable container for closure

    cash = config.initial_capital
    position = None  # single position: {strike, expiry, quantity, open_price, open_date, open_spot, ...}
    trades = []
    equity_curve = []

    scan_logs = []  # record all scan details for inspection

    async def _progress(day_idx, today, status="计算中"):
        if progress_callback:
            await progress_callback(day_idx, _total_observe[0], today, status)

    async with create_http_client() as client:

        async def _get_price(expiry, strike, spot, today):
            """Get option price — real data or BS model."""
            if use_real_data:
                price, src, iv, _ = await _fetch_single_strike_price_fast(
                    client, underlying, expiry, strike, spot, "CALL", today)
                return float(price), src, iv
            else:
                T = calculate_time_to_expiration(expiry, today)
                if T <= 0.0001:
                    return float(max(0, spot - strike)), "intrinsic", None
                default_iv = 0.6
                price = black_scholes_price(spot, strike, T, RISK_FREE_RATE, default_iv, "CALL")
                return float(price), "model", float(default_iv)

        async def _scan_best_strike(today, spot, expiry):
            """Scan strikes to find the best LEAPS CALL contract.

            Scan from ATM downward (deeper ITM). Among candidates whose
            annual_tv_pct is below the threshold, pick the one closest to ATM.
            Ties broken by lower annual_tv_pct.
            Fallback: if none meet threshold, pick lowest TV% ITM.

            Returns (best_tuple, scanned_list) where:
              best_tuple = (strike, price, iv, src, annual_tv_pct, intrinsic, tv) or None
              scanned_list = [{strike, price, iv, src, intrinsic, tv, annual_tv_pct, instrument, selected}, ...]
            """
            step = get_strike_step(underlying, spot)
            atm = find_nearest_strike(spot, step)
            days_to_exp = max((expiry - today).days, 1)

            # Scan from ATM downward (ITM for CALL: lower strikes)
            candidates = []
            for i in range(config.num_strikes + 1):
                s = atm - i * step
                if s > 0:
                    candidates.append(s)

            best = None  # (strike, price, iv, src, annual_tv_pct, intrinsic, tv)
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

                print(f"[LEAPS终极] Scan K={strike}, price={price:.2f}, "
                      f"intrinsic={intrinsic:.2f}, TV={tv:.2f}, "
                      f"annual_TV%={annual_tv_pct:.2f}%")

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
                        # Closer to ATM wins; if same distance, lower TV% wins
                        if dist < best_dist or (dist == best_dist and annual_tv_pct < best[4]):
                            best = (strike, price, iv, src, annual_tv_pct, intrinsic, tv)
                    entry["note"] = "满足阈值"
                else:
                    entry["note"] = f"年化TV%={annual_tv_pct:.2f}% > {config.max_annual_tv_pct}%"

                scanned.append(entry)

            # Fallback: if no candidate meets the TV% threshold,
            # pick the ITM candidate with the lowest annual TV% (never stay empty)
            fallback_used = False
            if best is None:
                # Prefer ITM (strike <= spot for CALL), lowest annual_tv_pct
                fb_pool = [
                    e for e in scanned
                    if isinstance(e.get("annual_tv_pct"), (int, float))
                    and e["price"] > 0 and e["strike"] <= spot
                ]
                if not fb_pool:
                    # No ITM with data — use any candidate with data
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

            # Mark the selected one
            if best is not None:
                for s in scanned:
                    if abs(s["strike"] - best[0]) < 1.0:
                        s["selected"] = True
                        if fallback_used:
                            s["note"] = "✓ 选中(回退: 最低年化TV% ITM)"
                        else:
                            s["note"] = "✓ 选中(最靠近ATM)"

            return best, scanned

        # Build observation dates: every N days instead of every day
        observe_interval = config.open_interval_days
        observe_dates = []
        if dates:
            observe_dates.append(dates[0])  # always include first date
            last_observe = dates[0]
            for d in dates[1:]:
                if (d - last_observe).days >= observe_interval:
                    observe_dates.append(d)
                    last_observe = d
            # Always include last date for final settlement
            if observe_dates[-1] != dates[-1]:
                observe_dates.append(dates[-1])

        total_observe = len(observe_dates)
        _total_observe[0] = total_observe
        print(f"[LEAPS终极] {len(dates)} trading days → {total_observe} observation points (every {observe_interval}d)")

        for day_idx, today in enumerate(observe_dates):
            spot = price_map[today]
            await _progress(day_idx, today)

            # ── 1) Check if current position needs closing (approaching expiry) ──
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

                    print(f"[LEAPS终极] CLOSE K={position['strike']}, "
                          f"pnl={pnl:.2f}, days_left={days_left}")
                    position = None

            # ── 2) Check if we should open a new position ──
            if position is None and cash > 0:
                await _progress(day_idx, today, "查找可用合约")
                print(f"[LEAPS终极] {today}: 尝试开仓, cash={cash:.2f}, position=None")

                # Smart expiry discovery: find actual Deribit expiry that exists
                expiry = _find_best_available_expiry_sync(
                    today, config.min_expiry_months)
                print(f"[LEAPS终极] {today}: expiry={expiry}")

                if expiry is not None:
                    T = calculate_time_to_expiration(expiry, today)
                    if T > 0.1:
                        await _progress(day_idx, today,
                                        f"扫描 expiry={expiry} ({(expiry-today).days}天)")
                        result, scanned1 = await _scan_best_strike(today, spot, expiry)
                        print(f"[LEAPS终极] {today}: scan result={'found K='+str(result[0]) if result else 'None'}, "
                              f"scanned={len(scanned1)} strikes")

                        # Record scan log for this expiry
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

                        # Also try the next available expiry for comparison
                        # (further expiry may have lower annual TV%)
                        next_min_months = config.min_expiry_months + 3
                        next_expiry = _find_best_available_expiry_sync(
                            today, next_min_months)
                        best_overall = None

                        if result is not None:
                            strike, price, iv, src, atv, intr, tv = result
                            best_overall = (strike, price, iv, src, atv, intr, tv, expiry)

                        scanned2 = []
                        if next_expiry is not None and next_expiry != expiry:
                            T2 = calculate_time_to_expiration(next_expiry, today)
                            if T2 > 0.1:
                                await _progress(day_idx, today,
                                                f"对比 expiry={next_expiry}")
                                result2, scanned2 = await _scan_best_strike(today, spot, next_expiry)
                                if result2 is not None:
                                    s2, p2, iv2, src2, atv2, intr2, tv2 = result2
                                    # Compare by distance to ATM (closest wins)
                                    if best_overall is None:
                                        best_overall = (s2, p2, iv2, src2, atv2, intr2, tv2, next_expiry)
                                    else:
                                        dist2 = abs(s2 - spot)
                                        dist1 = abs(best_overall[0] - spot)
                                        if dist2 < dist1 or (dist2 == dist1 and atv2 < best_overall[4]):
                                            best_overall = (s2, p2, iv2, src2, atv2, intr2, tv2, next_expiry)

                        # Add second expiry scan to log if exists
                        if scanned2:
                            scan_entry["expiry2"] = next_expiry.isoformat() if next_expiry else None
                            scan_entry["days_to_expiry2"] = (next_expiry - today).days if next_expiry else None
                            scan_entry["candidates2"] = scanned2

                        if best_overall is not None:
                            strike, price, iv, src, atv, intr, tv, expiry = best_overall
                            # 检查是否超过开仓年化TV%限制
                            if atv > config.max_open_annual_tv_pct:
                                scan_entry["selected_strike"] = float(round(strike, 2))
                                scan_entry["selected_expiry"] = expiry.isoformat()
                                scan_entry["result"] = (
                                    f"年化TV%={atv:.2f}% > 开仓限制{config.max_open_annual_tv_pct}%, 保持空仓"
                                )
                                print(f"[LEAPS终极] {today}: K={strike} 年化TV%={atv:.2f}% "
                                      f"> 限制{config.max_open_annual_tv_pct}%, 跳过开仓")
                            else:
                                scan_entry["selected_strike"] = float(round(strike, 2))
                                scan_entry["selected_expiry"] = expiry.isoformat()
                                cost = price * qty * mult
                                print(f"[LEAPS终极] {today}: 选中 K={strike}, price={price:.2f}, "
                                      f"cost={cost:.2f}, cash={cash:.2f}, qty={qty}, mult={mult}")
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

                                    print(f"[LEAPS终极] OPEN K={strike}, spot={spot:.0f}, "
                                          f"price={price:.2f}, annual_TV%={atv:.2f}%, "
                                          f"expiry={expiry}")
                                else:
                                    scan_entry["result"] = f"资金不足(需${cost:.2f}, 有${cash:.2f})"
                                    print(f"[LEAPS终极] 资金不足: need ${cost:.2f}, have ${cash:.2f}, "
                                          f"price={price:.2f}, qty={qty}, mult={mult}")
                        else:
                            scan_entry["result"] = "未找到满足条件的合约"
                            print(f"[LEAPS终极] {today}: 未找到满足条件的合约")

                        scan_logs.append(scan_entry)
                    else:
                        scan_logs.append({
                            "date": today.isoformat(), "spot": float(round(spot, 2)),
                            "expiry": expiry.isoformat() if expiry else None,
                            "days_to_expiry": (expiry - today).days if expiry else 0,
                            "candidates": [], "selected_strike": None,
                            "selected_expiry": None,
                            "result": f"到期日太近(T={T:.3f}年)",
                        })
                else:
                    scan_logs.append({
                        "date": today.isoformat(), "spot": float(round(spot, 2)),
                        "expiry": None, "days_to_expiry": 0,
                        "candidates": [], "selected_strike": None,
                        "selected_expiry": None,
                        "result": "无可用到期日",
                    })
                    print(f"[LEAPS终极] {today}: 无可用到期日")
            elif position is not None:
                # Record that we're holding — no scan needed
                days_left = (position["expiry"] - today).days
                scan_logs.append({
                    "date": today.isoformat(), "spot": float(round(spot, 2)),
                    "expiry": position["expiry"].isoformat(),
                    "days_to_expiry": days_left,
                    "candidates": [], "selected_strike": None,
                    "selected_expiry": None,
                    "result": f"持仓中(K={position['strike']}, 剩余{days_left}天)",
                })

            # ── 3) Mark-to-market ──
            holdings = 0.0
            mtm_price_val = 0.0
            if position is not None:
                T = calculate_time_to_expiration(position["expiry"], today)
                if T > 0.0001:
                    iv_mtm = position.get("iv_used") or 0.6
                    mtm_price_val = black_scholes_price(spot, position["strike"], T,
                                                         RISK_FREE_RATE, iv_mtm, "CALL")
                    holdings = float(mtm_price_val * position["quantity"] * mult)
                else:
                    mtm_price_val = float(max(0, spot - position["strike"]))
                    holdings = float(mtm_price_val * position["quantity"] * mult)

            total_equity = cash + holdings
            capital_usage_pct = round(holdings / total_equity * 100, 1) if total_equity > 0 else 0.0

            equity_curve.append({
                "date": today.isoformat(),
                "equity": float(round(total_equity, 2)),
                "spot": float(spot),
                "cash": float(round(cash, 2)),
                "holdings": float(round(holdings, 2)),
                "has_position": position is not None,
                "capital_usage_pct": capital_usage_pct,
            })

            # ── 4) Generate MTM trade record (skip open/close day) ──
            if position is not None and position["open_date"] != today:
                pnl_mtm = (mtm_price_val - position["open_price"]) * position["quantity"] * mult
                pnl_pct_mtm = (mtm_price_val / position["open_price"] - 1) * 100 if position["open_price"] > 0 else 0
                intrinsic_mtm = max(0.0, spot - position["strike"])
                tv_mtm = max(0.0, mtm_price_val - intrinsic_mtm)
                days_left_mtm = max((position["expiry"] - today).days, 1)
                days_held_mtm = (today - position["open_date"]).days
                annual_tv_mtm = (tv_mtm / position["strike"]) * (365.0 / days_left_mtm) * 100.0

                trades.append(TradeRecord(
                    date=today.isoformat(), action="MTM",
                    strike=float(round(position["strike"], 2)),
                    expiry=position["expiry"].isoformat(),
                    spot=float(round(spot, 2)),
                    option_price=float(round(mtm_price_val, 2)),
                    quantity=float(position["quantity"]),
                    intrinsic=float(round(intrinsic_mtm, 2)),
                    time_value=float(round(tv_mtm, 2)),
                    annual_tv_pct=float(round(annual_tv_mtm, 2)),
                    cash_flow=0,
                    equity_after=float(round(cash + holdings, 2)),
                    data_source=position.get("data_source"),
                    iv_used=float(round(position.get("iv_used") or 0.6, 4)),
                    note=f"持仓{days_held_mtm}天, 剩余{days_left_mtm}天, "
                         f"PnL=${pnl_mtm:.2f}({pnl_pct_mtm:+.1f}%), "
                         f"年化TV%={annual_tv_mtm:.2f}%",
                ).model_dump())

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

            # Update the last equity_curve point to reflect forced close
            if equity_curve and equity_curve[-1]["date"] == last_date.isoformat():
                equity_curve[-1]["equity"] = float(round(cash, 2))
                equity_curve[-1]["cash"] = float(round(cash, 2))
                equity_curve[-1]["holdings"] = 0.0
                equity_curve[-1]["has_position"] = False
                equity_curve[-1]["capital_usage_pct"] = 0.0

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
    mtm_count = sum(1 for t in trades if t.get("action") == "MTM")

    # ── Sharpe ratio calculation ──
    # Observation interval = open_interval_days, annualization factor = 365 / interval
    ann_factor = 365.0 / max(config.open_interval_days, 1)

    # Strategy returns (period-over-period from equity curve)
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

    # Spot returns (as if holding spot with initial_capital)
    spot_sharpe = 0.0
    spot_return_pct = 0.0
    if len(equity_curve) >= 2:
        first_spot = equity_curve[0]["spot"]
        last_spot = equity_curve[-1]["spot"]
        if first_spot > 0:
            spot_return_pct = (last_spot / first_spot - 1.0) * 100.0

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
        "total_trades": sum(1 for t in trades if t.get("action") != "MTM"),
        "mtm_count": mtm_count,
        "open_count": open_count,
        "close_count": close_count,
        "backtest_days": days,
        "use_real_data": use_real_data,
        "sharpe_ratio": float(round(strategy_sharpe, 3)),
        "spot_sharpe_ratio": float(round(spot_sharpe, 3)),
        "spot_return_pct": float(round(spot_return_pct, 2)),
    }

    return BacktestResult(equity_curve=equity_curve, trades=trades, summary=summary, scan_logs=scan_logs)


# ── API Endpoints ────────────────────────────────────────────────────

class BacktestRequest(BaseModel):
    """Request body for backtest endpoint."""
    underlying: str = Field(default="BTC")
    start_date: date
    end_date: date
    initial_capital: float = Field(default=100000)
    contract_multiplier: float = Field(default=1.0)
    max_annual_tv_pct: float = Field(default=10.0)
    max_open_annual_tv_pct: float = Field(default=16.0)
    min_expiry_months: int = Field(default=12)
    close_days_before: int = Field(default=30)
    quantity: float = Field(default=1.0)
    num_strikes: int = Field(default=15)
    open_interval_days: int = Field(default=30)
    use_real_data: bool = Field(default=True)


@router.post("/backtest-stream")
async def leaps_ultimate_stream(req: BacktestRequest):
    """Run LEAPS终极 backtest with SSE progress streaming."""
    if req.start_date >= req.end_date:
        raise HTTPException(status_code=400, detail="开始日期必须早于结束日期")

    config = LeapsUltimateConfig(
        underlying=req.underlying,
        start_date=req.start_date,
        end_date=req.end_date,
        initial_capital=req.initial_capital,
        contract_multiplier=req.contract_multiplier,
        max_annual_tv_pct=req.max_annual_tv_pct,
        max_open_annual_tv_pct=req.max_open_annual_tv_pct,
        min_expiry_months=req.min_expiry_months,
        close_days_before=req.close_days_before,
        quantity=req.quantity,
        num_strikes=req.num_strikes,
        open_interval_days=req.open_interval_days,
    )

    async def event_generator():
        try:
            # Fetch prices
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
            return await run_leaps_ultimate(
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
            yield f"data: {json.dumps({'type': 'error', 'message': f'LEAPS终极回测失败: {str(e)}'})}\n\n"

        yield "data: {\"type\": \"done\"}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


# ── Live scan endpoint ───────────────────────────────────────────────

class LiveScanRequest(BaseModel):
    underlying: str = Field(default="BTC")
    max_annual_tv_pct: float = Field(default=10.0)
    min_expiry_months: int = Field(default=12)
    num_strikes: int = Field(default=15)


@router.post("/live-scan")
async def leaps_live_scan(req: LiveScanRequest):
    """Scan current market for the best LEAPS contract to hold today."""
    today = date.today()

    async with create_http_client() as client:
        # 1) Get live spot price from Deribit
        try:
            index_name = f"{req.underlying.lower()}_usd"
            resp = await client.get(
                f"{DERIBIT_BASE}/public/get_index_price",
                params={"index_name": index_name},
            )
            resp.raise_for_status()
            data = resp.json()
            spot = float(data["result"]["index_price"])
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"获取实时价格失败: {e}")

        # 2) Get available expiries at today
        available = get_available_expiries_at_date(today)

        # 3) Find best expiry
        expiry = _find_best_available_expiry_sync(today, req.min_expiry_months)
        if expiry is None:
            return {"error": "无可用到期日", "spot": float(spot), "date": today.isoformat()}

        T = calculate_time_to_expiration(expiry, today)
        if T <= 0.1:
            return {"error": "到期日太近", "spot": float(spot), "date": today.isoformat()}

        # 4) Scan strikes (use real data)
        step = get_strike_step(req.underlying, spot)
        atm = find_nearest_strike(spot, step)
        days_to_exp = max((expiry - today).days, 1)

        # Scan from ATM downward (ITM for CALL)
        candidates = []
        for i in range(req.num_strikes + 1):
            s = atm - i * step
            if s > 0:
                candidates.append(s)

        scanned = []
        best = None
        for strike in candidates:
            instrument = build_instrument_name(req.underlying, expiry, strike, "CALL")
            try:
                price, src, iv, _ = await _fetch_single_strike_price_fast(
                    client, req.underlying, expiry, strike, spot, "CALL", today)
                price = float(price)
            except Exception:
                price, src, iv = 0.0, "error", None

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

            if annual_tv_pct < req.max_annual_tv_pct:
                dist = abs(strike - spot)
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

        # Fallback: lowest TV% ITM
        fallback_used = False
        if best is None:
            fb_pool = [e for e in scanned if isinstance(e.get("annual_tv_pct"), (int, float))
                       and e["price"] > 0 and e["strike"] <= spot]
            if not fb_pool:
                fb_pool = [e for e in scanned if isinstance(e.get("annual_tv_pct"), (int, float))
                           and e["price"] > 0]
            if fb_pool:
                fb_pool.sort(key=lambda x: x["annual_tv_pct"])
                best = fb_pool[0]
                fallback_used = True

        if best is not None:
            for s in scanned:
                if abs(s["strike"] - best["strike"]) < 1.0:
                    s["selected"] = True
                    s["note"] = "✓ 推荐持仓" + ("(回退)" if fallback_used else "")

        # Also scan next expiry for comparison
        next_expiry = _find_best_available_expiry_sync(today, req.min_expiry_months + 3)
        scanned2 = []
        if next_expiry and next_expiry != expiry:
            T2 = calculate_time_to_expiration(next_expiry, today)
            if T2 > 0.1:
                days2 = max((next_expiry - today).days, 1)
                for strike in candidates:
                    instrument = build_instrument_name(req.underlying, next_expiry, strike, "CALL")
                    try:
                        price, src, iv, _ = await _fetch_single_strike_price_fast(
                            client, req.underlying, next_expiry, strike, spot, "CALL", today)
                        price = float(price)
                    except Exception:
                        price, src, iv = 0.0, "error", None
                    if price <= 0:
                        scanned2.append({
                            "strike": float(strike), "instrument": instrument,
                            "price": 0, "iv": None, "data_source": src,
                            "intrinsic": 0, "time_value": 0, "annual_tv_pct": None,
                            "selected": False, "note": "无数据",
                        })
                        continue
                    intrinsic = max(0.0, spot - strike)
                    tv = max(0.0, price - intrinsic)
                    atv = (tv / strike) * (365.0 / days2) * 100.0
                    scanned2.append({
                        "strike": float(round(strike, 2)),
                        "instrument": instrument,
                        "price": float(round(price, 2)),
                        "iv": float(round(iv, 4)) if iv else None,
                        "data_source": src,
                        "intrinsic": float(round(intrinsic, 2)),
                        "time_value": float(round(tv, 2)),
                        "annual_tv_pct": float(round(atv, 2)),
                        "selected": False,
                        "note": "满足阈值" if atv < req.max_annual_tv_pct else f"TV%={atv:.2f}%>{req.max_annual_tv_pct}%",
                    })

        return {
            "date": today.isoformat(),
            "spot": float(round(spot, 2)),
            "underlying": req.underlying,
            "available_expiries": [e.isoformat() for e in available],
            "expiry1": expiry.isoformat(),
            "days_to_expiry1": (expiry - today).days,
            "candidates1": scanned,
            "expiry2": next_expiry.isoformat() if next_expiry and next_expiry != expiry else None,
            "days_to_expiry2": (next_expiry - today).days if next_expiry and next_expiry != expiry else None,
            "candidates2": scanned2 if scanned2 else None,
            "recommended": best,
            "fallback_used": fallback_used,
        }
