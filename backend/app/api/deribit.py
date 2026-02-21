"""Real data backtest using Deribit historical IV smile interpolation.
All fetched data is cached in SQLite so repeated backtests don't re-call the API."""
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from typing import List, Optional, Tuple, Dict
from datetime import datetime, date, timedelta, timezone
import httpx
import asyncio
import calendar
import json
import numpy as np

from app.services.pricing import (
    black_scholes_price,
    calculate_time_to_expiration,
    implied_volatility,
)
from app.core.database import SessionLocal
from app.core.config import create_http_client
from app.models.deribit_cache import DeribitPriceCache, DeribitIVCache

router = APIRouter(prefix="/api/deribit", tags=["deribit"])

DERIBIT_BASE = "https://www.deribit.com/api/v2"
RATE_DELAY = 0.3
RISK_FREE_RATE = 0.05
MAX_RETRY = 3  # max retries before marking as no-data


# ── Pydantic Models ─────────────────────────────────────────────────────────

class RealBacktestLeg(BaseModel):
    option_type: str = Field(..., pattern="^(PUT|CALL)$")
    strike_offset_pct: float = Field(...)
    quantity: float = Field(...)
    expiry_months: int = Field(default=1)


class RealBacktestRequest(BaseModel):
    underlying: str = Field(default="BTC")
    start_date: date
    end_date: date
    roll_day: int = Field(default=1)
    close_days_before_expiry: int = Field(default=1)
    initial_capital: float = Field(default=10000.0)
    contract_multiplier: float = Field(default=1.0)
    legs: List[RealBacktestLeg] = Field(default=[])
    # Martingale mode
    martingale: bool = Field(default=False)
    max_double_times: int = Field(default=3)  # max consecutive doublings
    # Enhanced martingale mode
    enhanced_martingale: bool = Field(default=False)
    enhanced_martingale_recover_pct: float = Field(default=1.1)  # target recover ratio (1.1 = 110%)
    enhanced_max_multiplier: float = Field(default=10.0)  # max multiplier cap
    # Volatility strategy mode
    vol_strategy: bool = Field(default=False)
    vol_sell_iv: float = Field(default=80.0)  # sell ATM PUT when IV > this (in %)
    vol_buy_iv: float = Field(default=40.0)   # buy ATM PUT when IV < this (in %)
    vol_quantity: float = Field(default=1.0)   # base quantity (positive number, sign determined by action)
    vol_strike_offset_pct: float = Field(default=0.0)  # 0 = ATM
    # Roll PUT strategy mode
    roll_put_strategy: bool = Field(default=False)
    roll_put_offset_pct: float = Field(default=10.0)   # sell PUT at spot * (1 - this/100)
    roll_put_quantity: float = Field(default=1.0)       # quantity to sell (positive, will be negated)
    # Hedge PUT strategy mode (like Roll PUT but buys PUT on crash instead of rolling to far month)
    hedge_put_strategy: bool = Field(default=False)
    hedge_put_offset_pct: float = Field(default=10.0)   # sell PUT at spot * (1 - this/100)
    hedge_put_quantity: float = Field(default=1.0)       # quantity to sell (positive, will be negated)
    hedge_put_crash_pct: float = Field(default=20.0)     # if spot drops > this %, switch to buying PUT
    hedge_put_hedge_quantity: float = Field(default=1.0)  # quantity of hedge PUT to buy
    # Channel strategy mode (sell strangle based on rolling 3-month high/low)
    channel_strategy: bool = Field(default=False)
    channel_lookback_days: int = Field(default=90)        # rolling window in days (default 90 = ~3 months)
    channel_quantity: float = Field(default=1.0)           # quantity to sell (positive, will be negated)
    # Wheel strategy (飞轮策略): sell PUT → assigned → sell CALL → assigned → repeat
    wheel_strategy: bool = Field(default=False)
    wheel_put_offset_pct: float = Field(default=5.0)      # sell PUT at spot * (1 - this/100)
    wheel_call_offset_pct: float = Field(default=5.0)     # sell CALL at spot * (1 + this/100)
    wheel_quantity: float = Field(default=1.0)             # quantity per leg (positive, will be negated for selling)
    wheel_reinvest: bool = Field(default=True)             # reinvest premium into next cycle
    # Grid strategy (网格策略): sell PUT at each grid level where annualized yield > threshold
    grid_strategy: bool = Field(default=False)
    grid_step: float = Field(default=100.0)               # grid spacing in USD (e.g. 100 for ETH)
    grid_quantity: float = Field(default=1.0)              # quantity per grid level
    grid_min_yield_pct: float = Field(default=10.0)       # minimum annualized yield % to open position
    grid_range_up: int = Field(default=5)                  # number of grid levels above spot
    grid_range_down: int = Field(default=5)                # number of grid levels below spot
    grid_max_positions: int = Field(default=10)            # max total concurrent grid positions (PUT + CALL)
    # LEAPS strategy (长期期权策略): buy deep ITM/ATM CALL with ≥1 year expiry, low time value
    leaps_strategy: bool = Field(default=False)
    leaps_max_annual_tv_pct: float = Field(default=10.0)   # max annualized time_value/strike % to accept
    leaps_min_months: int = Field(default=12)              # minimum months to expiry (default 12 = 1 year)
    leaps_close_days_before: int = Field(default=30)       # close N days before expiry (default 30 = 1 month)
    leaps_quantity: float = Field(default=1.0)             # quantity to buy (positive)
    leaps_num_strikes: int = Field(default=15)             # number of strikes to scan around ATM


class RealTradeRecord(BaseModel):
    open_date: str
    close_date: str
    option_type: str
    strike: float
    quantity: float
    open_price: float
    close_price: float
    pnl: float
    close_reason: str
    instrument: str
    data_source: str  # "iv_smile" or "model"
    iv_used: Optional[float] = None
    open_spot: Optional[float] = None
    close_spot: Optional[float] = None
    strike_distance_pct: Optional[float] = None  # (spot - strike) / strike * 100 at open
    equity_after: Optional[float] = None  # equity after this trade closed


class RealBacktestResult(BaseModel):
    equity_curve: List[dict]
    trades: List[dict]
    summary: dict
    iv_smiles: List[dict]  # per-trade IV smile snapshots


# ── Deribit date / instrument helpers ───────────────────────────────────────

MONTH_ABBR = {
    1: "JAN", 2: "FEB", 3: "MAR", 4: "APR", 5: "MAY", 6: "JUN",
    7: "JUL", 8: "AUG", 9: "SEP", 10: "OCT", 11: "NOV", 12: "DEC",
}


def last_friday_of_month(year: int, month: int) -> date:
    last_day = calendar.monthrange(year, month)[1]
    d = date(year, month, last_day)
    while d.weekday() != 4:
        d -= timedelta(days=1)
    return d


def find_deribit_expiry(open_date: date, months_ahead: int) -> date:
    year = open_date.year
    month = open_date.month + months_ahead
    while month > 12:
        month -= 12
        year += 1
    return last_friday_of_month(year, month)


def find_deribit_quarterly_expiries(from_date: date, max_years_ahead: int = 3) -> List[date]:
    """Generate Deribit quarterly expiry dates (last Friday of Mar/Jun/Sep/Dec)
    starting from from_date, looking up to max_years_ahead into the future.
    Deribit long-dated options only exist on quarterly expiries."""
    quarterly_months = [3, 6, 9, 12]
    expiries = []
    for y in range(from_date.year, from_date.year + max_years_ahead + 1):
        for m in quarterly_months:
            exp = last_friday_of_month(y, m)
            if exp > from_date:
                expiries.append(exp)
    expiries.sort()
    return expiries


def format_deribit_date(d: date) -> str:
    return f"{d.day}{MONTH_ABBR[d.month]}{str(d.year)[2:]}"


def build_instrument_name(underlying: str, expiry: date, strike: float, option_type: str) -> str:
    strike_int = int(round(strike))
    suffix = "P" if option_type.upper() == "PUT" else "C"
    return f"{underlying}-{format_deribit_date(expiry)}-{strike_int}-{suffix}"


def find_nearest_strike(target: float, step: int = 1000) -> float:
    return round(target / step) * step


def get_strike_step(underlying: str, spot: float) -> int:
    """Get the appropriate strike step for a given underlying and spot price.
    Deribit uses different strike increments for BTC vs ETH."""
    if underlying.upper() == "ETH":
        if spot > 5000:
            return 100
        elif spot > 2000:
            return 50
        else:
            return 25
    else:  # BTC
        if spot > 50000:
            return 1000
        elif spot > 10000:
            return 500
        else:
            return 250


# ── DB Cache helpers ────────────────────────────────────────────────────────

def get_cached_prices(underlying: str, start_date: date, end_date: date) -> Dict[date, float]:
    """Load cached daily prices from DB."""
    db = SessionLocal()
    try:
        rows = db.query(DeribitPriceCache).filter(
            DeribitPriceCache.underlying == underlying,
            DeribitPriceCache.trade_date >= start_date,
            DeribitPriceCache.trade_date <= end_date,
        ).all()
        return {r.trade_date: r.close_price for r in rows}
    finally:
        db.close()


def save_cached_prices(underlying: str, price_map: Dict[date, float]):
    """Save daily prices to DB cache (skip existing)."""
    db = SessionLocal()
    try:
        for d, price in price_map.items():
            exists = db.query(DeribitPriceCache).filter(
                DeribitPriceCache.underlying == underlying,
                DeribitPriceCache.trade_date == d,
            ).first()
            if not exists:
                db.add(DeribitPriceCache(
                    underlying=underlying, trade_date=d, close_price=price
                ))
        db.commit()
    finally:
        db.close()


def get_cached_iv_smile(
    underlying: str, expiry: date, option_type: str, target_date: date
) -> List[Tuple[float, float, float]]:
    """Load cached IV smile from DB. Returns list of (strike, iv, trade_price_usd).
    Only returns real data (strike > 0), never sentinel rows."""
    db = SessionLocal()
    try:
        rows = db.query(DeribitIVCache).filter(
            DeribitIVCache.underlying == underlying,
            DeribitIVCache.expiry_date == expiry,
            DeribitIVCache.option_type == option_type,
            DeribitIVCache.target_date == target_date,
            DeribitIVCache.strike > 0,
        ).all()
        return [(r.strike, r.iv, r.trade_price_usd) for r in rows]
    finally:
        db.close()


def is_cached_no_data(
    underlying: str, expiry: date, option_type: str, target_date: date
) -> bool:
    """Check if we previously cached a 'no data' sentinel for this combo.
    Sentinel = row with strike == -1."""
    db = SessionLocal()
    try:
        row = db.query(DeribitIVCache).filter(
            DeribitIVCache.underlying == underlying,
            DeribitIVCache.expiry_date == expiry,
            DeribitIVCache.option_type == option_type,
            DeribitIVCache.target_date == target_date,
            DeribitIVCache.strike == -1,
        ).first()
        return row is not None
    finally:
        db.close()


def save_no_data_sentinel(
    underlying: str, expiry: date, option_type: str, target_date: date, spot: float
):
    """Save a 'no data' sentinel so we don't re-fetch this combo."""
    db = SessionLocal()
    try:
        exists = db.query(DeribitIVCache).filter(
            DeribitIVCache.underlying == underlying,
            DeribitIVCache.expiry_date == expiry,
            DeribitIVCache.option_type == option_type,
            DeribitIVCache.target_date == target_date,
            DeribitIVCache.strike == -1,
        ).first()
        if not exists:
            db.add(DeribitIVCache(
                underlying=underlying, expiry_date=expiry,
                option_type=option_type, target_date=target_date,
                spot_price=spot, strike=-1, iv=0,
                trade_price_usd=0, instrument="NO_DATA_SENTINEL",
            ))
            db.commit()
    finally:
        db.close()


def save_cached_iv_smile(
    underlying: str, expiry: date, option_type: str, target_date: date,
    spot: float, points: List[Tuple[float, float, float, str]],
):
    """Save IV smile points to DB. points = [(strike, iv, trade_price_usd, instrument), ...]"""
    db = SessionLocal()
    try:
        for strike, iv, price_usd, instrument in points:
            exists = db.query(DeribitIVCache).filter(
                DeribitIVCache.underlying == underlying,
                DeribitIVCache.expiry_date == expiry,
                DeribitIVCache.option_type == option_type,
                DeribitIVCache.target_date == target_date,
                DeribitIVCache.strike == strike,
            ).first()
            if not exists:
                db.add(DeribitIVCache(
                    underlying=underlying, expiry_date=expiry,
                    option_type=option_type, target_date=target_date,
                    spot_price=spot, strike=strike, iv=iv,
                    trade_price_usd=price_usd, instrument=instrument,
                ))
        db.commit()
    finally:
        db.close()


# ── Deribit API fetchers ────────────────────────────────────────────────────

async def fetch_deribit_index_prices(
    underlying: str, start_date: date, end_date: date,
    force: bool = False,
) -> Dict[date, float]:
    """Fetch daily index prices, using DB cache first.
    Falls back to cache if API fails (network error etc)."""
    # 1) Check cache
    cached = get_cached_prices(underlying, start_date, end_date)
    if not force:
        expected_days = (end_date - start_date).days
        if len(cached) >= expected_days * 0.8:
            print(f"[Deribit] Using {len(cached)} cached prices for {underlying}")
            return cached

    # 2) Fetch from API
    instrument = f"{underlying}-PERPETUAL"
    start_ts = int(datetime.combine(start_date, datetime.min.time(),
                                     tzinfo=timezone.utc).timestamp() * 1000)
    end_ts = int(datetime.combine(end_date + timedelta(days=1), datetime.min.time(),
                                   tzinfo=timezone.utc).timestamp() * 1000)

    url = f"{DERIBIT_BASE}/public/get_tradingview_chart_data"
    params = {
        "instrument_name": instrument,
        "start_timestamp": start_ts,
        "end_timestamp": end_ts,
        "resolution": "1D",
    }

    try:
        async with create_http_client() as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        print(f"[Deribit] API error fetching prices: {e}")
        if cached:
            print(f"[Deribit] Falling back to {len(cached)} cached prices")
            return cached
        raise

    result = data.get("result", {})
    ticks = result.get("ticks", [])
    closes = result.get("close", [])

    price_map: Dict[date, float] = {}
    for ts, close in zip(ticks, closes):
        dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).date()
        price_map[dt] = close

    print(f"[Deribit] Fetched {len(price_map)} daily prices from API for {instrument}")

    # 3) Save to cache
    save_cached_prices(underlying, price_map)

    # Merge with existing cache (API might not cover full range)
    for d, p in cached.items():
        if d not in price_map:
            price_map[d] = p

    return price_map


async def fetch_trades_for_instrument(
    client: httpx.AsyncClient,
    instrument: str,
    target_date: date,
    window_days: int = 5,
) -> Optional[Dict]:
    """Fetch trades for an instrument around a target date.
    First tries get_last_trades_by_instrument_and_time (best for recent data),
    then falls back to get_tradingview_chart_data (works for expired contracts)."""
    # Use wider window for dates far in the past
    days_from_now = (date.today() - target_date).days
    if days_from_now > 365:
        window_days = max(window_days, 10)
    elif days_from_now > 180:
        window_days = max(window_days, 7)

    start_ts = int(datetime.combine(
        target_date - timedelta(days=window_days), datetime.min.time(),
        tzinfo=timezone.utc
    ).timestamp() * 1000)
    end_ts = int(datetime.combine(
        target_date + timedelta(days=window_days + 1), datetime.min.time(),
        tzinfo=timezone.utc
    ).timestamp() * 1000)

    # ── Method 1: get_last_trades_by_instrument_and_time ──
    url = f"{DERIBIT_BASE}/public/get_last_trades_by_instrument_and_time"
    params = {
        "instrument_name": instrument,
        "start_timestamp": start_ts,
        "end_timestamp": end_ts,
        "count": 50,
        "sorting": "asc",
    }

    try:
        resp = await client.get(url, params=params)
        await asyncio.sleep(RATE_DELAY)

        if resp.status_code == 429:
            print(f"[Deribit] Rate limited for {instrument}, waiting 2s...")
            await asyncio.sleep(2.0)
            resp = await client.get(url, params=params)
            await asyncio.sleep(RATE_DELAY)

        # 400 = instrument doesn't exist on Deribit, skip silently
        if resp.status_code == 400:
            pass  # fall through to chart fallback
        else:
            resp.raise_for_status()
            data = resp.json()

            # Check for Deribit-specific error responses
            if "error" in data:
                error_info = data["error"]
                error_code = error_info.get("code", "unknown")
                error_msg = error_info.get("message", "unknown")
                print(f"[Deribit] API error for {instrument}: code={error_code}, msg={error_msg}")
                # Don't return yet — try chart data fallback
            else:
                result = data.get("result", {})
                if result is not None:
                    trades = result.get("trades", [])
                    if trades:
                        target_ts_val = int(datetime.combine(
                            target_date, datetime.min.time(), tzinfo=timezone.utc
                        ).timestamp() * 1000)
                        best = min(trades, key=lambda t: abs(t["timestamp"] - target_ts_val))
                        return best

    except Exception as e:
        print(f"[Deribit] Trade fetch error for {instrument}: {e}")

    # ── Method 2: Fallback to get_tradingview_chart_data ──
    # This works for expired contracts and returns daily OHLC data
    try:
        chart_url = f"{DERIBIT_BASE}/public/get_tradingview_chart_data"
        chart_params = {
            "instrument_name": instrument,
            "start_timestamp": start_ts,
            "end_timestamp": end_ts,
            "resolution": "1D",
        }
        resp = await client.get(chart_url, params=chart_params)
        await asyncio.sleep(RATE_DELAY)

        if resp.status_code == 429:
            await asyncio.sleep(2.0)
            resp = await client.get(chart_url, params=chart_params)
            await asyncio.sleep(RATE_DELAY)

        # 400 = instrument doesn't exist, return None silently
        if resp.status_code == 400:
            return None

        resp.raise_for_status()
        data = resp.json()

        if "error" in data:
            return None

        result = data.get("result", {})
        if result is None:
            return None

        ticks = result.get("ticks", [])
        closes = result.get("close", [])
        volumes = result.get("volume", [])

        if not ticks or not closes:
            return None

        # Find the candle closest to target_date
        target_ts_val = int(datetime.combine(
            target_date, datetime.min.time(), tzinfo=timezone.utc
        ).timestamp() * 1000)

        best_idx = min(range(len(ticks)), key=lambda i: abs(ticks[i] - target_ts_val))
        close_price = closes[best_idx]
        volume = volumes[best_idx] if best_idx < len(volumes) else 0

        if close_price <= 0:
            return None

        # Return a trade-like dict compatible with the rest of the code
        # Note: chart data prices are already in BTC (fraction of underlying)
        print(f"[Deribit] Chart fallback for {instrument}: date={target_date}, close={close_price:.6f}")
        return {
            "price": close_price,
            "timestamp": ticks[best_idx],
            "amount": volume,
            "direction": "chart",
            "index_price": None,
            "iv": None,
            "instrument_name": instrument,
            "_source": "chart_data",
        }

    except Exception as e:
        print(f"[Deribit] Chart fallback error for {instrument}: {e}")
        return None


# ── IV Smile interpolation (with DB cache) ──────────────────────────────────

async def _batch_fetch_chart_prices(
    client: httpx.AsyncClient,
    underlying: str,
    expiry: date,
    option_type: str,
    strikes: List[float],
    target_date: date,
    window_days: int = 5,
) -> Dict[float, float]:
    """Batch fetch option prices via chart data API for multiple strikes.
    Returns {strike: close_price_btc} for the candle closest to target_date.
    This is more reliable than trade API for historical/expired contracts."""

    days_from_now = (date.today() - target_date).days
    if days_from_now > 365:
        window_days = max(window_days, 10)
    elif days_from_now > 180:
        window_days = max(window_days, 7)

    start_ts = int(datetime.combine(
        target_date - timedelta(days=window_days), datetime.min.time(),
        tzinfo=timezone.utc
    ).timestamp() * 1000)
    end_ts = int(datetime.combine(
        target_date + timedelta(days=window_days + 1), datetime.min.time(),
        tzinfo=timezone.utc
    ).timestamp() * 1000)
    target_ts = int(datetime.combine(
        target_date, datetime.min.time(), tzinfo=timezone.utc
    ).timestamp() * 1000)

    results: Dict[float, float] = {}

    for strike in strikes:
        instrument = build_instrument_name(underlying, expiry, strike, option_type)
        chart_url = f"{DERIBIT_BASE}/public/get_tradingview_chart_data"
        chart_params = {
            "instrument_name": instrument,
            "start_timestamp": start_ts,
            "end_timestamp": end_ts,
            "resolution": "1D",
        }
        try:
            resp = await client.get(chart_url, params=chart_params)
            await asyncio.sleep(RATE_DELAY)

            if resp.status_code == 429:
                await asyncio.sleep(2.0)
                resp = await client.get(chart_url, params=chart_params)
                await asyncio.sleep(RATE_DELAY)

            # 400 = instrument doesn't exist on Deribit, skip
            if resp.status_code == 400:
                continue

            data = resp.json()
            if "error" in data:
                continue

            result = data.get("result", {})
            if not result:
                continue

            ticks = result.get("ticks", [])
            closes = result.get("close", [])
            if not ticks or not closes:
                continue

            best_idx = min(range(len(ticks)), key=lambda i: abs(ticks[i] - target_ts))
            close_price = closes[best_idx]
            if close_price and close_price > 0:
                results[strike] = close_price
                print(f"[Deribit Chart] {instrument}: close={close_price:.6f}")

        except Exception as e:
            print(f"[Deribit Chart] Error for {instrument}: {e}")
            continue

    print(f"[Deribit Chart] Batch result: {len(results)}/{len(strikes)} strikes have chart data")
    return results


async def fetch_iv_smile(
    client: httpx.AsyncClient,
    underlying: str,
    expiry: date,
    spot: float,
    option_type: str,
    target_date: date,
    num_strikes: int = 7,
) -> List[Tuple[float, float]]:
    """Fetch IV smile, using DB cache first.
    Returns list of (strike, iv) pairs."""

    # 1) Check DB cache — only use if we have real data (strike > 0)
    cached = get_cached_iv_smile(underlying, expiry, option_type, target_date)
    smile = [(s, iv) for s, iv, _ in cached if s > 0]
    if smile:
        print(f"[Deribit] Using cached IV smile for {underlying} {option_type} expiry={expiry} date={target_date}: {len(smile)} points")
        return smile

    # 1b) Check if we previously found no data for this combo — skip re-fetching
    if is_cached_no_data(underlying, expiry, option_type, target_date):
        print(f"[Deribit] Cached NO_DATA for {underlying} {option_type} expiry={expiry} date={target_date}, skipping")
        return []

    # 2) Fetch from API
    step = get_strike_step(underlying, spot)
    atm_strike = find_nearest_strike(spot, step)

    candidates = []
    for i in range(-num_strikes, num_strikes + 1):
        s = atm_strike + i * step
        if s > 0:
            candidates.append(s)
    candidates.sort(key=lambda s: abs(s - spot))
    candidates = candidates[:num_strikes * 2]

    print(f"[Deribit] Fetching IV smile: {underlying} {option_type} expiry={expiry} date={target_date} spot={spot:.0f} step={step} candidates={len(candidates)}")

    smile_points: List[Tuple[float, float]] = []
    db_points: List[Tuple[float, float, float, str]] = []

    # ── Strategy: Try trade API first for a few strikes. If mostly failing,
    #    switch to batch chart data approach which is more reliable for historical data.

    T = calculate_time_to_expiration(expiry, target_date)
    if T <= 0.0001:
        return []

    # First, try batch chart data for ALL candidates at once (most reliable for history)
    chart_results = await _batch_fetch_chart_prices(client, underlying, expiry, option_type, candidates, target_date)

    for strike in candidates:
        instrument = build_instrument_name(underlying, expiry, strike, option_type)
        price_btc = chart_results.get(strike)

        # If chart didn't have it, try trade API as fallback
        if price_btc is None or price_btc <= 0:
            trade = await fetch_trades_for_instrument(client, instrument, target_date, window_days=5)
            if trade is not None:
                price_btc = trade.get("price", 0)

        if price_btc is None or price_btc <= 0:
            continue

        trade_price_usd = price_btc * spot
        if trade_price_usd <= 0:
            continue

        iv = implied_volatility(
            market_price=trade_price_usd,
            S=spot, K=strike, T=T, r=RISK_FREE_RATE,
            option_type=option_type,
        )

        if iv is not None and 0.01 < iv < 10.0:
            smile_points.append((strike, iv))
            db_points.append((strike, iv, trade_price_usd, instrument))
            source = "chart" if strike in chart_results else "trade"
            print(f"[Deribit IV] {instrument}: price_btc={price_btc:.6f} price_usd={trade_price_usd:.2f}, IV={iv:.4f} ({source})")

    print(f"[Deribit] Smile result: {len(smile_points)} valid points out of {len(candidates)} candidates")

    # 3) Save to DB — only cache real data, never cache empty results
    if db_points:
        save_cached_iv_smile(underlying, expiry, option_type, target_date, spot, db_points)
    else:
        # Retry up to 3 times before marking as no-data
        # Check if we already have a retry count in the collection log
        from app.core.database import SessionLocal as _SL
        from app.models.data_collection import DataCollectionLog
        _db = _SL()
        try:
            log = _db.query(DataCollectionLog).filter(
                DataCollectionLog.source == "deribit",
                DataCollectionLog.data_type == "iv_smile",
                DataCollectionLog.underlying == underlying,
                DataCollectionLog.target_date == target_date,
                DataCollectionLog.expiry_date == expiry,
                DataCollectionLog.option_type == option_type,
            ).first()
            if log is None:
                log = DataCollectionLog(
                    source="deribit", data_type="iv_smile",
                    underlying=underlying, target_date=target_date,
                    expiry_date=expiry, option_type=option_type,
                    status="no_data", retry_count=1,
                    no_data_confirmed=False,
                )
                _db.add(log)
                _db.commit()
                print(f"[Deribit] No IV data (attempt 1/3) for {underlying} {option_type} expiry={expiry} date={target_date}")
            else:
                log.retry_count = (log.retry_count or 0) + 1
                if log.retry_count >= MAX_RETRY:
                    log.no_data_confirmed = True
                    log.status = "no_data"
                    # Save sentinel so we never re-fetch
                    save_no_data_sentinel(underlying, expiry, option_type, target_date, spot)
                    print(f"[Deribit] No IV data (attempt {log.retry_count}/{MAX_RETRY}) — CONFIRMED no data, sentinel saved")
                else:
                    print(f"[Deribit] No IV data (attempt {log.retry_count}/{MAX_RETRY}) for {underlying} {option_type} expiry={expiry} date={target_date}")
                _db.commit()
        except Exception as e:
            print(f"[Deribit] Error updating collection log: {e}")
            # Fallback: save sentinel immediately to avoid infinite retries
            save_no_data_sentinel(underlying, expiry, option_type, target_date, spot)
        finally:
            _db.close()

    return smile_points


def interpolate_iv_at_strike(
    smile_points: List[Tuple[float, float]],
    target_strike: float,
) -> Optional[float]:
    if not smile_points:
        return None
    if len(smile_points) == 1:
        return smile_points[0][1]

    strikes = np.array([p[0] for p in smile_points])
    ivs = np.array([p[1] for p in smile_points])
    order = np.argsort(strikes)
    strikes = strikes[order]
    ivs = ivs[order]
    interpolated = float(np.interp(target_strike, strikes, ivs))
    return max(0.05, min(interpolated, 5.0))


async def get_option_price_via_smile(
    client: httpx.AsyncClient,
    underlying: str,
    expiry: date,
    strike: float,
    spot: float,
    option_type: str,
    target_date: date,
) -> Tuple[float, str, Optional[float], List[Tuple[float, float]]]:
    """Get option price using IV smile interpolation.
    Returns (price_usd, data_source, iv_used, smile_points)."""

    smile = await fetch_iv_smile(
        client, underlying, expiry, spot, option_type, target_date
    )

    if smile:
        iv = interpolate_iv_at_strike(smile, strike)
        if iv is not None:
            T = calculate_time_to_expiration(expiry, target_date)
            price = black_scholes_price(spot, strike, T, RISK_FREE_RATE, iv, option_type)
            print(f"[Deribit] IV smile: strike={strike}, IV={iv:.4f}, price={price:.2f}")
            return price, "iv_smile", iv, smile

    T = calculate_time_to_expiration(expiry, target_date)
    default_iv = 0.6
    price = black_scholes_price(spot, strike, T, RISK_FREE_RATE, default_iv, option_type)
    print(f"[Deribit] Fallback model: strike={strike}, IV={default_iv}, price={price:.2f}")
    return price, "model", default_iv, []


# ── Fast single-strike price lookup for LEAPS scan ──────────────────────────

async def _fetch_single_strike_price_fast(
    client: httpx.AsyncClient,
    underlying: str,
    expiry: date,
    strike: float,
    spot: float,
    option_type: str,
    target_date: date,
) -> Tuple[float, str, Optional[float], List[Tuple[float, float]]]:
    """Get option price for a single strike with minimal API calls.

    Optimized for the LEAPS strike scan: first checks DB cache, then
    fetches a full IV smile (which caches all strikes at once), then
    falls back to direct chart/trade API, then BS model.

    Returns (price_usd, data_source, iv_used, smile_points).
    smile_points is empty since we don't build a full smile.
    """
    T = calculate_time_to_expiration(expiry, target_date)
    if T <= 0.0001:
        intrinsic = max(0, spot - strike) if option_type == "CALL" else max(0, strike - spot)
        return float(intrinsic), "intrinsic", None, []

    # 1) Check DB cache for this specific strike
    cached = get_cached_iv_smile(underlying, expiry, option_type, target_date)
    for s, iv, price_usd in cached:
        if abs(s - strike) < 1.0:
            price = black_scholes_price(spot, strike, T, RISK_FREE_RATE, iv, option_type)
            return float(price), "iv_cache", float(iv), []

    # If cache has nearby strikes, interpolate
    if len(cached) >= 2:
        strikes_arr = np.array([s for s, _, _ in cached if s > 0])
        ivs_arr = np.array([iv for _, iv, _ in cached if iv > 0])
        if len(strikes_arr) >= 2:
            order = np.argsort(strikes_arr)
            iv_interp = float(np.interp(strike, strikes_arr[order], ivs_arr[order]))
            iv_interp = max(0.05, min(iv_interp, 5.0))
            price = black_scholes_price(spot, strike, T, RISK_FREE_RATE, iv_interp, option_type)
            return float(price), "iv_cache_interp", float(iv_interp), []

    # 2) Check no-data sentinel — if the whole expiry/date has no data, use model
    if is_cached_no_data(underlying, expiry, option_type, target_date):
        default_iv = 0.6
        price = black_scholes_price(spot, strike, T, RISK_FREE_RATE, default_iv, option_type)
        return float(price), "model", float(default_iv), []

    # 3) Fetch full IV smile for this expiry/date combo — caches all strikes at once
    #    so subsequent calls for other strikes on the same expiry/date hit cache
    try:
        smile = await fetch_iv_smile(
            client, underlying, expiry, spot, option_type, target_date, num_strikes=7)
        if smile:
            # Now re-check cache (fetch_iv_smile saved data to DB)
            iv_interp = interpolate_iv_at_strike(smile, strike)
            if iv_interp and 0.01 < iv_interp < 10.0:
                price = black_scholes_price(spot, strike, T, RISK_FREE_RATE, iv_interp, option_type)
                return float(price), "iv_smile", float(iv_interp), []
    except (httpx.ConnectError, httpx.ConnectTimeout) as e:
        # Network is down — skip API calls, fall back to model immediately
        print(f"[LEAPS Fast] Network error in IV smile: {e}")
        default_iv = 0.6
        price = black_scholes_price(spot, strike, T, RISK_FREE_RATE, default_iv, option_type)
        return float(price), "model", float(default_iv), []
    except Exception as e:
        print(f"[LEAPS Fast] IV smile fetch error: {e}")

    # 4) Try chart data for just this one strike (1 API call)
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
                        iv = implied_volatility(price_usd, spot, strike, T, RISK_FREE_RATE, option_type)
                        if iv and 0.01 < iv < 10.0:
                            save_cached_iv_smile(underlying, expiry, option_type, target_date,
                                                 spot, [(strike, iv, price_usd, instrument)])
                            return float(price_usd), "chart_direct", float(iv), []
                        else:
                            return float(price_usd), "chart_direct", None, []
    except (httpx.ConnectError, httpx.ConnectTimeout) as e:
        print(f"[LEAPS Fast] Network error for chart {instrument}: {e}")
        default_iv = 0.6
        price = black_scholes_price(spot, strike, T, RISK_FREE_RATE, default_iv, option_type)
        return float(price), "model", float(default_iv), []
    except Exception as e:
        print(f"[LEAPS Fast] Chart error for {instrument}: {e}")

    # 4) Try trade data (1-2 API calls)
    try:
        trade = await fetch_trades_for_instrument(client, instrument, target_date, window_days=window_days)
        if trade:
            price_btc = trade.get("price", 0)
            if price_btc and price_btc > 0:
                price_usd = price_btc * spot
                iv = implied_volatility(price_usd, spot, strike, T, RISK_FREE_RATE, option_type)
                if iv and 0.01 < iv < 10.0:
                    save_cached_iv_smile(underlying, expiry, option_type, target_date,
                                         spot, [(strike, iv, price_usd, instrument)])
                    return float(price_usd), "trade_direct", float(iv), []
                else:
                    return float(price_usd), "trade_direct", None, []
    except (httpx.ConnectError, httpx.ConnectTimeout) as e:
        print(f"[LEAPS Fast] Network error for trade {instrument}: {e}")
    except Exception as e:
        print(f"[LEAPS Fast] Trade error for {instrument}: {e}")

    # 5) Fall back to BS model
    default_iv = 0.6
    price = black_scholes_price(spot, strike, T, RISK_FREE_RATE, default_iv, option_type)
    print(f"[LEAPS Fast] Fallback model: {instrument}, IV={default_iv}, price={price:.2f}")
    return float(price), "model", float(default_iv), []


# ── Backtest engine ─────────────────────────────────────────────────────────

async def run_real_backtest_engine(
    price_map: Dict[date, float],
    legs: List[RealBacktestLeg],
    roll_day: int,  # kept for API compatibility, not used (expiry-driven rolling)
    close_days_before: int,
    initial_capital: float,
    contract_mult: float,
    underlying: str,
    progress_callback=None,
    martingale: bool = False,
    max_double_times: int = 3,
    enhanced_martingale: bool = False,
    enhanced_martingale_recover_pct: float = 1.1,
    enhanced_max_multiplier: float = 10.0,
    vol_strategy: bool = False,
    vol_sell_iv: float = 80.0,
    vol_buy_iv: float = 40.0,
    vol_quantity: float = 1.0,
    vol_strike_offset_pct: float = 0.0,
    roll_put_strategy: bool = False,
    roll_put_offset_pct: float = 10.0,
    roll_put_quantity: float = 1.0,
    hedge_put_strategy: bool = False,
    hedge_put_offset_pct: float = 10.0,
    hedge_put_quantity: float = 1.0,
    hedge_put_crash_pct: float = 20.0,
    hedge_put_hedge_quantity: float = 1.0,
    channel_strategy: bool = False,
    channel_lookback_days: int = 90,
    channel_quantity: float = 1.0,
    wheel_strategy: bool = False,
    wheel_put_offset_pct: float = 5.0,
    wheel_call_offset_pct: float = 5.0,
    wheel_quantity: float = 1.0,
    wheel_reinvest: bool = True,
    grid_strategy: bool = False,
    grid_step: float = 100.0,
    grid_quantity: float = 1.0,
    grid_min_yield_pct: float = 10.0,
    grid_range_up: int = 5,
    grid_range_down: int = 5,
    grid_max_positions: int = 10,
    leaps_strategy: bool = False,
    leaps_max_annual_tv_pct: float = 10.0,
    leaps_min_months: int = 12,
    leaps_close_days_before: int = 30,
    leaps_quantity: float = 1.0,
    leaps_num_strikes: int = 15,
) -> RealBacktestResult:
    """Run backtest using Deribit IV smile interpolation for pricing."""

    equity = initial_capital
    equity_curve = []
    trades = []
    iv_smiles = []  # collect smile snapshots per trade event
    open_positions = []

    dates = sorted(price_map.keys())
    if not dates:
        raise HTTPException(status_code=400, detail="No price data")

    if martingale:
        print(f"[Martingale] Mode ENABLED, max_double_times={max_double_times}")
    if enhanced_martingale:
        print(f"[Enhanced Martingale] Mode ENABLED, recover_pct={enhanced_martingale_recover_pct}")
    if vol_strategy:
        print(f"[Vol Strategy] Mode ENABLED, sell_iv={vol_sell_iv}%, buy_iv={vol_buy_iv}%, qty={vol_quantity}, offset={vol_strike_offset_pct}")
    if roll_put_strategy:
        print(f"[Roll PUT] Mode ENABLED, offset={roll_put_offset_pct}%, qty={roll_put_quantity}")
    if hedge_put_strategy:
        print(f"[Hedge PUT] Mode ENABLED, offset={hedge_put_offset_pct}%, qty={hedge_put_quantity}, crash_threshold={hedge_put_crash_pct}%, hedge_qty={hedge_put_hedge_quantity}")
    if channel_strategy:
        print(f"[Channel] Mode ENABLED (进阶通道飞轮), lookback={channel_lookback_days} days, qty={channel_quantity}")
    if wheel_strategy:
        print(f"[Wheel] Mode ENABLED, put_offset={wheel_put_offset_pct}%, call_offset={wheel_call_offset_pct}%, qty={wheel_quantity}, reinvest={wheel_reinvest}")
    if grid_strategy:
        print(f"[Grid] Mode ENABLED, step={grid_step}, qty={grid_quantity}, min_yield={grid_min_yield_pct}%, range_up={grid_range_up}, range_down={grid_range_down}, max_pos={grid_max_positions}")
    if leaps_strategy:
        print(f"[LEAPS] Mode ENABLED, max_annual_tv={leaps_max_annual_tv_pct}%, min_months={leaps_min_months}, close_before={leaps_close_days_before}d, qty={leaps_quantity}, scan_strikes={leaps_num_strikes}")

    total_days = len(dates)
    leg_last_roll: Dict[int, date] = {}  # leg_idx -> last expiry date used
    last_progress_month = None
    real_count = 0
    total_count = 0

    # Martingale state: track consecutive losses and quantity multiplier
    # We track per "cycle" — a cycle is one set of legs opened and closed together
    martingale_multiplier = 1.0
    max_multiplier_reached = 1.0
    consecutive_losses = 0
    total_doublings = 0
    cycle_pnl = 0.0  # accumulated PnL for the current cycle
    cycle_open = False  # whether we have open positions in current cycle

    # Enhanced martingale state
    accumulated_loss = 0.0  # total unrecovered loss across consecutive losing cycles
    enhanced_multiplier = 1.0  # computed multiplier for enhanced martingale
    last_cycle_premium = 0.0  # total premium collected in the last cycle (at base qty)

    # Roll PUT state
    roll_put_pending_roll = None  # if set, dict with roll info for next open
    roll_put_loss_rolls = 0   # count of loss rolls
    roll_put_fresh_opens = 0  # count of fresh opens

    # Hedge PUT state
    hedge_put_pending_action = None  # "fresh", "loss_roll", "crash_hedge"
    hedge_put_loss_rolls = 0
    hedge_put_crash_hedges = 0
    hedge_put_fresh_opens = 0
    hedge_put_in_hedge_mode = False  # True when we're buying PUTs as hedge
    hedge_put_recovery_spot = None   # spot price at which we opened the original sell PUT (recovery target)

    # Channel strategy state (进阶通道策略 - 类似飞轮，但行权价由通道上下轨决定)
    channel_phase = "sell_put"  # "sell_put" or "sell_call"
    channel_assigned_spot = 0.0  # 行权日的实际spot（现货浮动盈亏基准）
    channel_assigned_strike = 0.0  # PUT行权价（记录用）
    channel_underlying_realized = 0.0  # 现货端累计已实现盈亏
    channel_underlying_unrealized = 0.0
    channel_fresh_opens = 0
    channel_put_sells = 0
    channel_call_sells = 0
    channel_assignments = 0
    channel_cycles = 0
    channel_total_premium = 0.0

    # Wheel strategy state (飞轮策略)
    wheel_phase = "sell_put"  # "sell_put" or "sell_call"
    wheel_assigned_spot = 0.0  # 行权日的实际spot（现货浮动盈亏基准）
    wheel_assigned_strike = 0.0  # PUT行权价（记录用）
    wheel_total_premium = 0.0  # total premium collected across all cycles
    wheel_cycles = 0  # completed full cycles (PUT assigned → CALL assigned)
    wheel_put_sells = 0
    wheel_call_sells = 0
    wheel_assignments = 0
    wheel_underlying_realized = 0.0  # 现货端累计已实现盈亏（CALL被行权时结算）

    # Grid strategy state (网格策略)
    # Each grid level can be in one of these states:
    # - "idle": no position, waiting for next roll
    # - "sell_put": sold PUT at this grid level
    # - "hold_spot": PUT was assigned, holding spot, selling CALL one grid up
    # - "sell_call": sold CALL at grid+1 level (holding spot)
    grid_levels: Dict[float, dict] = {}  # strike -> state dict
    grid_total_premium = 0.0
    grid_put_sells = 0
    grid_call_sells = 0
    grid_assignments = 0
    grid_cycles = 0  # completed full cycles (PUT assigned → CALL assigned)
    grid_skipped_low_yield = 0
    grid_underlying_realized = 0.0
    grid_underlying_unrealized = 0.0

    # LEAPS strategy state
    leaps_total_cost = 0.0
    leaps_total_proceeds = 0.0
    leaps_trades_count = 0

    async with create_http_client() as client:
        for day_idx, today in enumerate(dates):
            spot = price_map[today]

            # Report progress frequently (every 7 days or when month changes)
            current_ym = (today.year, today.month)
            should_report = (current_ym != last_progress_month) or (day_idx % 7 == 0)
            if should_report:
                last_progress_month = current_ym
                if progress_callback:
                    await progress_callback(day_idx, total_days, today)

            # ── 1) Close positions near expiry ──
            still_open = []
            for pos in open_positions:
                # Skip LEAPS positions — they have their own close logic in step 2b6
                if leaps_strategy and pos.get("_leg_idx") == 400:
                    still_open.append(pos)
                    continue

                days_to_exp = (pos["expiry"] - today).days
                should_close = False
                close_reason = ""

                if days_to_exp <= close_days_before and days_to_exp > 0:
                    should_close = True
                    close_reason = "到期前平仓"
                elif days_to_exp <= 0:
                    should_close = True
                    close_reason = "到期平仓"

                if should_close:
                    if days_to_exp <= 0:
                        # Expired — use intrinsic value
                        if pos["option_type"] == "PUT":
                            close_price = max(pos["strike"] - spot, 0.0)
                        else:
                            close_price = max(spot - pos["strike"], 0.0)
                        close_source = pos["data_source"]
                        close_iv = pos["iv_used"]
                        close_smile = []
                    else:
                        close_price, close_source, close_iv, close_smile = \
                            await get_option_price_via_smile(
                                client, underlying, pos["expiry"], pos["strike"],
                                spot, pos["option_type"], today,
                            )

                    # Cash flow at close: buying back the position
                    # For sold options (qty<0): close_price * qty is negative (we pay)
                    close_cash_flow = close_price * pos["quantity"] * contract_mult
                    pnl = (pos["open_price"] - close_price) * (-pos["quantity"]) * contract_mult
                    equity += close_cash_flow
                    total_count += 1
                    if close_source == "iv_smile" or pos["data_source"] == "iv_smile":
                        real_count += 1

                    # Track cycle PnL for martingale
                    if martingale or enhanced_martingale:
                        cycle_pnl += pnl

                    trade_idx = len(trades)
                    trades.append(RealTradeRecord(
                        open_date=pos["open_date"].isoformat(),
                        close_date=today.isoformat(),
                        option_type=pos["option_type"],
                        strike=round(pos["strike"], 2),
                        quantity=pos["quantity"],
                        open_price=round(pos["open_price"], 4),
                        close_price=round(close_price, 4),
                        pnl=round(pnl, 2),
                        close_reason=close_reason,
                        instrument=pos["instrument"],
                        data_source=pos["data_source"] if close_source != "iv_smile" else "iv_smile",
                        iv_used=close_iv or pos["iv_used"],
                        open_spot=round(pos["open_spot"], 2),
                        close_spot=round(spot, 2),
                        strike_distance_pct=round((pos["open_spot"] - pos["strike"]) / pos["strike"] * 100, 2) if pos["strike"] else None,
                        equity_after=round(equity, 2),
                    ))

                    # Roll PUT strategy: 用平仓现货价格和行权价比较判断是否被行权
                    # PUT价内(spot < strike) = 被行权，roll同一strike到下月
                    # PUT价外(spot >= strike) = 未被行权，重新开仓
                    if roll_put_strategy and pos.get("_leg_idx") == 0:
                        is_itm = spot < pos["strike"]  # PUT价内：现货 < 行权价
                        if is_itm:
                            # PUT被行权 — roll to next month, same strike
                            roll_put_pending_roll = {
                                "type": "loss_roll",
                                "months": 1,
                                "same_strike": pos["strike"],
                                "close_price": close_price,
                            }
                            print(f"[Roll PUT] PUT被行权: spot={spot:.0f} < strike={pos['strike']}, PnL={pnl:.2f}: roll to next month, same strike={pos['strike']}")
                        else:
                            # PUT未被行权(价外到期)
                            roll_put_pending_roll = {"type": "fresh"}
                            print(f"[Roll PUT] PUT未被行权: spot={spot:.0f} >= strike={pos['strike']}, PnL={pnl:.2f}: open fresh next month")

                    # Hedge PUT strategy: 用平仓现货价格和行权价比较判断是否被行权
                    if hedge_put_strategy and pos.get("_leg_idx") == 0:
                        if hedge_put_in_hedge_mode:
                            # We were in hedge mode (buying PUTs). Position closed.
                            # Check if spot has recovered to the recovery target
                            if spot >= hedge_put_recovery_spot:
                                # Recovered! Go back to normal selling
                                hedge_put_in_hedge_mode = False
                                hedge_put_pending_action = {"type": "fresh"}
                                print(f"[Hedge PUT] Spot recovered to {spot:.0f} >= {hedge_put_recovery_spot:.0f}, resuming normal sell PUT")
                            else:
                                # Still below recovery, keep buying PUTs
                                hedge_put_pending_action = {"type": "crash_hedge"}
                                print(f"[Hedge PUT] Spot {spot:.0f} still below recovery {hedge_put_recovery_spot:.0f}, continue hedge buying")
                        else:
                            is_itm = spot < pos["strike"]  # PUT价内：现货 < 行权价
                            if is_itm:
                                spot_drop_pct = (pos["open_spot"] - spot) / pos["open_spot"] * 100
                                if spot_drop_pct >= hedge_put_crash_pct:
                                    # Crash: switch to buying PUT as hedge
                                    hedge_put_in_hedge_mode = True
                                    hedge_put_recovery_spot = pos["open_spot"]  # recover to original open spot
                                    hedge_put_pending_action = {"type": "crash_hedge"}
                                    print(f"[Hedge PUT] CRASH detected: spot={spot:.0f} < strike={pos['strike']}, spot dropped {spot_drop_pct:.1f}% — switching to BUY PUT hedge until spot recovers to {hedge_put_recovery_spot:.0f}")
                                else:
                                    # PUT被行权但跌幅不大: roll to next month, same strike
                                    hedge_put_pending_action = {
                                        "type": "loss_roll",
                                        "same_strike": pos["strike"],
                                    }
                                    print(f"[Hedge PUT] PUT被行权: spot={spot:.0f} < strike={pos['strike']}, PnL={pnl:.2f}: will roll to next month, same strike={pos['strike']}")
                            else:
                                hedge_put_pending_action = {"type": "fresh"}
                                print(f"[Hedge PUT] PUT未被行权: spot={spot:.0f} >= strike={pos['strike']}, PnL={pnl:.2f}: will open fresh next month")

                    # Channel strategy (进阶通道飞轮): determine phase transition
                    if channel_strategy and pos.get("_leg_idx") in (100, 101):
                        ch_phase = pos.get("_channel_phase", "sell_put")
                        if ch_phase == "sell_put":
                            is_itm = spot < pos["strike"]  # PUT价内：现货 < 行权价
                            if is_itm:
                                channel_assigned_spot = spot
                                channel_assigned_strike = pos["strike"]
                                channel_phase = "sell_call"
                                channel_assignments += 1
                                print(f"[Channel] PUT被行权 K={pos['strike']}, spot={spot:.0f}, 转入卖CALL阶段 (现货基准={spot:.0f})")
                            else:
                                channel_phase = "sell_put"
                                print(f"[Channel] PUT未被行权 (OTM), 继续卖PUT")
                        elif ch_phase == "sell_call":
                            is_itm = spot > pos["strike"]  # CALL价内：现货 > 行权价
                            if is_itm:
                                underlying_pnl = (spot - channel_assigned_spot) * abs(channel_quantity) * contract_mult
                                equity += underlying_pnl
                                channel_underlying_realized += underlying_pnl
                                channel_phase = "sell_put"
                                channel_cycles += 1
                                channel_assignments += 1
                                print(f"[Channel] CALL被行权 K={pos['strike']}, spot={spot:.0f}, 现货盈亏={underlying_pnl:.2f} (基准={channel_assigned_spot:.0f}), 第{channel_cycles}轮完成, 回到卖PUT")
                            else:
                                channel_phase = "sell_call"
                                print(f"[Channel] CALL未被行权 (OTM), 继续卖CALL (持有现货, 基准={channel_assigned_spot:.0f})")

                    # Wheel strategy: determine phase transition
                    if wheel_strategy and pos.get("_leg_idx") == 200:
                        phase = pos.get("_wheel_phase", "sell_put")
                        # 用平仓现货价格和行权价比较判断是否被行权
                        if phase == "sell_put":
                            is_itm = spot < pos["strike"]  # PUT价内：现货 < 行权价
                        else:
                            is_itm = spot > pos["strike"]  # CALL价内：现货 > 行权价
                        if phase == "sell_put":
                            if is_itm:
                                # PUT assigned → we "bought" the underlying at strike
                                # 期权结算已经扣除了内在价值 (strike - spot)，
                                # 所以现货持仓的浮动盈亏基准应该是行权当天的 spot，
                                # 而不是 strike（否则会重复计算）
                                wheel_assigned_spot = spot  # 用行权日的实际spot作为基准
                                wheel_assigned_strike = pos["strike"]  # 记录行权价用于CALL行权时计算
                                wheel_phase = "sell_call"
                                wheel_assignments += 1
                                print(f"[Wheel] PUT assigned at K={pos['strike']}, spot={spot:.0f}, switching to sell CALL phase (underlying basis={spot:.0f})")
                            else:
                                # PUT expired OTM → stay in sell_put, sell again
                                wheel_phase = "sell_put"
                                print(f"[Wheel] PUT expired OTM, continue selling PUT")
                        elif phase == "sell_call":
                            if is_itm:
                                # CALL assigned → we "sold" the underlying at call_strike
                                # 现货真实盈亏 = call_strike - assigned_spot
                                # 但 CALL 期权结算已经扣除了内在价值 (spot - call_strike)
                                # 所以需要补回的总额 = (call_strike - assigned_spot) + (spot - call_strike)
                                #                    = spot - assigned_spot
                                underlying_pnl = (spot - wheel_assigned_spot) * abs(wheel_quantity) * contract_mult
                                equity += underlying_pnl
                                wheel_underlying_realized += underlying_pnl
                                wheel_phase = "sell_put"
                                wheel_cycles += 1
                                wheel_assignments += 1
                                print(f"[Wheel] CALL assigned at K={pos['strike']}, spot={spot:.0f}, underlying PnL={underlying_pnl:.2f} (basis={wheel_assigned_spot:.0f}), cycle #{wheel_cycles} complete")
                            else:
                                # CALL expired OTM → still holding underlying, sell CALL again
                                wheel_phase = "sell_call"
                                print(f"[Wheel] CALL expired OTM, continue selling CALL (holding underlying, basis={wheel_assigned_spot:.0f})")

                    # Grid strategy: determine phase transition per grid level
                    if grid_strategy and pos.get("_leg_idx") == 300:
                        grid_strike = pos.get("_grid_strike")
                        grid_phase = pos.get("_grid_phase", "sell_put")
                        if grid_strike is not None and grid_strike in grid_levels:
                            gl = grid_levels[grid_strike]
                            if grid_phase == "sell_put":
                                is_itm = spot < pos["strike"]
                                if is_itm:
                                    # PUT assigned → hold spot, will sell CALL one grid up
                                    gl["phase"] = "sell_call"
                                    gl["assigned_spot"] = spot
                                    grid_assignments += 1
                                    print(f"[Grid] PUT assigned K={pos['strike']}, spot={spot:.0f}, will sell CALL at K={grid_strike + grid_step}")
                                else:
                                    gl["phase"] = "idle"
                                    print(f"[Grid] PUT expired OTM K={pos['strike']}, spot={spot:.0f}, grid idle")
                            elif grid_phase == "sell_call":
                                is_itm = spot > pos["strike"]
                                if is_itm:
                                    # CALL assigned → sold underlying, realize PnL
                                    assigned_spot = gl.get("assigned_spot", pos["strike"])
                                    underlying_pnl = (spot - assigned_spot) * abs(grid_quantity) * contract_mult
                                    equity += underlying_pnl
                                    grid_underlying_realized += underlying_pnl
                                    gl["phase"] = "idle"
                                    grid_cycles += 1
                                    grid_assignments += 1
                                    print(f"[Grid] CALL assigned K={pos['strike']}, spot={spot:.0f}, underlying PnL={underlying_pnl:.2f} (basis={assigned_spot:.0f}), cycle complete")
                                else:
                                    # CALL expired OTM → still holding, sell CALL again
                                    gl["phase"] = "sell_call"
                                    print(f"[Grid] CALL expired OTM K={pos['strike']}, spot={spot:.0f}, continue selling CALL (holding spot, basis={gl.get('assigned_spot', 0):.0f})")

                    # Record both open and close smiles
                    iv_smiles.append({
                        "trade_idx": trade_idx,
                        "instrument": pos["instrument"],
                        "open_date": pos["open_date"].isoformat(),
                        "close_date": today.isoformat(),
                        "open_smile": pos.get("open_smile", []),
                        "close_smile": [{"strike": s, "iv": round(iv, 4)} for s, iv in close_smile],
                        "open_spot": pos["open_spot"],
                        "close_spot": spot,
                        "strike": round(pos["strike"], 2),
                        "option_type": pos["option_type"],
                        "expiry": pos["expiry"].isoformat(),
                    })
                else:
                    still_open.append(pos)

            open_positions = still_open

            # ── 2a) Vol strategy: open positions based on ATM IV ──
            if vol_strategy:
                has_open = len(open_positions) > 0
                if not has_open:
                    # Only check IV on roll_day each month (e.g. 1st of month)
                    # If roll_day already passed this month, wait for next month
                    is_roll_day = today.day == roll_day
                    last_expiry = leg_last_roll.get(0)
                    if last_expiry is not None and today <= last_expiry:
                        is_roll_day = False  # wait until after previous expiry

                    if is_roll_day:
                        expiry = find_deribit_expiry(today, 1)  # always 1-month expiry
                        T = calculate_time_to_expiration(expiry, today)
                        if T > 0.01:
                            # Fetch ATM IV to decide direction
                            raw_strike = spot * (1 + vol_strike_offset_pct)
                            step = get_strike_step(underlying, spot)
                            strike = find_nearest_strike(raw_strike, step)

                            smile = await fetch_iv_smile(
                                client, underlying, expiry, spot, "PUT", today
                            )
                            atm_iv_pct = None
                            if smile:
                                atm_iv = interpolate_iv_at_strike(smile, strike)
                                if atm_iv is not None:
                                    atm_iv_pct = atm_iv * 100  # convert to percentage

                            if atm_iv_pct is not None:
                                action = None
                                if atm_iv_pct >= vol_sell_iv:
                                    action = "sell"
                                    quantity = -abs(vol_quantity)
                                elif atm_iv_pct <= vol_buy_iv:
                                    action = "buy"
                                    quantity = abs(vol_quantity)

                                if action:
                                    instrument = build_instrument_name(underlying, expiry, strike, "PUT")
                                    open_price, data_source, iv_used, open_smile_pts = \
                                        await get_option_price_via_smile(
                                            client, underlying, expiry, strike,
                                            spot, "PUT", today,
                                        )
                                    premium_flow = -open_price * quantity * contract_mult
                                    equity += premium_flow
                                    leg_last_roll[0] = expiry

                                    open_positions.append({
                                        "option_type": "PUT",
                                        "strike": strike,
                                        "expiry": expiry,
                                        "quantity": quantity,
                                        "open_price": open_price,
                                        "open_date": today,
                                        "open_spot": spot,
                                        "iv_used": iv_used,
                                        "data_source": data_source,
                                        "instrument": instrument,
                                        "open_smile": [{"strike": s, "iv": round(iv, 4)} for s, iv in (open_smile_pts or [])],
                                        "_leg_idx": 0,
                                    })
                                    print(f"[Vol Strategy] {action.upper()} PUT: IV={atm_iv_pct:.1f}%, strike={strike}, qty={quantity}, price={open_price:.4f}, instrument={instrument}")
                                else:
                                    print(f"[Vol Strategy] {today} IV={atm_iv_pct:.1f}% — no action (sell>{vol_sell_iv}%, buy<{vol_buy_iv}%), wait next month")
                            else:
                                print(f"[Vol Strategy] Could not determine ATM IV for {today}, skipping")

            # ── 2b) Roll PUT strategy ──
            if roll_put_strategy:
                has_open = any(p.get("_leg_idx") == 0 for p in open_positions)
                if not has_open:
                    # For loss_roll / crash_roll: open immediately (next day after close)
                    # For fresh / None: wait for roll_day
                    is_pending_roll = (roll_put_pending_roll is not None
                                       and roll_put_pending_roll["type"] == "loss_roll")
                    is_roll_day = today.day == roll_day
                    last_expiry = leg_last_roll.get(0)
                    if not is_pending_roll:
                        # Only block on last_expiry for fresh opens
                        if last_expiry is not None and today <= last_expiry:
                            is_roll_day = False
                    else:
                        # Pending roll: open as soon as possible (any day after close)
                        is_roll_day = True

                    if is_roll_day:
                        step = get_strike_step(underlying, spot)
                        quantity = -abs(roll_put_quantity)

                        if roll_put_pending_roll is None or roll_put_pending_roll["type"] == "fresh":
                            # Fresh open: sell PUT at spot * (1 - offset%)
                            raw_strike = spot * (1 - roll_put_offset_pct / 100.0)
                            strike = find_nearest_strike(raw_strike, step)
                            expiry = find_deribit_expiry(today, 1)
                            print(f"[Roll PUT] Fresh open: spot={spot}, strike={strike} ({roll_put_offset_pct}% OTM), expiry={expiry}")

                        elif roll_put_pending_roll["type"] == "loss_roll":
                            # Loss roll: same strike, next month
                            strike = roll_put_pending_roll["same_strike"]
                            expiry = find_deribit_expiry(today, 1)
                            print(f"[Roll PUT] Loss roll: same strike={strike}, expiry={expiry}")

                        else:
                            # Fallback: fresh
                            raw_strike = spot * (1 - roll_put_offset_pct / 100.0)
                            strike = find_nearest_strike(raw_strike, step)
                            expiry = find_deribit_expiry(today, 1)

                        T = calculate_time_to_expiration(expiry, today)
                        if T > 0.01:
                            instrument = build_instrument_name(underlying, expiry, strike, "PUT")
                            open_price, data_source, iv_used, open_smile_pts = \
                                await get_option_price_via_smile(
                                    client, underlying, expiry, strike,
                                    spot, "PUT", today,
                                )

                            premium_flow = -open_price * quantity * contract_mult
                            equity += premium_flow
                            leg_last_roll[0] = expiry

                            open_positions.append({
                                "option_type": "PUT",
                                "strike": strike,
                                "expiry": expiry,
                                "quantity": quantity,
                                "open_price": open_price,
                                "open_date": today,
                                "open_spot": spot,
                                "iv_used": iv_used,
                                "data_source": data_source,
                                "instrument": instrument,
                                "open_smile": [{"strike": s, "iv": round(iv, 4)} for s, iv in (open_smile_pts or [])],
                                "_leg_idx": 0,
                            })
                            roll_type = roll_put_pending_roll["type"] if roll_put_pending_roll else "fresh"
                            if roll_type == "loss_roll":
                                roll_put_loss_rolls += 1
                            else:
                                roll_put_fresh_opens += 1
                            print(f"[Roll PUT] Opened: {instrument}, qty={quantity}, price={open_price:.4f}, type={roll_type}")
                            roll_put_pending_roll = None  # reset

            # ── 2b2) Hedge PUT strategy ──
            if hedge_put_strategy:
                has_open = any(p.get("_leg_idx") == 0 for p in open_positions)
                if not has_open:
                    # For loss_roll / crash_hedge: open immediately
                    # For fresh / None: wait for roll_day
                    is_pending = (hedge_put_pending_action is not None
                                  and hedge_put_pending_action["type"] in ("loss_roll", "crash_hedge"))
                    is_roll_day = today.day == roll_day
                    last_expiry = leg_last_roll.get(0)
                    if not is_pending:
                        if last_expiry is not None and today <= last_expiry:
                            is_roll_day = False
                    else:
                        is_roll_day = True

                    # In hedge mode: also check if spot recovered before opening new hedge
                    if hedge_put_in_hedge_mode and hedge_put_recovery_spot is not None:
                        if spot >= hedge_put_recovery_spot:
                            hedge_put_in_hedge_mode = False
                            hedge_put_pending_action = {"type": "fresh"}
                            print(f"[Hedge PUT] Spot recovered to {spot:.0f} before new open, resuming normal sell")

                    if is_roll_day:
                        step = get_strike_step(underlying, spot)

                        if hedge_put_in_hedge_mode:
                            # HEDGE MODE: buy PUT (ATM or near ATM) for current month
                            quantity = abs(hedge_put_hedge_quantity)  # positive = buying
                            strike = find_nearest_strike(spot, step)  # ATM
                            expiry = find_deribit_expiry(today, 1)
                            hedge_put_crash_hedges += 1
                            print(f"[Hedge PUT] Hedge BUY PUT: spot={spot}, ATM strike={strike}, qty=+{quantity}, expiry={expiry}")

                        elif hedge_put_pending_action is None or hedge_put_pending_action["type"] == "fresh":
                            # Fresh open: sell PUT at spot * (1 - offset%)
                            quantity = -abs(hedge_put_quantity)
                            raw_strike = spot * (1 - hedge_put_offset_pct / 100.0)
                            strike = find_nearest_strike(raw_strike, step)
                            expiry = find_deribit_expiry(today, 1)
                            hedge_put_fresh_opens += 1
                            print(f"[Hedge PUT] Fresh open: spot={spot}, strike={strike} ({hedge_put_offset_pct}% OTM), expiry={expiry}")

                        elif hedge_put_pending_action["type"] == "loss_roll":
                            # Loss roll: same strike, next month
                            quantity = -abs(hedge_put_quantity)
                            strike = hedge_put_pending_action["same_strike"]
                            expiry = find_deribit_expiry(today, 1)
                            hedge_put_loss_rolls += 1
                            print(f"[Hedge PUT] Loss roll: same strike={strike}, expiry={expiry}")

                        elif hedge_put_pending_action["type"] == "crash_hedge":
                            # Crash hedge: buy PUT ATM
                            quantity = abs(hedge_put_hedge_quantity)
                            strike = find_nearest_strike(spot, step)
                            expiry = find_deribit_expiry(today, 1)
                            hedge_put_crash_hedges += 1
                            print(f"[Hedge PUT] Crash hedge BUY PUT: ATM strike={strike}, qty=+{quantity}, expiry={expiry}")

                        else:
                            quantity = -abs(hedge_put_quantity)
                            raw_strike = spot * (1 - hedge_put_offset_pct / 100.0)
                            strike = find_nearest_strike(raw_strike, step)
                            expiry = find_deribit_expiry(today, 1)
                            hedge_put_fresh_opens += 1

                        T = calculate_time_to_expiration(expiry, today)
                        if T > 0.01:
                            instrument = build_instrument_name(underlying, expiry, strike, "PUT")
                            open_price, data_source, iv_used, open_smile_pts = \
                                await get_option_price_via_smile(
                                    client, underlying, expiry, strike,
                                    spot, "PUT", today,
                                )

                            premium_flow = -open_price * quantity * contract_mult
                            equity += premium_flow
                            leg_last_roll[0] = expiry

                            open_positions.append({
                                "option_type": "PUT",
                                "strike": strike,
                                "expiry": expiry,
                                "quantity": quantity,
                                "open_price": open_price,
                                "open_date": today,
                                "open_spot": spot,
                                "iv_used": iv_used,
                                "data_source": data_source,
                                "instrument": instrument,
                                "open_smile": [{"strike": s, "iv": round(iv, 4)} for s, iv in (open_smile_pts or [])],
                                "_leg_idx": 0,
                            })
                            action_type = hedge_put_pending_action["type"] if hedge_put_pending_action else "fresh"
                            print(f"[Hedge PUT] Opened: {instrument}, qty={quantity}, price={open_price:.4f}, type={action_type}")
                            hedge_put_pending_action = None  # reset

            # ── 2b3) Channel strategy (进阶通道飞轮): 下轨卖PUT → 被行权持有现货 → 上轨卖CALL → 被行权回到卖PUT ──
            if channel_strategy:
                has_channel_open = any(p.get("_leg_idx") in (100, 101) for p in open_positions)
                if not has_channel_open:
                    last_expiry = leg_last_roll.get(100) or leg_last_roll.get(101)
                    should_open = False
                    if last_expiry is None:
                        if today.day >= roll_day:
                            should_open = True
                    else:
                        if today > last_expiry:
                            should_open = True

                    if should_open:
                        # Calculate rolling high/low over lookback window
                        lookback_start = today - timedelta(days=channel_lookback_days)
                        window_prices = [price_map[d] for d in dates if lookback_start <= d < today and d in price_map]
                        if len(window_prices) >= 10:
                            rolling_high = max(window_prices)
                            rolling_low = min(window_prices)
                            step = get_strike_step(underlying, spot)
                            put_strike = find_nearest_strike(rolling_low, step)
                            call_strike = find_nearest_strike(rolling_high, step)
                            expiry = find_deribit_expiry(today, 1)
                            T = calculate_time_to_expiration(expiry, today)

                            if T > 0.01 and put_strike < call_strike:
                                quantity = -abs(channel_quantity)

                                if channel_phase == "sell_put":
                                    # 卖PUT在下轨
                                    instrument = build_instrument_name(underlying, expiry, put_strike, "PUT")
                                    price, src, iv, smile = await get_option_price_via_smile(
                                        client, underlying, expiry, put_strike, spot, "PUT", today,
                                    )
                                    premium_flow = -price * quantity * contract_mult
                                    equity += premium_flow
                                    channel_total_premium += abs(premium_flow)
                                    open_positions.append({
                                        "option_type": "PUT", "strike": put_strike, "expiry": expiry,
                                        "quantity": quantity, "open_price": price, "open_date": today,
                                        "open_spot": spot, "iv_used": iv, "data_source": src,
                                        "instrument": instrument,
                                        "open_smile": [{"strike": s, "iv": round(v, 4)} for s, v in (smile or [])],
                                        "_leg_idx": 100, "_channel_phase": "sell_put",
                                    })
                                    leg_last_roll[100] = expiry
                                    channel_put_sells += 1
                                    channel_fresh_opens += 1
                                    print(f"[Channel] 卖PUT K={put_strike} (下轨={rolling_low:.0f}), spot={spot:.0f}, premium={price:.2f}, expiry={expiry}")

                                elif channel_phase == "sell_call":
                                    # 卖CALL在上轨（持有现货中）
                                    instrument = build_instrument_name(underlying, expiry, call_strike, "CALL")
                                    price, src, iv, smile = await get_option_price_via_smile(
                                        client, underlying, expiry, call_strike, spot, "CALL", today,
                                    )
                                    premium_flow = -price * quantity * contract_mult
                                    equity += premium_flow
                                    channel_total_premium += abs(premium_flow)
                                    open_positions.append({
                                        "option_type": "CALL", "strike": call_strike, "expiry": expiry,
                                        "quantity": quantity, "open_price": price, "open_date": today,
                                        "open_spot": spot, "iv_used": iv, "data_source": src,
                                        "instrument": instrument,
                                        "open_smile": [{"strike": s, "iv": round(v, 4)} for s, v in (smile or [])],
                                        "_leg_idx": 101, "_channel_phase": "sell_call",
                                    })
                                    leg_last_roll[101] = expiry
                                    channel_call_sells += 1
                                    channel_fresh_opens += 1
                                    print(f"[Channel] 卖CALL K={call_strike} (上轨={rolling_high:.0f}), spot={spot:.0f}, 现货基准={channel_assigned_spot:.0f}, premium={price:.2f}, expiry={expiry}")
                            else:
                                print(f"[Channel] Skipped: T too small or put_strike({put_strike}) >= call_strike({call_strike})")
                        else:
                            print(f"[Channel] Not enough lookback data ({len(window_prices)} prices), need at least 10")

            # ── 2b4) Wheel strategy (飞轮策略) ──
            if wheel_strategy:
                # Use leg_idx 200 for wheel positions
                has_wheel_open = any(p.get("_leg_idx") == 200 for p in open_positions)
                if not has_wheel_open:
                    last_expiry = leg_last_roll.get(200)
                    should_open = False
                    if last_expiry is None:
                        # First open: use roll_day
                        if today.day >= roll_day:
                            should_open = True
                    else:
                        # After previous expiry closed, open next day
                        if today > last_expiry:
                            should_open = True

                    if should_open:
                        expiry = find_deribit_expiry(today, 1)
                        T = calculate_time_to_expiration(expiry, today)
                        if T > 0.01:
                            step = get_strike_step(underlying, spot)
                            quantity = -abs(wheel_quantity)

                            if wheel_phase == "sell_put":
                                # Sell cash-secured PUT
                                raw_strike = spot * (1 - wheel_put_offset_pct / 100.0)
                                strike = find_nearest_strike(raw_strike, step)
                                instrument = build_instrument_name(underlying, expiry, strike, "PUT")
                                price, src, iv, smile = await get_option_price_via_smile(
                                    client, underlying, expiry, strike, spot, "PUT", today,
                                )
                                premium_flow = -price * quantity * contract_mult
                                equity += premium_flow
                                wheel_total_premium += abs(premium_flow)
                                open_positions.append({
                                    "option_type": "PUT", "strike": strike, "expiry": expiry,
                                    "quantity": quantity, "open_price": price, "open_date": today,
                                    "open_spot": spot, "iv_used": iv, "data_source": src,
                                    "instrument": instrument,
                                    "open_smile": [{"strike": s, "iv": round(v, 4)} for s, v in (smile or [])],
                                    "_leg_idx": 200, "_wheel_phase": "sell_put",
                                })
                                leg_last_roll[200] = expiry
                                wheel_put_sells += 1
                                print(f"[Wheel] Sell PUT K={strike}, spot={spot:.0f}, premium={price:.2f}, expiry={expiry}")

                            elif wheel_phase == "sell_call":
                                # Sell covered CALL (we "hold" the underlying from PUT assignment)
                                raw_strike = spot * (1 + wheel_call_offset_pct / 100.0)
                                strike = find_nearest_strike(raw_strike, step)
                                instrument = build_instrument_name(underlying, expiry, strike, "CALL")
                                price, src, iv, smile = await get_option_price_via_smile(
                                    client, underlying, expiry, strike, spot, "CALL", today,
                                )
                                premium_flow = -price * quantity * contract_mult
                                equity += premium_flow
                                wheel_total_premium += abs(premium_flow)
                                open_positions.append({
                                    "option_type": "CALL", "strike": strike, "expiry": expiry,
                                    "quantity": quantity, "open_price": price, "open_date": today,
                                    "open_spot": spot, "iv_used": iv, "data_source": src,
                                    "instrument": instrument,
                                    "open_smile": [{"strike": s, "iv": round(v, 4)} for s, v in (smile or [])],
                                    "_leg_idx": 200, "_wheel_phase": "sell_call",
                                })
                                leg_last_roll[200] = expiry
                                wheel_call_sells += 1
                                print(f"[Wheel] Sell CALL K={strike}, spot={spot:.0f}, cost_basis={wheel_assigned_spot:.0f}, premium={price:.2f}, expiry={expiry}")

            # ── 2b5) Grid strategy (网格策略): sell PUT at each grid level where annualized yield > threshold ──
            if grid_strategy:
                # On roll_day of each month, scan grid levels and open positions
                is_roll_day = today.day == roll_day
                last_grid_put_expiry = leg_last_roll.get(301)  # PUT roll tracking
                if last_grid_put_expiry is not None and today <= last_grid_put_expiry:
                    is_roll_day = False

                # Check if any sell_call grid levels need a CALL opened
                # (independent of roll_day — CALL should open as soon as PUT expires ITM)
                grid_needs_call_open = False
                for gs, gl in grid_levels.items():
                    if gl["phase"] == "sell_call":
                        has_open_for_grid = any(
                            p.get("_leg_idx") == 300 and p.get("_grid_strike") == gs
                            for p in open_positions
                        )
                        if not has_open_for_grid:
                            grid_needs_call_open = True
                            break

                if is_roll_day or grid_needs_call_open:
                    # Find current month's expiry
                    expiry = find_deribit_expiry(today, 1)
                    T = calculate_time_to_expiration(expiry, today)
                    if T > 0.01:
                        step = grid_step
                        # Snap spot to nearest grid
                        base_grid = round(spot / step) * step

                        # Generate grid levels around current spot
                        grid_strikes = []
                        for i in range(-grid_range_down, grid_range_up + 1):
                            gs = base_grid + i * step
                            if gs > 0:
                                grid_strikes.append(gs)

                        # Also include any existing grid levels in sell_call phase
                        # that may have fallen outside the current spot-based range
                        # (e.g. spot moved significantly since the PUT was assigned)
                        for gs, gl in grid_levels.items():
                            if gl["phase"] == "sell_call" and gs not in grid_strikes:
                                grid_strikes.append(gs)
                        grid_strikes.sort()

                        # Initialize grid levels that don't exist yet
                        for gs in grid_strikes:
                            if gs not in grid_levels:
                                grid_levels[gs] = {"phase": "idle", "assigned_spot": 0.0}

                        for gs in grid_strikes:
                            gl = grid_levels[gs]
                            # Check if this grid level already has an open position
                            has_open_for_grid = any(
                                p.get("_leg_idx") == 300 and p.get("_grid_strike") == gs
                                for p in open_positions
                            )
                            if has_open_for_grid:
                                continue

                            # Check total grid position limit before opening new positions
                            current_grid_positions = sum(
                                1 for p in open_positions if p.get("_leg_idx") == 300
                            )
                            if current_grid_positions >= grid_max_positions:
                                print(f"[Grid] Max positions reached ({grid_max_positions}), skipping K={gs}")
                                continue

                            if gl["phase"] == "idle" and is_roll_day:
                                # Sell PUT at this grid level if annualized yield > threshold
                                put_strike = gs
                                instrument = build_instrument_name(underlying, expiry, put_strike, "PUT")
                                price, src, iv, smile = await get_option_price_via_smile(
                                    client, underlying, expiry, put_strike, spot, "PUT", today,
                                )
                                # Annualized yield = (premium / strike) * (365 / days_to_expiry) * 100
                                days_to_exp = max((expiry - today).days, 1)
                                annualized_yield = (price / put_strike) * (365.0 / days_to_exp) * 100.0
                                if annualized_yield >= grid_min_yield_pct:
                                    quantity = -abs(grid_quantity)
                                    premium_flow = -price * quantity * contract_mult
                                    equity += premium_flow
                                    grid_total_premium += abs(premium_flow)
                                    open_positions.append({
                                        "option_type": "PUT", "strike": put_strike, "expiry": expiry,
                                        "quantity": quantity, "open_price": price, "open_date": today,
                                        "open_spot": spot, "iv_used": iv, "data_source": src,
                                        "instrument": instrument,
                                        "open_smile": [{"strike": s, "iv": round(v, 4)} for s, v in (smile or [])],
                                        "_leg_idx": 300, "_grid_strike": gs, "_grid_phase": "sell_put",
                                    })
                                    gl["phase"] = "sell_put"
                                    grid_put_sells += 1
                                    leg_last_roll[301] = expiry
                                    print(f"[Grid] Sell PUT K={put_strike}, spot={spot:.0f}, premium={price:.2f}, yield={annualized_yield:.1f}%, expiry={expiry}")
                                else:
                                    grid_skipped_low_yield += 1
                                    print(f"[Grid] Skip PUT K={put_strike}: yield={annualized_yield:.1f}% < {grid_min_yield_pct}%")

                            elif gl["phase"] == "sell_call":
                                # Sell CALL one grid up from the assigned PUT strike
                                call_strike = gs + step
                                instrument = build_instrument_name(underlying, expiry, call_strike, "CALL")
                                price, src, iv, smile = await get_option_price_via_smile(
                                    client, underlying, expiry, call_strike, spot, "CALL", today,
                                )
                                quantity = -abs(grid_quantity)
                                premium_flow = -price * quantity * contract_mult
                                equity += premium_flow
                                grid_total_premium += abs(premium_flow)
                                open_positions.append({
                                    "option_type": "CALL", "strike": call_strike, "expiry": expiry,
                                    "quantity": quantity, "open_price": price, "open_date": today,
                                    "open_spot": spot, "iv_used": iv, "data_source": src,
                                    "instrument": instrument,
                                    "open_smile": [{"strike": s, "iv": round(v, 4)} for s, v in (smile or [])],
                                    "_leg_idx": 300, "_grid_strike": gs, "_grid_phase": "sell_call",
                                })
                                grid_call_sells += 1
                                print(f"[Grid] Sell CALL K={call_strike} (grid {gs} + {step}), spot={spot:.0f}, basis={gl.get('assigned_spot', 0):.0f}, premium={price:.2f}, expiry={expiry}")

            # ── 2b6) LEAPS strategy: buy long-dated CALL with low annualized time value ──
            if leaps_strategy:
                has_leaps_open = any(p.get("_leg_idx") == 400 for p in open_positions)

                # Check if we need to close existing LEAPS (N days before expiry)
                # Close logic is handled in step 1 via close_days_before, but LEAPS
                # uses its own close threshold. We handle it here for clarity.
                # Actually, the main close loop already handles it if days_to_exp <= close_days_before.
                # But LEAPS uses leaps_close_days_before which may differ from the global one.
                # So we do an explicit check here.
                new_still_open = []
                leaps_just_closed = False
                for pos in open_positions:
                    if pos.get("_leg_idx") == 400:
                        days_to_exp = (pos["expiry"] - today).days
                        if days_to_exp <= leaps_close_days_before:
                            # Close the LEAPS position
                            T_close = max(calculate_time_to_expiration(pos["expiry"], today), 0.0001)
                            close_price, close_src, close_iv, close_smile = await _fetch_single_strike_price_fast(
                                client, underlying, pos["expiry"], pos["strike"], spot, "CALL", today,
                            )
                            # Cash flow: sell back the CALL, receive close_price * qty * mult
                            close_cash_flow = close_price * pos["quantity"] * contract_mult
                            pnl = (close_price - pos["open_price"]) * pos["quantity"] * contract_mult
                            equity += close_cash_flow
                            leaps_total_proceeds += close_cash_flow
                            leaps_trades_count += 1

                            trade_idx = len(trades)
                            trades.append(RealTradeRecord(
                                open_date=pos["open_date"].isoformat(),
                                close_date=today.isoformat(),
                                option_type="CALL",
                                strike=round(pos["strike"], 2),
                                quantity=pos["quantity"],
                                open_price=round(pos["open_price"], 2),
                                close_price=round(close_price, 2),
                                pnl=round(pnl, 2),
                                close_reason=f"LEAPS到期前{leaps_close_days_before}天平仓",
                                instrument=pos["instrument"],
                                data_source=close_src,
                                iv_used=close_iv,
                                open_spot=pos["open_spot"],
                                close_spot=spot,
                                strike_distance_pct=round((pos["strike"] - pos["open_spot"]) / pos["open_spot"] * 100, 1) if pos["open_spot"] else None,
                                equity_after=round(equity, 2),
                            ))
                            iv_smiles.append({
                                "trade_idx": trade_idx,
                                "instrument": pos["instrument"],
                                "open_date": pos["open_date"].isoformat(),
                                "close_date": today.isoformat(),
                                "open_smile": pos.get("open_smile", []),
                                "close_smile": [{"strike": s, "iv": round(iv, 4)} for s, iv in (close_smile or [])],
                                "open_spot": pos["open_spot"],
                                "close_spot": spot,
                                "strike": round(pos["strike"], 2),
                                "option_type": "CALL",
                                "expiry": pos["expiry"].isoformat(),
                            })
                            leaps_just_closed = True
                            print(f"[LEAPS] Closed CALL K={pos['strike']}, pnl={pnl:.2f}, days_left={days_to_exp}")
                        else:
                            new_still_open.append(pos)
                    else:
                        new_still_open.append(pos)
                open_positions = new_still_open

                # Open new LEAPS if none open
                has_leaps_open = any(p.get("_leg_idx") == 400 for p in open_positions)
                if not has_leaps_open:
                    # Find the nearest quarterly expiry that is ≥ leaps_min_months away
                    # Deribit long-dated options only exist on quarterly expiries (Mar/Jun/Sep/Dec)
                    min_target_date = today + timedelta(days=leaps_min_months * 30)
                    quarterly_expiries = find_deribit_quarterly_expiries(today, max_years_ahead=3)
                    expiry = None
                    for qe in quarterly_expiries:
                        if qe >= min_target_date:
                            expiry = qe
                            break

                    if expiry is None and quarterly_expiries:
                        # Fallback: use the furthest available quarterly expiry
                        expiry = quarterly_expiries[-1]

                    if expiry is not None:
                        T = calculate_time_to_expiration(expiry, today)
                    else:
                        T = 0.0
                        print(f"[LEAPS] No quarterly expiry found for {today}, skipping")
                    if T > 0.1:
                        print(f"[LEAPS] Selected quarterly expiry={expiry} (T={T:.2f}y, {(expiry - today).days}d away) for {today}")
                        step = get_strike_step(underlying, spot)
                        # Scan strikes from ATM toward deep ITM (decreasing strike for CALL)
                        # For CALL: ITM = strike < spot. Lower strike = deeper ITM = less time value.
                        # We scan from ATM downward, find the first strike where annual TV/K < threshold.
                        # That gives us the closest-to-ATM deep ITM CALL that meets the criteria.
                        atm_strike = find_nearest_strike(spot, step)
                        candidates = []
                        for i in range(0, leaps_num_strikes + 1):
                            s = atm_strike - i * step
                            if s > 0:
                                candidates.append(s)
                        # candidates are already sorted ATM → deep ITM (descending strike)

                        best_strike = None
                        best_price = None
                        best_iv = None
                        best_smile = None
                        best_src = None
                        best_tv_annual = None

                        for strike_candidate in candidates:
                            instrument = build_instrument_name(underlying, expiry, strike_candidate, "CALL")
                            price, src, iv, smile = await _fetch_single_strike_price_fast(
                                client, underlying, expiry, strike_candidate, spot, "CALL", today,
                            )
                            if price <= 0:
                                continue
                            # Time value = option price - intrinsic value
                            intrinsic = max(0, spot - strike_candidate)
                            time_value = price - intrinsic
                            if time_value < 0:
                                time_value = 0  # shouldn't happen but safety
                            # Annualized TV% = (time_value / strike) * (365 / days_to_expiry) * 100
                            days_to_exp = max((expiry - today).days, 1)
                            annual_tv_pct = (time_value / strike_candidate) * (365.0 / days_to_exp) * 100.0
                            print(f"[LEAPS] Scan K={strike_candidate}, price={price:.2f}, intrinsic={intrinsic:.2f}, TV={time_value:.2f}, annual_TV%={annual_tv_pct:.2f}%")

                            if annual_tv_pct < leaps_max_annual_tv_pct:
                                best_strike = strike_candidate
                                best_price = price
                                best_iv = iv
                                best_smile = smile
                                best_src = src
                                best_tv_annual = annual_tv_pct
                                break  # closest to ATM that meets criteria

                        if best_strike is not None:
                            quantity = abs(leaps_quantity)
                            cost = best_price * quantity * contract_mult
                            equity -= cost  # pay premium to buy
                            leaps_total_cost += cost
                            open_positions.append({
                                "option_type": "CALL", "strike": best_strike, "expiry": expiry,
                                "quantity": quantity, "open_price": best_price, "open_date": today,
                                "open_spot": spot, "iv_used": best_iv, "data_source": best_src,
                                "instrument": build_instrument_name(underlying, expiry, best_strike, "CALL"),
                                "open_smile": [{"strike": s, "iv": round(v, 4)} for s, v in (best_smile or [])],
                                "_leg_idx": 400,
                            })
                            print(f"[LEAPS] Buy CALL K={best_strike}, spot={spot:.0f}, price={best_price:.2f}, annual_TV%={best_tv_annual:.2f}%, expiry={expiry}, T={T:.2f}y")
                        else:
                            print(f"[LEAPS] No strike found with annual_TV% < {leaps_max_annual_tv_pct}% for expiry={expiry}")

            # ── 2c) Open new positions — expiry-driven rolling (normal legs) ──
            if not vol_strategy and not roll_put_strategy and not hedge_put_strategy and not channel_strategy and not wheel_strategy and not grid_strategy and not leaps_strategy:
                # Open new positions when we have no open positions for a leg,
                # i.e. the day after the previous expiry's positions were closed.
                # Each leg tracks independently based on its expiry_months.
                for leg_idx, leg in enumerate(legs):
                    # Check if this leg already has an open position
                    has_open = any(
                        p.get("_leg_idx") == leg_idx for p in open_positions
                    )
                    if has_open:
                        continue

                    # Determine the next expiry to target
                    last_expiry = leg_last_roll.get(leg_idx)

                    if last_expiry is not None:
                        if today <= last_expiry:
                            continue
                        expiry = find_deribit_expiry(today, leg.expiry_months)
                    else:
                        expiry = find_deribit_expiry(today, leg.expiry_months)

                    # Don't open if expiry is too close
                    T = calculate_time_to_expiration(expiry, today)
                    if T <= 0.01:
                        continue

                    # Martingale: adjust multiplier at cycle boundary
                    # (when we're about to open the first leg of a new cycle)
                    if martingale and leg_idx == 0:
                        if last_expiry is not None:
                            if cycle_pnl < 0:
                                consecutive_losses += 1
                                if consecutive_losses <= max_double_times:
                                    martingale_multiplier *= 2
                                    total_doublings += 1
                                    if martingale_multiplier > max_multiplier_reached:
                                        max_multiplier_reached = martingale_multiplier
                                    print(f"[Martingale] Loss cycle (PnL={cycle_pnl:.2f}), doubling x{consecutive_losses}, multiplier={martingale_multiplier}")
                                else:
                                    print(f"[Martingale] Loss cycle but max doubles reached ({max_double_times}), keeping multiplier={martingale_multiplier}")
                            else:
                                if consecutive_losses > 0:
                                    print(f"[Martingale] Win cycle (PnL={cycle_pnl:.2f}), resetting multiplier from {martingale_multiplier} to 1")
                                consecutive_losses = 0
                                martingale_multiplier = 1.0
                            cycle_pnl = 0.0
                        print(f"[Martingale] Opening cycle: date={today}, multiplier={martingale_multiplier}, consecutive_losses={consecutive_losses}, cycle_pnl_before_reset={cycle_pnl}")

                    # Enhanced martingale: adjust accumulated loss and compute multiplier at cycle boundary
                    if enhanced_martingale and leg_idx == 0:
                        if last_expiry is not None:
                            if cycle_pnl < 0:
                                accumulated_loss += abs(cycle_pnl)
                                if last_cycle_premium > 0:
                                    target_revenue = accumulated_loss * enhanced_martingale_recover_pct
                                    enhanced_multiplier = target_revenue / last_cycle_premium
                                    enhanced_multiplier = max(1.0, min(enhanced_multiplier, enhanced_max_multiplier))
                                else:
                                    enhanced_multiplier = min(2.0, enhanced_max_multiplier)
                                total_doublings += 1
                                if enhanced_multiplier > max_multiplier_reached:
                                    max_multiplier_reached = enhanced_multiplier
                                print(f"[Enhanced Martingale] Loss cycle (PnL={cycle_pnl:.2f}), accumulated_loss={accumulated_loss:.2f}, "
                                      f"last_premium={last_cycle_premium:.2f}, new_multiplier={enhanced_multiplier:.2f} (cap={enhanced_max_multiplier})")
                            else:
                                accumulated_loss = max(0.0, accumulated_loss - cycle_pnl)
                                if accumulated_loss <= 0:
                                    enhanced_multiplier = 1.0
                                    print(f"[Enhanced Martingale] Win cycle (PnL={cycle_pnl:.2f}), loss fully recovered! Reset to 1x")
                                else:
                                    if last_cycle_premium > 0:
                                        target_revenue = accumulated_loss * enhanced_martingale_recover_pct
                                        enhanced_multiplier = target_revenue / last_cycle_premium
                                        enhanced_multiplier = max(1.0, min(enhanced_multiplier, enhanced_max_multiplier))
                                    print(f"[Enhanced Martingale] Win cycle (PnL={cycle_pnl:.2f}), remaining loss={accumulated_loss:.2f}, multiplier={enhanced_multiplier:.2f}")
                            cycle_pnl = 0.0
                            last_cycle_premium = 0.0
                        print(f"[Enhanced Martingale] Opening cycle: date={today}, accumulated_loss={accumulated_loss:.2f}, multiplier={enhanced_multiplier:.2f}")

                    # Apply multiplier to quantity
                    if enhanced_martingale:
                        actual_quantity = leg.quantity * enhanced_multiplier
                    elif martingale:
                        actual_quantity = leg.quantity * martingale_multiplier
                    else:
                        actual_quantity = leg.quantity

                    raw_strike = spot * (1 + leg.strike_offset_pct)
                    step = get_strike_step(underlying, spot)
                    strike = find_nearest_strike(raw_strike, step)
                    instrument = build_instrument_name(underlying, expiry, strike, leg.option_type)

                    print(f"[Deribit] Opening: {instrument}, spot={spot}, strike={strike}, expiry={expiry}, qty={actual_quantity}")

                    open_price, data_source, iv_used, open_smile = \
                        await get_option_price_via_smile(
                            client, underlying, expiry, strike,
                            spot, leg.option_type, today,
                        )

                    # Track base premium for this cycle (sold legs only, at base qty)
                    if enhanced_martingale and leg.quantity < 0:
                        last_cycle_premium += open_price * abs(leg.quantity) * contract_mult

                    premium_flow = -open_price * actual_quantity * contract_mult
                    equity += premium_flow

                    # Record the expiry we're targeting so we know when to roll next
                    leg_last_roll[leg_idx] = expiry

                    open_positions.append({
                        "option_type": leg.option_type,
                        "strike": strike,
                        "expiry": expiry,
                        "quantity": actual_quantity,
                        "open_price": open_price,
                        "open_date": today,
                        "open_spot": spot,
                        "iv_used": iv_used,
                        "data_source": data_source,
                        "instrument": instrument,
                        "open_smile": [{"strike": s, "iv": round(iv, 4)} for s, iv in open_smile],
                        "_leg_idx": leg_idx,
                    })

            # ── 3) Mark-to-market ──
            unrealized = 0.0
            for pos in open_positions:
                T_mtm = max(calculate_time_to_expiration(pos["expiry"], today), 0.0001)
                iv = pos["iv_used"] if pos["iv_used"] else 0.6
                current_price = black_scholes_price(
                    spot, pos["strike"], T_mtm, RISK_FREE_RATE, iv, pos["option_type"]
                )
                # Position value: for a sold option (qty<0), we owe current_price,
                # so unrealized = current_price * quantity (negative = liability).
                # equity already includes the premium received at open,
                # so unrealized should reflect the current cost to close.
                unrealized += current_price * pos["quantity"] * contract_mult

            # Wheel: if holding underlying (sell_call phase), add unrealized underlying PnL
            wheel_underlying_unrealized = 0.0
            if wheel_strategy and wheel_phase == "sell_call" and wheel_assigned_spot > 0:
                wheel_underlying_unrealized = (spot - wheel_assigned_spot) * abs(wheel_quantity) * contract_mult
                unrealized += wheel_underlying_unrealized

            # Channel: if holding underlying (sell_call phase), add unrealized underlying PnL
            channel_underlying_unrealized = 0.0
            if channel_strategy and channel_phase == "sell_call" and channel_assigned_spot > 0:
                channel_underlying_unrealized = (spot - channel_assigned_spot) * abs(channel_quantity) * contract_mult
                unrealized += channel_underlying_unrealized

            # Grid: if holding underlying (sell_call phase) at any grid level, add unrealized
            grid_underlying_unrealized = 0.0
            if grid_strategy:
                for gs, gl in grid_levels.items():
                    if gl["phase"] == "sell_call" and gl.get("assigned_spot", 0) > 0:
                        grid_underlying_unrealized += (spot - gl["assigned_spot"]) * abs(grid_quantity) * contract_mult
                unrealized += grid_underlying_unrealized

            equity_curve.append({
                "date": today.isoformat(),
                "equity": round(equity + unrealized, 2),
                "spot": spot,
                "open_positions": len(open_positions),
                "wheel_underlying_unrealized": round(wheel_underlying_unrealized, 2) if wheel_strategy else None,
                "wheel_underlying_realized": round(wheel_underlying_realized, 2) if wheel_strategy else None,
                "wheel_phase": wheel_phase if wheel_strategy else None,
                "channel_underlying_unrealized": round(channel_underlying_unrealized, 2) if channel_strategy else None,
                "channel_underlying_realized": round(channel_underlying_realized, 2) if channel_strategy else None,
                "channel_phase": channel_phase if channel_strategy else None,
                "grid_underlying_unrealized": round(grid_underlying_unrealized, 2) if grid_strategy else None,
                "grid_underlying_realized": round(grid_underlying_realized, 2) if grid_strategy else None,
                "leaps_strategy_enabled": leaps_strategy if leaps_strategy else None,
            })

    # ── Close remaining positions at end ──
    if open_positions and dates:
        last_date = dates[-1]
        last_spot = price_map[last_date]
        for pos in open_positions:
            T_final = max(calculate_time_to_expiration(pos["expiry"], last_date), 0.0001)
            iv = pos["iv_used"] if pos["iv_used"] else 0.6
            close_price = black_scholes_price(
                last_spot, pos["strike"], T_final, RISK_FREE_RATE, iv, pos["option_type"]
            )
            pnl = (pos["open_price"] - close_price) * (-pos["quantity"]) * contract_mult
            close_cash_flow = close_price * pos["quantity"] * contract_mult
            equity += close_cash_flow
            total_count += 1
            if pos["data_source"] == "iv_smile":
                real_count += 1

            # Track cycle PnL for martingale
            if martingale or enhanced_martingale:
                cycle_pnl += pnl

            trade_idx = len(trades)
            trades.append(RealTradeRecord(
                open_date=pos["open_date"].isoformat(),
                close_date=last_date.isoformat(),
                option_type=pos["option_type"],
                strike=round(pos["strike"], 2),
                quantity=pos["quantity"],
                open_price=round(pos["open_price"], 4),
                close_price=round(close_price, 4),
                pnl=round(pnl, 2),
                close_reason="回测结束平仓",
                instrument=pos["instrument"],
                data_source=pos["data_source"],
                iv_used=pos["iv_used"],
                open_spot=round(pos["open_spot"], 2),
                close_spot=round(last_spot, 2),
                strike_distance_pct=round((pos["open_spot"] - pos["strike"]) / pos["strike"] * 100, 2) if pos["strike"] else None,
                equity_after=round(equity, 2),
            ))

            iv_smiles.append({
                "trade_idx": trade_idx,
                "instrument": pos["instrument"],
                "open_date": pos["open_date"].isoformat(),
                "close_date": last_date.isoformat(),
                "open_smile": pos.get("open_smile", []),
                "close_smile": [],
                "open_spot": pos["open_spot"],
                "close_spot": last_spot,
                "strike": round(pos["strike"], 2),
                "option_type": pos["option_type"],
                "expiry": pos["expiry"].isoformat(),
            })

    # Wheel: if backtest ends while holding underlying (sell_call phase), add unrealized underlying PnL
    wheel_end_underlying_pnl = 0.0
    if wheel_strategy and wheel_phase == "sell_call" and wheel_assigned_spot > 0 and dates:
        last_spot_final = price_map[dates[-1]]
        wheel_end_underlying_pnl = (last_spot_final - wheel_assigned_spot) * abs(wheel_quantity) * contract_mult
        equity += wheel_end_underlying_pnl
        print(f"[Wheel] Backtest ended in sell_call phase: underlying PnL = ({last_spot_final:.0f} - {wheel_assigned_spot:.0f}) * {abs(wheel_quantity)} * {contract_mult} = {wheel_end_underlying_pnl:.2f}")

    # Channel: if backtest ends while holding underlying (sell_call phase), add unrealized underlying PnL
    channel_end_underlying_pnl = 0.0
    if channel_strategy and channel_phase == "sell_call" and channel_assigned_spot > 0 and dates:
        last_spot_final = price_map[dates[-1]]
        channel_end_underlying_pnl = (last_spot_final - channel_assigned_spot) * abs(channel_quantity) * contract_mult
        equity += channel_end_underlying_pnl
        print(f"[Channel] Backtest ended in sell_call phase: underlying PnL = ({last_spot_final:.0f} - {channel_assigned_spot:.0f}) * {abs(channel_quantity)} * {contract_mult} = {channel_end_underlying_pnl:.2f}")

    # Grid: if backtest ends while holding underlying at any grid level, add unrealized
    grid_end_underlying_pnl = 0.0
    if grid_strategy and dates:
        last_spot_final = price_map[dates[-1]]
        for gs, gl in grid_levels.items():
            if gl["phase"] == "sell_call" and gl.get("assigned_spot", 0) > 0:
                pnl_this = (last_spot_final - gl["assigned_spot"]) * abs(grid_quantity) * contract_mult
                grid_end_underlying_pnl += pnl_this
        if grid_end_underlying_pnl != 0:
            equity += grid_end_underlying_pnl
            print(f"[Grid] Backtest ended with underlying positions: PnL = {grid_end_underlying_pnl:.2f}")

    # ── Summary ──
    total_pnl = equity - initial_capital
    total_trades = len(trades)
    winning = [t for t in trades if t.pnl > 0]
    max_drawdown = 0.0
    peak = initial_capital
    for e in equity_curve:
        if e["equity"] > peak:
            peak = e["equity"]
        dd = (peak - e["equity"]) / peak if peak > 0 else 0
        if dd > max_drawdown:
            max_drawdown = dd

    real_data_pct = round(real_count / total_count * 100, 1) if total_count > 0 else 0.0

    summary = {
        "initial_capital": initial_capital,
        "final_equity": round(equity, 2),
        "total_pnl": round(total_pnl, 2),
        "total_return_pct": round(total_pnl / initial_capital * 100, 2),
        "max_drawdown_pct": round(max_drawdown * 100, 2),
        "total_trades": total_trades,
        "winning_trades": len(winning),
        "losing_trades": len([t for t in trades if t.pnl < 0]),
        "win_rate_pct": round(len(winning) / total_trades * 100, 1) if total_trades > 0 else 0,
        "avg_pnl": round(sum(t.pnl for t in trades) / total_trades, 2) if total_trades > 0 else 0,
        "real_data_pct": real_data_pct,
        "martingale_enabled": martingale,
        "enhanced_martingale_enabled": enhanced_martingale,
        "vol_strategy_enabled": vol_strategy,
        "roll_put_strategy_enabled": roll_put_strategy,
        "hedge_put_strategy_enabled": hedge_put_strategy,
        "channel_strategy_enabled": channel_strategy,
        "max_multiplier_used": max_multiplier_reached if (martingale or enhanced_martingale) else 1.0,
        "martingale_doublings": total_doublings if (martingale or enhanced_martingale) else 0,
        "accumulated_loss_remaining": round(accumulated_loss, 2) if enhanced_martingale else 0,
        "roll_put_loss_rolls": roll_put_loss_rolls if roll_put_strategy else 0,
        "roll_put_fresh_opens": roll_put_fresh_opens if roll_put_strategy else 0,
        "hedge_put_loss_rolls": hedge_put_loss_rolls if hedge_put_strategy else 0,
        "hedge_put_crash_hedges": hedge_put_crash_hedges if hedge_put_strategy else 0,
        "hedge_put_fresh_opens": hedge_put_fresh_opens if hedge_put_strategy else 0,
        "channel_fresh_opens": channel_fresh_opens if channel_strategy else 0,
        "channel_phase": channel_phase if channel_strategy else None,
        "channel_cycles": channel_cycles if channel_strategy else 0,
        "channel_put_sells": channel_put_sells if channel_strategy else 0,
        "channel_call_sells": channel_call_sells if channel_strategy else 0,
        "channel_assignments": channel_assignments if channel_strategy else 0,
        "channel_total_premium": round(channel_total_premium, 2) if channel_strategy else 0,
        "channel_end_underlying_pnl": round(channel_end_underlying_pnl, 2) if channel_strategy else 0,
        "channel_assigned_spot": round(channel_assigned_spot, 2) if channel_strategy and channel_phase == "sell_call" else None,
        "wheel_strategy_enabled": wheel_strategy,
        "wheel_phase": wheel_phase if wheel_strategy else None,
        "wheel_cycles": wheel_cycles if wheel_strategy else 0,
        "wheel_put_sells": wheel_put_sells if wheel_strategy else 0,
        "wheel_call_sells": wheel_call_sells if wheel_strategy else 0,
        "wheel_assignments": wheel_assignments if wheel_strategy else 0,
        "wheel_total_premium": round(wheel_total_premium, 2) if wheel_strategy else 0,
        "wheel_end_underlying_pnl": round(wheel_end_underlying_pnl, 2) if wheel_strategy else 0,
        "wheel_assigned_spot": round(wheel_assigned_spot, 2) if wheel_strategy and wheel_phase == "sell_call" else None,
        "wheel_assigned_strike": round(wheel_assigned_strike, 2) if wheel_strategy and wheel_phase == "sell_call" else None,
        "grid_strategy_enabled": grid_strategy,
        "grid_put_sells": grid_put_sells if grid_strategy else 0,
        "grid_call_sells": grid_call_sells if grid_strategy else 0,
        "grid_assignments": grid_assignments if grid_strategy else 0,
        "grid_cycles": grid_cycles if grid_strategy else 0,
        "grid_total_premium": round(grid_total_premium, 2) if grid_strategy else 0,
        "grid_skipped_low_yield": grid_skipped_low_yield if grid_strategy else 0,
        "grid_end_underlying_pnl": round(grid_end_underlying_pnl, 2) if grid_strategy else 0,
        "grid_active_levels": sum(1 for gl in grid_levels.values() if gl["phase"] != "idle") if grid_strategy else 0,
        "leaps_strategy_enabled": leaps_strategy,
        "leaps_total_cost": round(leaps_total_cost, 2) if leaps_strategy else 0,
        "leaps_total_proceeds": round(leaps_total_proceeds, 2) if leaps_strategy else 0,
        "leaps_trades_count": leaps_trades_count if leaps_strategy else 0,
    }

    return RealBacktestResult(
        equity_curve=equity_curve,
        trades=[t.model_dump() for t in trades],
        summary=summary,
        iv_smiles=iv_smiles,
    )


# ── API endpoints ───────────────────────────────────────────────────────────

@router.post("/real-backtest", response_model=RealBacktestResult)
async def real_backtest_api(request: RealBacktestRequest):
    """Run real data backtest using Deribit IV smile interpolation."""
    if request.start_date >= request.end_date:
        raise HTTPException(status_code=400, detail="开始日期必须早于结束日期")
    if not request.vol_strategy and not request.roll_put_strategy and not request.hedge_put_strategy and not request.channel_strategy and not request.wheel_strategy and not request.grid_strategy and not request.leaps_strategy and not request.legs:
        raise HTTPException(status_code=400, detail="至少需要一个策略腿")

    try:
        price_map = await fetch_deribit_index_prices(
            request.underlying, request.start_date, request.end_date
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取Deribit指数数据失败: {str(e)}")

    if not price_map:
        raise HTTPException(status_code=400, detail="无法获取指定时间段的Deribit价格数据")

    try:
        result = await run_real_backtest_engine(
            price_map=price_map,
            legs=request.legs,
            roll_day=request.roll_day,
            close_days_before=request.close_days_before_expiry,
            initial_capital=request.initial_capital,
            contract_mult=request.contract_multiplier,
            underlying=request.underlying,
            martingale=request.martingale,
            max_double_times=request.max_double_times,
            enhanced_martingale=request.enhanced_martingale,
            enhanced_martingale_recover_pct=request.enhanced_martingale_recover_pct,
            enhanced_max_multiplier=request.enhanced_max_multiplier,
            vol_strategy=request.vol_strategy,
            vol_sell_iv=request.vol_sell_iv,
            vol_buy_iv=request.vol_buy_iv,
            vol_quantity=request.vol_quantity,
            vol_strike_offset_pct=request.vol_strike_offset_pct / 100.0,
            roll_put_strategy=request.roll_put_strategy,
            roll_put_offset_pct=request.roll_put_offset_pct,
            roll_put_quantity=request.roll_put_quantity,
            hedge_put_strategy=request.hedge_put_strategy,
            hedge_put_offset_pct=request.hedge_put_offset_pct,
            hedge_put_quantity=request.hedge_put_quantity,
            hedge_put_crash_pct=request.hedge_put_crash_pct,
            hedge_put_hedge_quantity=request.hedge_put_hedge_quantity,
            channel_strategy=request.channel_strategy,
            channel_lookback_days=request.channel_lookback_days,
            channel_quantity=request.channel_quantity,
            wheel_strategy=request.wheel_strategy,
            wheel_put_offset_pct=request.wheel_put_offset_pct,
            wheel_call_offset_pct=request.wheel_call_offset_pct,
            wheel_quantity=request.wheel_quantity,
            wheel_reinvest=request.wheel_reinvest,
            grid_strategy=request.grid_strategy,
            grid_step=request.grid_step,
            grid_quantity=request.grid_quantity,
            grid_min_yield_pct=request.grid_min_yield_pct,
            grid_range_up=request.grid_range_up,
            grid_range_down=request.grid_range_down,
            grid_max_positions=request.grid_max_positions,
            leaps_strategy=request.leaps_strategy,
            leaps_max_annual_tv_pct=request.leaps_max_annual_tv_pct,
            leaps_min_months=request.leaps_min_months,
            leaps_close_days_before=request.leaps_close_days_before,
            leaps_quantity=request.leaps_quantity,
            leaps_num_strikes=request.leaps_num_strikes,
        )
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"真实数据回测失败: {str(e)}")

    return result


@router.post("/real-backtest-stream")
async def real_backtest_stream_api(request: RealBacktestRequest):
    """Run real data backtest with SSE progress streaming."""
    if request.start_date >= request.end_date:
        raise HTTPException(status_code=400, detail="开始日期必须早于结束日期")
    if not request.vol_strategy and not request.roll_put_strategy and not request.hedge_put_strategy and not request.channel_strategy and not request.wheel_strategy and not request.grid_strategy and not request.leaps_strategy and not request.legs:
        raise HTTPException(status_code=400, detail="至少需要一个策略腿")

    async def event_generator():
        try:
            price_map = await fetch_deribit_index_prices(
                request.underlying, request.start_date, request.end_date
            )
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': f'获取Deribit指数数据失败: {str(e)}'})}\n\n"
            return

        if not price_map:
            yield f"data: {json.dumps({'type': 'error', 'message': '无法获取指定时间段的Deribit价格数据'})}\n\n"
            return

        # Send initial progress
        yield f"data: {json.dumps({'type': 'progress', 'day': 0, 'total': len(price_map), 'date': request.start_date.isoformat(), 'pct': 0})}\n\n"

        async def on_progress(day_idx, total_days, current_date):
            pass  # placeholder, we use a queue instead

        progress_queue = asyncio.Queue()

        async def progress_callback(day_idx, total_days, current_date):
            await progress_queue.put({
                'type': 'progress',
                'day': day_idx,
                'total': total_days,
                'date': current_date.isoformat(),
                'pct': round(day_idx / total_days * 100, 1),
            })

        # Run backtest in a task so we can stream progress
        async def run_backtest():
            return await run_real_backtest_engine(
                price_map=price_map,
                legs=request.legs,
                roll_day=request.roll_day,
                close_days_before=request.close_days_before_expiry,
                initial_capital=request.initial_capital,
                contract_mult=request.contract_multiplier,
                underlying=request.underlying,
                progress_callback=progress_callback,
                martingale=request.martingale,
                max_double_times=request.max_double_times,
                enhanced_martingale=request.enhanced_martingale,
                enhanced_martingale_recover_pct=request.enhanced_martingale_recover_pct,
                enhanced_max_multiplier=request.enhanced_max_multiplier,
                vol_strategy=request.vol_strategy,
                vol_sell_iv=request.vol_sell_iv,
                vol_buy_iv=request.vol_buy_iv,
                vol_quantity=request.vol_quantity,
                vol_strike_offset_pct=request.vol_strike_offset_pct / 100.0,
                roll_put_strategy=request.roll_put_strategy,
                roll_put_offset_pct=request.roll_put_offset_pct,
                roll_put_quantity=request.roll_put_quantity,
                hedge_put_strategy=request.hedge_put_strategy,
                hedge_put_offset_pct=request.hedge_put_offset_pct,
                hedge_put_quantity=request.hedge_put_quantity,
                hedge_put_crash_pct=request.hedge_put_crash_pct,
                hedge_put_hedge_quantity=request.hedge_put_hedge_quantity,
                channel_strategy=request.channel_strategy,
                channel_lookback_days=request.channel_lookback_days,
                channel_quantity=request.channel_quantity,
                wheel_strategy=request.wheel_strategy,
                wheel_put_offset_pct=request.wheel_put_offset_pct,
                wheel_call_offset_pct=request.wheel_call_offset_pct,
                wheel_quantity=request.wheel_quantity,
                wheel_reinvest=request.wheel_reinvest,
                grid_strategy=request.grid_strategy,
                grid_step=request.grid_step,
                grid_quantity=request.grid_quantity,
                grid_min_yield_pct=request.grid_min_yield_pct,
                grid_range_up=request.grid_range_up,
                grid_range_down=request.grid_range_down,
                grid_max_positions=request.grid_max_positions,
                leaps_strategy=request.leaps_strategy,
                leaps_max_annual_tv_pct=request.leaps_max_annual_tv_pct,
                leaps_min_months=request.leaps_min_months,
                leaps_close_days_before=request.leaps_close_days_before,
                leaps_quantity=request.leaps_quantity,
                leaps_num_strikes=request.leaps_num_strikes,
            )

        task = asyncio.create_task(run_backtest())

        # Stream progress events until task completes
        while not task.done():
            try:
                msg = await asyncio.wait_for(progress_queue.get(), timeout=0.5)
                yield f"data: {json.dumps(msg)}\n\n"
            except asyncio.TimeoutError:
                continue

        # Drain remaining progress messages
        while not progress_queue.empty():
            msg = await progress_queue.get()
            yield f"data: {json.dumps(msg)}\n\n"

        # Get result or error
        try:
            result = task.result()
            yield f"data: {json.dumps({'type': 'progress', 'day': 1, 'total': 1, 'date': request.end_date.isoformat(), 'pct': 100})}\n\n"
            result_data = {
                'type': 'result',
                'data': {
                    'equity_curve': result.equity_curve,
                    'trades': result.trades,
                    'summary': result.summary,
                    'iv_smiles': result.iv_smiles,
                },
            }
            yield f"data: {json.dumps(result_data)}\n\n"
        except Exception as e:
            import traceback
            traceback.print_exc()
            yield f"data: {json.dumps({'type': 'error', 'message': f'真实数据回测失败: {str(e)}'})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.delete("/cache")
async def clear_cache():
    """Clear all Deribit cached data."""
    db = SessionLocal()
    try:
        db.query(DeribitPriceCache).delete()
        db.query(DeribitIVCache).delete()
        db.commit()
        return {"message": "缓存已清除"}
    finally:
        db.close()


@router.delete("/cache/iv")
async def clear_iv_cache():
    """Clear only IV smile cache (keep price cache)."""
    db = SessionLocal()
    try:
        count = db.query(DeribitIVCache).delete()
        db.commit()
        return {"message": f"已清除 {count} 条IV缓存记录"}
    finally:
        db.close()


@router.get("/cache/stats")
async def cache_stats():
    """Get cache statistics."""
    db = SessionLocal()
    try:
        price_count = db.query(DeribitPriceCache).count()
        iv_count = db.query(DeribitIVCache).filter(DeribitIVCache.strike > 0).count()
        # Count any legacy sentinel rows that might still exist
        empty_count = db.query(DeribitIVCache).filter(DeribitIVCache.strike <= 0).count()
        return {
            "price_records": price_count,
            "iv_smile_points": iv_count,
            "empty_smile_markers": empty_count,
            "note": "Empty markers are legacy and will be ignored. They can be safely deleted."
        }
    finally:
        db.close()


# ── Verification endpoint ───────────────────────────────────────────────────

class VerifyRequest(BaseModel):
    underlying: str = Field(default="BTC")
    start_date: date
    end_date: date
    initial_capital: float = Field(default=10000.0)
    close_days_before_expiry: int = Field(default=1)
    legs: List[RealBacktestLeg]


@router.post("/verify-backtest")
async def verify_backtest(request: VerifyRequest):
    """Run a backtest with detailed step-by-step accounting log for verification."""
    try:
        price_map = await fetch_deribit_index_prices(
            request.underlying, request.start_date, request.end_date
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    if not price_map:
        raise HTTPException(status_code=400, detail="无价格数据")

    equity = request.initial_capital
    open_positions = []
    trades = []
    log = []
    leg_last_roll: Dict[int, date] = {}
    contract_mult = 1.0

    dates = sorted(price_map.keys())

    async with create_http_client() as client:
        for today in dates:
            spot = price_map[today]
            day_events = []

            # Close positions near expiry
            still_open = []
            for pos in open_positions:
                days_to_exp = (pos["expiry"] - today).days

                if days_to_exp <= request.close_days_before_expiry and days_to_exp > 0:
                    close_price, src, iv, _ = await get_option_price_via_smile(
                        client, request.underlying, pos["expiry"], pos["strike"],
                        spot, pos["option_type"], today,
                    )
                    close_cash = close_price * pos["quantity"] * contract_mult
                    pnl = (pos["open_price"] - close_price) * (-pos["quantity"]) * contract_mult
                    equity += close_cash
                    day_events.append({
                        "action": "CLOSE",
                        "reason": f"到期前平仓 (距到期{days_to_exp}天)",
                        "instrument": pos["instrument"],
                        "open_price": round(pos["open_price"], 4),
                        "close_price": round(close_price, 4),
                        "quantity": pos["quantity"],
                        "close_cash_flow": round(close_cash, 2),
                        "pnl": round(pnl, 2),
                        "equity_after": round(equity, 2),
                        "data_source": src,
                    })
                    trades.append({
                        "open_date": pos["open_date"].isoformat(),
                        "close_date": today.isoformat(),
                        "instrument": pos["instrument"],
                        "option_type": pos["option_type"],
                        "strike": pos["strike"],
                        "quantity": pos["quantity"],
                        "open_price": round(pos["open_price"], 4),
                        "close_price": round(close_price, 4),
                        "open_spot": round(pos["open_spot"], 2),
                        "close_spot": round(spot, 2),
                        "pnl": round(pnl, 2),
                        "equity_after": round(equity, 2),
                    })
                elif days_to_exp <= 0:
                    if pos["option_type"] == "PUT":
                        close_price = max(pos["strike"] - spot, 0.0)
                    else:
                        close_price = max(spot - pos["strike"], 0.0)
                    close_cash = close_price * pos["quantity"] * contract_mult
                    pnl = (pos["open_price"] - close_price) * (-pos["quantity"]) * contract_mult
                    equity += close_cash
                    day_events.append({
                        "action": "EXPIRE",
                        "reason": f"到期结算 (内在价值=${close_price:.2f})",
                        "instrument": pos["instrument"],
                        "open_price": round(pos["open_price"], 4),
                        "close_price": round(close_price, 4),
                        "quantity": pos["quantity"],
                        "close_cash_flow": round(close_cash, 2),
                        "pnl": round(pnl, 2),
                        "equity_after": round(equity, 2),
                    })
                    trades.append({
                        "open_date": pos["open_date"].isoformat(),
                        "close_date": today.isoformat(),
                        "instrument": pos["instrument"],
                        "option_type": pos["option_type"],
                        "strike": pos["strike"],
                        "quantity": pos["quantity"],
                        "open_price": round(pos["open_price"], 4),
                        "close_price": round(close_price, 4),
                        "open_spot": round(pos["open_spot"], 2),
                        "close_spot": round(spot, 2),
                        "pnl": round(pnl, 2),
                        "equity_after": round(equity, 2),
                    })
                else:
                    still_open.append(pos)

            open_positions = still_open

            # Open new positions
            for leg_idx, leg in enumerate(request.legs):
                has_open = any(p.get("_leg_idx") == leg_idx for p in open_positions)
                if has_open:
                    continue
                last_expiry = leg_last_roll.get(leg_idx)
                if last_expiry is not None and today <= last_expiry:
                    continue
                expiry = find_deribit_expiry(today, leg.expiry_months)
                T = calculate_time_to_expiration(expiry, today)
                if T <= 0.01:
                    continue

                raw_strike = spot * (1 + leg.strike_offset_pct)
                step = get_strike_step(request.underlying, spot)
                strike = find_nearest_strike(raw_strike, step)
                instrument = build_instrument_name(request.underlying, expiry, strike, leg.option_type)

                open_price, src, iv, _ = await get_option_price_via_smile(
                    client, request.underlying, expiry, strike,
                    spot, leg.option_type, today,
                )
                premium_flow = -open_price * leg.quantity * contract_mult
                equity += premium_flow
                leg_last_roll[leg_idx] = expiry

                open_positions.append({
                    "option_type": leg.option_type,
                    "strike": strike,
                    "expiry": expiry,
                    "quantity": leg.quantity,
                    "open_price": open_price,
                    "open_date": today,
                    "open_spot": spot,
                    "iv_used": iv,
                    "data_source": src,
                    "instrument": instrument,
                    "_leg_idx": leg_idx,
                })

                day_events.append({
                    "action": "OPEN",
                    "instrument": instrument,
                    "expiry": expiry.isoformat(),
                    "strike": strike,
                    "quantity": leg.quantity,
                    "open_price": round(open_price, 4),
                    "premium_cash_flow": round(premium_flow, 2),
                    "equity_after": round(equity, 2),
                    "spot": round(spot, 2),
                    "iv": round(iv, 4) if iv else None,
                    "data_source": src,
                })

            # MTM
            unrealized = 0.0
            for pos in open_positions:
                T_mtm = max(calculate_time_to_expiration(pos["expiry"], today), 0.0001)
                iv = pos["iv_used"] if pos["iv_used"] else 0.6
                cur_price = black_scholes_price(
                    spot, pos["strike"], T_mtm, RISK_FREE_RATE, iv, pos["option_type"]
                )
                unrealized += cur_price * pos["quantity"] * contract_mult

            if day_events:
                log.append({
                    "date": today.isoformat(),
                    "spot": round(spot, 2),
                    "cash": round(equity, 2),
                    "unrealized": round(unrealized, 2),
                    "total_equity": round(equity + unrealized, 2),
                    "open_positions": len(open_positions),
                    "events": day_events,
                })

    return {
        "log": log,
        "trades": trades,
        "final_cash": round(equity, 2),
        "total_trades": len(trades),
        "total_pnl": round(equity - request.initial_capital, 2),
    }
