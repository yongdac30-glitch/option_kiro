"""OKX market data proxy API endpoints."""
from fastapi import APIRouter, HTTPException, Query
from typing import Optional
import httpx
from datetime import datetime, timezone, date as date_type
from app.services.pricing import implied_volatility, calculate_time_to_expiration
from app.core.config import settings, create_http_client

router = APIRouter(prefix="/api/okx", tags=["okx"])

OKX_BASE = "https://www.okx.com"
TIMEOUT = 15.0


async def _okx_get(path: str, params: dict) -> dict:
    """Make a GET request to OKX public API."""
    async with create_http_client(timeout=TIMEOUT, connect_timeout=TIMEOUT) as client:
        resp = await client.get(
            f"{OKX_BASE}{path}",
            params=params,
            headers={"User-Agent": "OptionsRiskMonitor/1.0"},
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != "0":
            raise HTTPException(status_code=502, detail=f"OKX API error: {data.get('msg', 'unknown')}")
        return data


@router.get("/spot-price")
async def get_spot_price(instId: str = Query(default="BTC-USDC")):
    """Get spot ticker price from OKX (for BTC→USD conversion)."""
    data = await _okx_get("/api/v5/market/ticker", {"instId": instId})
    if not data["data"]:
        raise HTTPException(status_code=404, detail="Ticker not found")
    ticker = data["data"][0]
    return {
        "instId": ticker["instId"],
        "last": float(ticker["last"]),
        "askPx": float(ticker["askPx"]) if ticker.get("askPx") else None,
        "bidPx": float(ticker["bidPx"]) if ticker.get("bidPx") else None,
    }


@router.get("/option-chain")
async def get_option_chain(
    uly: str = Query(default="BTC-USD", description="Underlying, e.g. BTC-USD"),
    expiry: Optional[str] = Query(default=None, description="Filter by expiry date YYMMDD, e.g. 250228"),
):
    """Get option chain: instruments + tickers merged, with BTC→USD conversion."""
    # 1) Fetch instruments
    inst_params = {"instType": "OPTION", "uly": uly}
    inst_data = await _okx_get("/api/v5/public/instruments", inst_params)
    instruments = inst_data["data"]

    # 2) Fetch tickers
    ticker_params = {"instType": "OPTION", "uly": uly}
    ticker_data = await _okx_get("/api/v5/market/tickers", ticker_params)
    ticker_map = {t["instId"]: t for t in ticker_data["data"]}

    # 3) Fetch spot price for conversion (BTC-USDC or ETH-USDC based on uly)
    base_ccy = uly.split("-")[0]  # BTC or ETH
    spot_inst = f"{base_ccy}-USDC"
    spot_data = await _okx_get("/api/v5/market/ticker", {"instId": spot_inst})
    btc_usd = float(spot_data["data"][0]["last"])

    # 4) Build result
    results = []
    for inst in instruments:
        inst_id = inst["instId"]
        ticker = ticker_map.get(inst_id, {})

        # Parse expiry from instId: e.g. BTC-USD-250228-80000-P
        parts = inst_id.split("-")
        # parts: [BTC, USD, 250228, 80000, P]
        exp_str = parts[2] if len(parts) >= 5 else ""

        # Filter by expiry if specified
        if expiry and exp_str != expiry:
            continue

        # Parse expiry timestamp to date string
        exp_ts = int(inst.get("expTime", "0"))
        exp_date = ""
        if exp_ts:
            exp_date = datetime.fromtimestamp(exp_ts / 1000, tz=timezone.utc).strftime("%Y-%m-%d")

        strike = float(inst.get("stk", "0"))
        opt_type = "CALL" if inst.get("optType") == "C" else "PUT"

        # Prices in BTC
        last_btc = float(ticker.get("last", "0") or "0")
        ask_btc = float(ticker.get("askPx", "0") or "0")
        bid_btc = float(ticker.get("bidPx", "0") or "0")

        # Convert to USD
        last_usd = last_btc * btc_usd
        ask_usd = ask_btc * btc_usd
        bid_usd = bid_btc * btc_usd

        # Contract multiplier
        ct_mult = float(inst.get("ctMult", "1") or "1")

        # Calculate IV from mid price (bid+ask)/2 — skip if no bid/ask quote
        iv_value = None
        mid_usd = 0.0
        if bid_usd > 0 and ask_usd > 0:
            mid_usd = (bid_usd + ask_usd) / 2

        if mid_usd > 0 and exp_date and strike > 0:
            try:
                exp_d = date_type.fromisoformat(exp_date)
                today = date_type.today()
                T = calculate_time_to_expiration(exp_d, today)
                if T > 0.0001:
                    iv_calc = implied_volatility(
                        market_price=mid_usd,
                        S=btc_usd,
                        K=strike,
                        T=T,
                        r=settings.RISK_FREE_RATE,
                        option_type=opt_type,
                    )
                    if iv_calc is not None and 0 < iv_calc < 20:
                        iv_value = round(iv_calc * 100, 2)
            except Exception:
                pass

        results.append({
            "instId": inst_id,
            "optType": opt_type,
            "strike": strike,
            "expiryDate": exp_date,
            "expiryCode": exp_str,
            "ctMult": ct_mult,
            "lastBtc": last_btc,
            "askBtc": ask_btc,
            "bidBtc": bid_btc,
            "lastUsd": round(last_usd, 4),
            "askUsd": round(ask_usd, 4),
            "bidUsd": round(bid_usd, 4),
            "midUsd": round(mid_usd, 4),
            "iv": iv_value,
            "vol24h": ticker.get("vol24h", "0"),
        })

    # Sort by expiry, then strike, then type
    results.sort(key=lambda x: (x["expiryDate"], x["strike"], x["optType"]))

    return {
        "btcUsdPrice": btc_usd,
        "uly": uly,
        "count": len(results),
        "data": results,
    }


@router.get("/expiry-dates")
async def get_expiry_dates(
    uly: str = Query(default="BTC-USD"),
):
    """Get available expiry dates for an underlying."""
    inst_data = await _okx_get("/api/v5/public/instruments", {"instType": "OPTION", "uly": uly})

    expiries = {}
    for inst in inst_data["data"]:
        parts = inst["instId"].split("-")
        if len(parts) >= 5:
            exp_code = parts[2]
            exp_ts = int(inst.get("expTime", "0"))
            if exp_ts and exp_code not in expiries:
                exp_date = datetime.fromtimestamp(exp_ts / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
                expiries[exp_code] = exp_date

    sorted_expiries = sorted(expiries.items(), key=lambda x: x[1])
    return {
        "uly": uly,
        "expiries": [{"code": code, "date": date} for code, date in sorted_expiries],
    }
