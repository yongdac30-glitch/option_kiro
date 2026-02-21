"""Deribit data debug/test endpoints.
Allows manual testing of instrument lookups, trade fetching, IV calculation,
and cache inspection."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import List, Optional, Dict
from datetime import datetime, date, timedelta, timezone
import httpx
import asyncio

from app.services.pricing import (
    black_scholes_price,
    calculate_time_to_expiration,
    implied_volatility,
)
from app.core.database import SessionLocal
from app.core.config import create_http_client
from app.models.deribit_cache import DeribitPriceCache, DeribitIVCache
from app.api.deribit import (
    DERIBIT_BASE, RATE_DELAY, RISK_FREE_RATE,
    build_instrument_name, find_nearest_strike, get_strike_step,
    format_deribit_date, last_friday_of_month, find_deribit_expiry,
    get_cached_iv_smile, save_cached_iv_smile,
    fetch_trades_for_instrument,
)

router = APIRouter(prefix="/api/deribit-debug", tags=["deribit-debug"])


# ── 0) Deribit expiry dates helper ─────────────────────────────────────────

@router.get("/expiry-dates")
async def get_expiry_dates(
    start_year: int = 2023,
    end_year: int = 2026,
):
    """Generate all valid Deribit monthly expiry dates (last Friday of each month)
    for a given year range. Useful for populating dropdowns."""
    expiries = []
    for year in range(start_year, end_year + 1):
        for month in range(1, 13):
            d = last_friday_of_month(year, month)
            expiries.append({
                "date": d.isoformat(),
                "label": f"{d.isoformat()} ({format_deribit_date(d)})",
                "deribit_str": format_deribit_date(d),
            })
    return {"expiries": expiries}


@router.get("/build-instrument")
async def preview_instrument(
    underlying: str = "BTC",
    expiry_date: str = "2025-03-28",
    strike: float = 80000,
    option_type: str = "PUT",
):
    """Preview what instrument name would be generated for given parameters."""
    try:
        exp = date.fromisoformat(expiry_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format, use YYYY-MM-DD")
    name = build_instrument_name(underlying, exp, strike, option_type)
    return {"instrument_name": name, "expiry_date": expiry_date, "deribit_date": format_deribit_date(exp)}


# ── 1) List available instruments for a given expiry ────────────────────────

@router.get("/instruments")
async def list_instruments(
    underlying: str = "BTC",
    expired: bool = True,
):
    """Fetch instruments from Deribit (active or expired options)."""
    url = f"{DERIBIT_BASE}/public/get_instruments"
    params = {
        "currency": underlying,
        "kind": "option",
        "expired": str(expired).lower(),
    }
    async with create_http_client() as client:
        resp = await client.get(url, params=params)
        resp.raise_for_status()
        data = resp.json()

    if "error" in data:
        raise HTTPException(status_code=400, detail=data["error"])

    instruments = data.get("result", [])
    # Return summary
    summary = []
    for inst in instruments:
        summary.append({
            "instrument_name": inst.get("instrument_name"),
            "strike": inst.get("strike"),
            "option_type": inst.get("option_type"),
            "expiration_timestamp": inst.get("expiration_timestamp"),
            "creation_timestamp": inst.get("creation_timestamp"),
            "is_active": inst.get("is_active"),
            "settlement_period": inst.get("settlement_period"),
        })

    # Sort by expiration then strike
    summary.sort(key=lambda x: (x.get("expiration_timestamp", 0), x.get("strike", 0)))

    return {
        "count": len(summary),
        "instruments": summary[:500],  # cap to avoid huge response
    }


# ── 2) Test fetching trades for a specific instrument ──────────────────────

class TradeTestRequest(BaseModel):
    instrument: str = Field(..., description="e.g. BTC-26APR24-64000-P")
    target_date: date = Field(..., description="Date to search around")
    window_days: int = Field(default=5)
    # Optional fields for IV calculation on chart data
    strike: Optional[float] = Field(default=None, description="Strike price for IV calc")
    option_type: Optional[str] = Field(default=None, description="PUT or CALL")
    expiry_date: Optional[date] = Field(default=None, description="Expiry date for IV calc")
    spot_price: Optional[float] = Field(default=None, description="Spot price (auto-fetched if empty)")


@router.post("/test-trades")
async def test_fetch_trades(req: TradeTestRequest):
    """Test fetching trades for a specific instrument around a date.
    Tries both trade API and chart data API, returns results from both."""
    start_ts = int(datetime.combine(
        req.target_date - timedelta(days=req.window_days), datetime.min.time(),
        tzinfo=timezone.utc
    ).timestamp() * 1000)
    end_ts = int(datetime.combine(
        req.target_date + timedelta(days=req.window_days + 1), datetime.min.time(),
        tzinfo=timezone.utc
    ).timestamp() * 1000)

    async with create_http_client() as client:
        # ── Method 1: Trade API ──
        url1 = f"{DERIBIT_BASE}/public/get_last_trades_by_instrument_and_time"
        params1 = {
            "instrument_name": req.instrument,
            "start_timestamp": start_ts,
            "end_timestamp": end_ts,
            "count": 50,
            "sorting": "asc",
        }
        trades = []
        trade_error = None
        try:
            resp1 = await client.get(url1, params=params1)
            raw_body1 = resp1.json()
            if "error" in raw_body1:
                trade_error = raw_body1["error"]
            else:
                raw_trades = raw_body1.get("result", {}).get("trades", [])
                for t in raw_trades:
                    ts = t.get("timestamp", 0)
                    trades.append({
                        "timestamp": ts,
                        "datetime": datetime.fromtimestamp(ts / 1000, tz=timezone.utc).isoformat() if ts else None,
                        "price": t.get("price"),
                        "amount": t.get("amount"),
                        "direction": t.get("direction"),
                        "index_price": t.get("index_price"),
                        "iv": t.get("iv"),
                        "instrument_name": t.get("instrument_name"),
                        "source": "trade_api",
                    })
        except Exception as e:
            trade_error = str(e)

        await asyncio.sleep(RATE_DELAY)

        # ── Method 2: Chart Data API (fallback for expired contracts) ──
        url2 = f"{DERIBIT_BASE}/public/get_tradingview_chart_data"
        params2 = {
            "instrument_name": req.instrument,
            "start_timestamp": start_ts,
            "end_timestamp": end_ts,
            "resolution": "1D",
        }
        chart_data = []
        chart_error = None

        # Try to get spot price for IV calculation
        spot_for_iv = req.spot_price
        spot_map = {}
        if spot_for_iv is None and (req.strike and req.option_type and req.expiry_date):
            # Try to parse underlying from instrument name (e.g. BTC-28MAR25-80000-P -> BTC)
            underlying = req.instrument.split("-")[0] if "-" in req.instrument else "BTC"
            try:
                spot_url = f"{DERIBIT_BASE}/public/get_tradingview_chart_data"
                spot_params = {
                    "instrument_name": f"{underlying}-PERPETUAL",
                    "start_timestamp": start_ts,
                    "end_timestamp": end_ts,
                    "resolution": "1D",
                }
                spot_resp = await client.get(spot_url, params=spot_params)
                spot_data = spot_resp.json().get("result", {})
                spot_closes = spot_data.get("close", [])
                spot_ticks = spot_data.get("ticks", [])
                if spot_closes:
                    # Build a date->spot map for per-candle IV calc
                    for st, sc in zip(spot_ticks, spot_closes):
                        sd = datetime.fromtimestamp(st / 1000, tz=timezone.utc).date()
                        spot_map[sd] = sc
                    # Use the closest spot to target_date as default
                    target_ts_val = int(datetime.combine(
                        req.target_date, datetime.min.time(), tzinfo=timezone.utc
                    ).timestamp() * 1000)
                    best_idx = min(range(len(spot_ticks)), key=lambda i: abs(spot_ticks[i] - target_ts_val))
                    spot_for_iv = spot_closes[best_idx]
                await asyncio.sleep(RATE_DELAY)
            except Exception:
                pass

        try:
            resp2 = await client.get(url2, params=params2)
            raw_body2 = resp2.json()
            if "error" in raw_body2:
                chart_error = raw_body2["error"]
            else:
                result2 = raw_body2.get("result", {})
                if result2:
                    ticks = result2.get("ticks", [])
                    opens = result2.get("open", [])
                    highs = result2.get("high", [])
                    lows = result2.get("low", [])
                    closes = result2.get("close", [])
                    volumes = result2.get("volume", [])
                    for i, ts in enumerate(ticks):
                        close_price = closes[i] if i < len(closes) else None
                        entry = {
                            "timestamp": ts,
                            "datetime": datetime.fromtimestamp(ts / 1000, tz=timezone.utc).isoformat(),
                            "open": opens[i] if i < len(opens) else None,
                            "high": highs[i] if i < len(highs) else None,
                            "low": lows[i] if i < len(lows) else None,
                            "close": close_price,
                            "volume": volumes[i] if i < len(volumes) else None,
                            "source": "chart_api",
                            "iv_calculated": None,
                            "price_usd": None,
                            "spot_used": None,
                        }

                        # Calculate IV if we have the required params
                        candle_date = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).date()
                        candle_spot = spot_map.get(candle_date, spot_for_iv) if spot_map else spot_for_iv
                        if (close_price and close_price > 0 and candle_spot and candle_spot > 0
                                and req.strike and req.option_type and req.expiry_date):
                            price_usd = close_price * candle_spot
                            T = calculate_time_to_expiration(req.expiry_date, candle_date)
                            if T > 0.0001 and price_usd > 0:
                                iv = implied_volatility(
                                    market_price=price_usd,
                                    S=candle_spot, K=req.strike, T=T, r=RISK_FREE_RATE,
                                    option_type=req.option_type,
                                )
                                if iv is not None and 0.01 < iv < 10.0:
                                    entry["iv_calculated"] = round(iv, 6)
                                entry["price_usd"] = round(price_usd, 4)
                                entry["spot_used"] = round(candle_spot, 2)

                        chart_data.append(entry)
        except Exception as e:
            chart_error = str(e)

    return {
        "instrument": req.instrument,
        "target_date": req.target_date.isoformat(),
        "window_days": req.window_days,
        "search_range": {
            "from": (req.target_date - timedelta(days=req.window_days)).isoformat(),
            "to": (req.target_date + timedelta(days=req.window_days)).isoformat(),
        },
        "trade_api": {
            "count": len(trades),
            "error": trade_error,
            "data": trades,
        },
        "chart_api": {
            "count": len(chart_data),
            "error": chart_error,
            "data": chart_data,
        },
    }


# ── 3) Test IV smile fetch for a specific expiry + date ─────────────────────

class SmileTestRequest(BaseModel):
    underlying: str = Field(default="BTC")
    expiry_date: date = Field(..., description="Option expiry date")
    target_date: date = Field(..., description="Date to fetch smile for")
    option_type: str = Field(default="PUT", pattern="^(PUT|CALL)$")
    spot_price: Optional[float] = Field(default=None, description="Override spot price (auto-fetched if empty)")
    num_strikes: int = Field(default=7)
    window_days: int = Field(default=5)


@router.post("/test-smile")
async def test_fetch_smile(req: SmileTestRequest):
    """Test fetching IV smile for a specific expiry/date combination.
    Shows detailed per-strike results for debugging."""

    # Get spot price if not provided
    spot = req.spot_price
    if spot is None:
        # Fetch from Deribit
        url = f"{DERIBIT_BASE}/public/get_tradingview_chart_data"
        instrument = f"{req.underlying}-PERPETUAL"
        ts_start = int(datetime.combine(
            req.target_date - timedelta(days=1), datetime.min.time(),
            tzinfo=timezone.utc
        ).timestamp() * 1000)
        ts_end = int(datetime.combine(
            req.target_date + timedelta(days=2), datetime.min.time(),
            tzinfo=timezone.utc
        ).timestamp() * 1000)
        async with create_http_client() as client:
            resp = await client.get(url, params={
                "instrument_name": instrument,
                "start_timestamp": ts_start,
                "end_timestamp": ts_end,
                "resolution": "1D",
            })
            data = resp.json().get("result", {})
            closes = data.get("close", [])
            if closes:
                spot = closes[-1]
            else:
                raise HTTPException(status_code=400, detail=f"无法获取{req.target_date}的现货价格")

    step = get_strike_step(req.underlying, spot)
    atm_strike = find_nearest_strike(spot, step)

    candidates = []
    for i in range(-req.num_strikes, req.num_strikes + 1):
        s = atm_strike + i * step
        if s > 0:
            candidates.append(s)
    candidates.sort(key=lambda s: abs(s - spot))
    candidates = candidates[:req.num_strikes * 2]

    T = calculate_time_to_expiration(req.expiry_date, req.target_date)

    results = []
    smile_points = []
    db_points = []

    async with create_http_client() as client:
        for strike in sorted(candidates):
            instrument = build_instrument_name(req.underlying, req.expiry_date, strike, req.option_type)

            trade = await fetch_trades_for_instrument(
                client, instrument, req.target_date, window_days=req.window_days
            )

            entry = {
                "strike": strike,
                "instrument": instrument,
                "distance_pct": round((strike - spot) / spot * 100, 2),
                "trade_found": trade is not None,
                "trade_price_btc": None,
                "trade_price_usd": None,
                "trade_datetime": None,
                "trade_iv_deribit": None,
                "iv_calculated": None,
                "bs_price_check": None,
                "status": "no_trade",
            }

            if trade:
                trade_price_btc = trade.get("price", 0)
                trade_price_usd = trade_price_btc * spot
                ts = trade.get("timestamp", 0)

                entry["trade_price_btc"] = round(trade_price_btc, 8)
                entry["trade_price_usd"] = round(trade_price_usd, 4)
                entry["trade_datetime"] = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).isoformat() if ts else None
                entry["trade_iv_deribit"] = trade.get("iv")
                entry["trade_amount"] = trade.get("amount")
                entry["trade_direction"] = trade.get("direction")
                entry["index_price_at_trade"] = trade.get("index_price")

                if trade_price_usd > 0 and T > 0.0001:
                    iv = implied_volatility(
                        market_price=trade_price_usd,
                        S=spot, K=strike, T=T, r=RISK_FREE_RATE,
                        option_type=req.option_type,
                    )
                    if iv is not None and 0.01 < iv < 10.0:
                        entry["iv_calculated"] = round(iv, 6)
                        bs_check = black_scholes_price(spot, strike, T, RISK_FREE_RATE, iv, req.option_type)
                        entry["bs_price_check"] = round(bs_check, 4)
                        entry["status"] = "ok"
                        smile_points.append({"strike": strike, "iv": round(iv, 4)})
                        db_points.append((strike, iv, trade_price_usd, instrument))
                    else:
                        entry["iv_calculated"] = round(iv, 6) if iv else None
                        entry["status"] = "iv_out_of_range"
                else:
                    entry["status"] = "price_zero_or_expired"

            results.append(entry)

    # Check cache
    cached = get_cached_iv_smile(req.underlying, req.expiry_date, req.option_type, req.target_date)
    cached_points = [{"strike": s, "iv": round(iv, 4)} for s, iv, _ in cached]

    return {
        "underlying": req.underlying,
        "expiry_date": req.expiry_date.isoformat(),
        "target_date": req.target_date.isoformat(),
        "option_type": req.option_type,
        "spot_price": round(spot, 2),
        "atm_strike": atm_strike,
        "strike_step": step,
        "time_to_expiry_years": round(T, 6),
        "time_to_expiry_days": round(T * 365, 1),
        "candidates_tested": len(candidates),
        "trades_found": sum(1 for r in results if r["trade_found"]),
        "valid_iv_points": len(smile_points),
        "smile_points": smile_points,
        "cached_points": cached_points,
        "details": results,
    }


# ── 4) Save smile to cache ─────────────────────────────────────────────────

class SaveSmileRequest(BaseModel):
    underlying: str = Field(default="BTC")
    expiry_date: date
    target_date: date
    option_type: str = Field(default="PUT", pattern="^(PUT|CALL)$")
    spot_price: float
    points: List[dict]  # [{strike, iv, trade_price_usd, instrument}]


@router.post("/save-smile")
async def save_smile_to_cache(req: SaveSmileRequest):
    """Manually save IV smile points to cache."""
    db_points = []
    for p in req.points:
        db_points.append((
            p["strike"], p["iv"],
            p.get("trade_price_usd", 0),
            p.get("instrument", "MANUAL"),
        ))
    save_cached_iv_smile(
        req.underlying, req.expiry_date, req.option_type,
        req.target_date, req.spot_price, db_points,
    )
    return {"saved": len(db_points)}


# ── 5) Cache inspection ────────────────────────────────────────────────────

@router.get("/cache/iv-data")
async def get_iv_cache_data(
    underlying: str = "BTC",
    limit: int = 200,
):
    """Get all cached IV data for inspection."""
    db = SessionLocal()
    try:
        rows = db.query(DeribitIVCache).filter(
            DeribitIVCache.underlying == underlying,
            DeribitIVCache.strike > 0,
        ).order_by(
            DeribitIVCache.target_date.desc(),
            DeribitIVCache.expiry_date,
            DeribitIVCache.strike,
        ).limit(limit).all()

        data = []
        for r in rows:
            data.append({
                "id": r.id,
                "underlying": r.underlying,
                "expiry_date": r.expiry_date.isoformat() if r.expiry_date else None,
                "option_type": r.option_type,
                "target_date": r.target_date.isoformat() if r.target_date else None,
                "spot_price": r.spot_price,
                "strike": r.strike,
                "iv": round(r.iv, 6),
                "trade_price_usd": round(r.trade_price_usd, 4),
                "instrument": r.instrument,
            })

        return {"count": len(data), "data": data}
    finally:
        db.close()


# ── 6) Batch smile fetch ───────────────────────────────────────────────────

class BatchSmileRequest(BaseModel):
    underlying: str = Field(default="BTC")
    expiry_date: date
    option_type: str = Field(default="PUT", pattern="^(PUT|CALL)$")
    start_date: date
    end_date: date
    num_strikes: int = Field(default=7)
    window_days: int = Field(default=5)
    save_to_cache: bool = Field(default=True)


@router.post("/batch-smile")
async def batch_fetch_smiles(req: BatchSmileRequest):
    """Batch fetch IV smiles for a date range. Useful for pre-populating cache."""

    # First get price data for the range
    url = f"{DERIBIT_BASE}/public/get_tradingview_chart_data"
    instrument = f"{req.underlying}-PERPETUAL"
    start_ts = int(datetime.combine(
        req.start_date, datetime.min.time(), tzinfo=timezone.utc
    ).timestamp() * 1000)
    end_ts = int(datetime.combine(
        req.end_date + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc
    ).timestamp() * 1000)

    async with create_http_client() as client:
        resp = await client.get(url, params={
            "instrument_name": instrument,
            "start_timestamp": start_ts,
            "end_timestamp": end_ts,
            "resolution": "1D",
        })
        data = resp.json().get("result", {})
        ticks = data.get("ticks", [])
        closes = data.get("close", [])

    price_map = {}
    for ts, close in zip(ticks, closes):
        dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).date()
        price_map[dt] = close

    if not price_map:
        raise HTTPException(status_code=400, detail="无法获取价格数据")

    # For each date, check cache first, then fetch if needed
    results_summary = []
    total_fetched = 0
    total_cached = 0
    total_saved = 0

    async with create_http_client() as client:
        for d in sorted(price_map.keys()):
            if d < req.start_date or d > req.end_date:
                continue

            # Check cache
            cached = get_cached_iv_smile(req.underlying, req.expiry_date, req.option_type, d)
            existing = [(s, iv) for s, iv, _ in cached if s > 0]
            if existing:
                results_summary.append({
                    "date": d.isoformat(),
                    "spot": round(price_map[d], 2),
                    "source": "cache",
                    "points": len(existing),
                })
                total_cached += 1
                continue

            # Fetch from API
            spot = price_map[d]
            step = get_strike_step(req.underlying, spot)
            atm_strike = find_nearest_strike(spot, step)

            candidates = []
            for i in range(-req.num_strikes, req.num_strikes + 1):
                s = atm_strike + i * step
                if s > 0:
                    candidates.append(s)
            candidates.sort(key=lambda s: abs(s - spot))
            candidates = candidates[:req.num_strikes * 2]

            T = calculate_time_to_expiration(req.expiry_date, d)
            if T <= 0.0001:
                continue

            smile_points = []
            db_points = []

            for strike in candidates:
                inst = build_instrument_name(req.underlying, req.expiry_date, strike, req.option_type)
                trade = await fetch_trades_for_instrument(client, inst, d, window_days=req.window_days)

                if trade is None:
                    continue

                trade_price_btc = trade.get("price", 0)
                trade_price_usd = trade_price_btc * spot

                if trade_price_usd <= 0:
                    continue

                iv = implied_volatility(
                    market_price=trade_price_usd,
                    S=spot, K=strike, T=T, r=RISK_FREE_RATE,
                    option_type=req.option_type,
                )

                if iv is not None and 0.01 < iv < 10.0:
                    smile_points.append({"strike": strike, "iv": round(iv, 4)})
                    db_points.append((strike, iv, trade_price_usd, inst))

            if db_points and req.save_to_cache:
                save_cached_iv_smile(req.underlying, req.expiry_date, req.option_type, d, spot, db_points)
                total_saved += len(db_points)

            results_summary.append({
                "date": d.isoformat(),
                "spot": round(spot, 2),
                "source": "api",
                "points": len(smile_points),
                "smile": smile_points,
            })
            total_fetched += 1

            # Rate limit between dates
            await asyncio.sleep(0.5)

    return {
        "underlying": req.underlying,
        "expiry_date": req.expiry_date.isoformat(),
        "option_type": req.option_type,
        "date_range": f"{req.start_date} ~ {req.end_date}",
        "total_dates": len(results_summary),
        "from_cache": total_cached,
        "from_api": total_fetched,
        "points_saved": total_saved,
        "results": results_summary,
    }


# ── 7) ATM IV History ───────────────────────────────────────────────────────

class ATMIVHistoryRequest(BaseModel):
    underlying: str = Field(default="BTC")
    start_date: date
    end_date: date
    option_type: str = Field(default="PUT", pattern="^(PUT|CALL)$")


@router.post("/atm-iv-history")
async def atm_iv_history(req: ATMIVHistoryRequest):
    """Fetch daily ATM IV from cached IV smile data.
    For each date, finds the IV smile in cache, interpolates at ATM strike.
    If no cache exists for a date, fetches from API (with rate limiting)."""
    from app.api.deribit import (
        fetch_deribit_index_prices, fetch_iv_smile, interpolate_iv_at_strike,
    )

    # 1) Get daily spot prices
    price_map = await fetch_deribit_index_prices(req.underlying, req.start_date, req.end_date)
    if not price_map:
        raise HTTPException(status_code=400, detail="无法获取价格数据")

    dates = sorted(d for d in price_map.keys() if req.start_date <= d <= req.end_date)
    results = []

    async with create_http_client() as client:
        for d in dates:
            spot = price_map[d]
            step = get_strike_step(req.underlying, spot)
            atm_strike = find_nearest_strike(spot, step)

            # Find the relevant expiry (current month's last Friday)
            expiry = find_deribit_expiry(d, 1)
            # If expiry is too close (< 2 days), use next month
            T = calculate_time_to_expiration(expiry, d)
            if T < 0.005:
                expiry = find_deribit_expiry(d, 2)
                T = calculate_time_to_expiration(expiry, d)

            # Try cache first
            cached = get_cached_iv_smile(req.underlying, expiry, req.option_type, d)
            smile = [(s, iv) for s, iv, _ in cached if s > 0]

            if not smile:
                # Fetch from API (rate limited)
                smile = await fetch_iv_smile(
                    client, req.underlying, expiry, spot, req.option_type, d, num_strikes=5
                )

            atm_iv = None
            if smile:
                atm_iv = interpolate_iv_at_strike(smile, atm_strike)

            results.append({
                "date": d.isoformat(),
                "spot": round(spot, 2),
                "atm_strike": atm_strike,
                "expiry": expiry.isoformat(),
                "atm_iv": round(atm_iv, 6) if atm_iv else None,
                "atm_iv_pct": round(atm_iv * 100, 2) if atm_iv else None,
                "smile_points": len(smile),
            })

    valid_count = sum(1 for r in results if r["atm_iv"] is not None)
    return {
        "underlying": req.underlying,
        "option_type": req.option_type,
        "total_dates": len(results),
        "valid_dates": valid_count,
        "data": results,
    }
