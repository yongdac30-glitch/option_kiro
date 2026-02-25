"""QQQ LEAPS 逢跌买入策略回测 API

策略逻辑：
- 开仓条件：标的单日跌幅 ≥ dip_threshold（默认1%）
- 开仓合约：买入到期日约 expiry_months（默认24）个月后、Delta 最接近 target_delta（默认0.60）的 CALL
- 止盈/平仓规则（阶梯式）：
  - 0~4个月：期权价格涨至开仓价 × (1 + tp_pct_1)（默认+50%）止盈
  - 4~6个月：止盈目标降为开仓价 × (1 + tp_pct_2)（默认+30%）
  - 6~9个月：止盈目标降为开仓价 × (1 + tp_pct_3)（默认+10%）
  - 超过 max_hold_months（默认9）个月：强制平仓
- 支持同时持有多笔仓位（每次跌幅触发独立开仓）

数据来源: Yahoo Finance (美股ETF) / OKX (BTC, ETH)
期权定价: Black-Scholes 模型
"""
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from typing import List, Optional, Dict
from datetime import datetime, date, timedelta, timezone
import asyncio
import json
import math

from app.services.pricing import black_scholes_price, calculate_time_to_expiration
from app.core.config import create_http_client

router = APIRouter(prefix="/api/qqq-leaps", tags=["qqq-leaps"])

RISK_FREE_RATE = 0.045

# Crypto tickers use OKX data, contract multiplier = 1 (1 BTC/ETH per contract)
CRYPTO_TICKERS = {"BTC", "ETH"}

YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
YAHOO_HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}


def _is_crypto(ticker: str) -> bool:
    return ticker.upper() in CRYPTO_TICKERS


def _contract_mult(ticker: str) -> int:
    """Crypto options: 1 unit per contract. US equity options: 100 shares."""
    return 1 if _is_crypto(ticker) else 100


async def _fetch_yahoo_prices(ticker: str, start: date, end: date) -> Dict[date, float]:
    """Fetch daily close prices from Yahoo Finance REST API."""
    start_ts = int(datetime.combine(start, datetime.min.time(),
                                     tzinfo=timezone.utc).timestamp())
    end_ts = int(datetime.combine(end + timedelta(days=1), datetime.min.time(),
                                   tzinfo=timezone.utc).timestamp())
    url = YAHOO_CHART_URL.format(symbol=ticker)
    params = {
        "period1": str(start_ts), "period2": str(end_ts),
        "interval": "1d", "includePrePost": "false",
    }
    last_error = None
    for attempt in range(3):
        try:
            async with create_http_client(timeout=30.0, connect_timeout=10.0) as client:
                resp = await client.get(url, params=params, headers=YAHOO_HEADERS)
                if resp.status_code == 429:
                    await asyncio.sleep(2.0 * (attempt + 1))
                    continue
                resp.raise_for_status()
                data = resp.json()
        except Exception as e:
            last_error = e
            await asyncio.sleep(2.0 * (attempt + 1))
            continue
        chart = data.get("chart", {})
        results = chart.get("result", [])
        if not results:
            raise ValueError(f"Yahoo Finance 无数据: {ticker}")
        result = results[0]
        timestamps = result.get("timestamp", [])
        indicators = result.get("indicators", {})
        quotes = indicators.get("quote", [{}])[0]
        closes = quotes.get("close", [])
        adj_close_list = indicators.get("adjclose", [{}])
        adj_closes = adj_close_list[0].get("adjclose", []) if adj_close_list else []
        prices = {}
        for i, ts in enumerate(timestamps):
            if ts is None:
                continue
            dt = datetime.fromtimestamp(ts, tz=timezone.utc).date()
            price = None
            if adj_closes and i < len(adj_closes) and adj_closes[i] is not None:
                price = float(adj_closes[i])
            elif i < len(closes) and closes[i] is not None:
                price = float(closes[i])
            if price and price > 0:
                prices[dt] = round(price, 4)
        if not prices:
            raise ValueError(f"Yahoo Finance 返回空数据: {ticker}")
        return prices
    raise ValueError(f"获取 {ticker} 数据失败(重试3次): {last_error}")


async def _fetch_okx_prices(ticker: str, start: date, end: date) -> Dict[date, float]:
    """Fetch daily close prices for BTC/ETH from OKX (DB cache first, then API)."""
    from app.api.data_center import _fetch_okx_prices_with_cache
    inst_id = f"{ticker.upper()}-USD"
    return await _fetch_okx_prices_with_cache(inst_id, start, end)


async def _fetch_prices(ticker: str, start: date, end: date) -> Dict[date, float]:
    """Unified price fetcher: OKX for crypto, Yahoo for US equities."""
    if _is_crypto(ticker):
        return await _fetch_okx_prices(ticker, start, end)
    return await _fetch_yahoo_prices(ticker, start, end)


# ── Expiry & strike helpers ──────────────────────────────────────────

def _generate_expiries_us(today: date) -> List[date]:
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


def _generate_expiries_crypto(today: date) -> List[date]:
    """Generate plausible crypto option expiry dates (last Friday of month)."""
    import calendar
    results = []
    for year_offset in range(0, 4):
        year = today.year + year_offset
        for month in range(1, 13):
            cal = calendar.monthcalendar(year, month)
            fridays = [w[calendar.FRIDAY] for w in cal if w[calendar.FRIDAY] != 0]
            if fridays:
                exp = date(year, month, fridays[-1])
                if exp > today:
                    results.append(exp)
    results.sort()
    return results


def _find_expiry(today: date, min_days: int = 730, is_crypto: bool = False) -> Optional[date]:
    """Find nearest expiry at least min_days away."""
    target = today + timedelta(days=min_days)
    expiries = _generate_expiries_crypto(today) if is_crypto else _generate_expiries_us(today)
    for exp in expiries:
        if exp >= target:
            return exp
    return None


def _strike_step(price: float, is_crypto: bool = False) -> float:
    if is_crypto:
        if price < 500: return 25.0
        if price < 2000: return 50.0
        if price < 10000: return 500.0
        if price < 50000: return 1000.0
        return 5000.0
    # US equities
    if price < 25: return 2.5
    if price < 200: return 5.0
    if price < 500: return 10.0
    return 25.0


def _nearest_strike(price: float, step: float) -> float:
    return round(round(price / step) * step, 2)


# ── Schemas ──────────────────────────────────────────────────────────

class QQQLeapsRequest(BaseModel):
    ticker: str = Field(default="QQQ", description="标的: QQQ, SPY, XLK 等")
    start_date: date
    end_date: date
    initial_capital: float = Field(default=50000)
    target_delta: float = Field(default=0.60, description="目标Delta")
    default_iv: float = Field(default=0.25, description="默认IV")
    dip_threshold: float = Field(default=1.0, description="单日跌幅触发阈值(%)")
    expiry_months: int = Field(default=24, description="目标到期月数")
    num_contracts: int = Field(default=1, description="每次开仓合约数")
    tp_pct_1: float = Field(default=50.0, description="0-4个月止盈%")
    tp_pct_2: float = Field(default=30.0, description="4-6个月止盈%")
    tp_pct_3: float = Field(default=10.0, description="6-9个月止盈%")
    max_hold_months: int = Field(default=9, description="最大持仓月数(超过强制平仓)")
    max_positions: int = Field(default=5, description="最大同时持仓数量")
    compare_tickers: List[str] = Field(default=["QQQ", "SPY", "XLK"],
                                        description="对比标的列表")


# ── Dynamic IV: rolling historical volatility ────────────────────────

def _build_dynamic_iv_map(price_map: Dict[date, float], window: int = 30,
                          trading_days_per_year: int = 252) -> Dict[date, float]:
    """Compute rolling historical volatility (annualized) from daily close prices.
    
    Args:
        trading_days_per_year: 252 for US equities, 365 for crypto (24/7 markets).
    """
    dates_sorted = sorted(price_map.keys())
    prices = [price_map[d] for d in dates_sorted]

    log_returns = []
    for i in range(1, len(prices)):
        if prices[i] > 0 and prices[i - 1] > 0:
            log_returns.append(math.log(prices[i] / prices[i - 1]))
        else:
            log_returns.append(0.0)

    iv_map: Dict[date, float] = {}
    for i in range(len(dates_sorted)):
        if i < 1:
            iv_map[dates_sorted[i]] = 0.25  # fallback
            continue
        start_ret_idx = max(0, i - window)
        window_returns = log_returns[start_ret_idx:i]
        if len(window_returns) < 5:
            iv_map[dates_sorted[i]] = 0.25
            continue
        mean_r = sum(window_returns) / len(window_returns)
        var_r = sum((r - mean_r) ** 2 for r in window_returns) / (len(window_returns) - 1)
        daily_vol = math.sqrt(var_r) if var_r > 0 else 0.0
        annualized_vol = daily_vol * math.sqrt(trading_days_per_year)
        annualized_vol = max(0.05, min(annualized_vol, 3.0))
        iv_map[dates_sorted[i]] = round(annualized_vol, 4)

    return iv_map


# ── Core backtest engine ─────────────────────────────────────────────

async def run_qqq_leaps_backtest(
    price_map: Dict[date, float],
    config: QQQLeapsRequest,
    progress_callback=None,
) -> dict:
    """Run QQQ LEAPS dip-buying strategy backtest."""
    dates = sorted(price_map.keys())
    if not dates:
        raise HTTPException(status_code=400, detail="No price data")

    iv = config.default_iv
    is_crypto_ticker = _is_crypto(config.ticker)
    mult = _contract_mult(config.ticker)
    qty = config.num_contracts

    # Build rolling historical IV map (crypto: 365 trading days, equities: 252)
    iv_annualize_days = 365 if is_crypto_ticker else 252
    dynamic_iv_map = _build_dynamic_iv_map(price_map, 30, iv_annualize_days)

    def _get_iv(today: date) -> float:
        """Get rolling 30-day historical volatility for a given date."""
        if today in dynamic_iv_map:
            return dynamic_iv_map[today]
        return iv  # fallback to default

    cash = config.initial_capital
    realized_pnl = 0.0  # 累计已实现盈亏
    positions = []  # list of open positions
    trades = []
    equity_curve = []
    total_days = len(dates)

    def _bs(spot, strike, expiry, today):
        T = calculate_time_to_expiration(expiry, today)
        if T <= 0.0001:
            return max(0.0, spot - strike)
        cur_iv = _get_iv(today)
        return black_scholes_price(spot, strike, T, RISK_FREE_RATE, cur_iv, "CALL")

    def _bs_delta(spot, strike, expiry, today):
        T = calculate_time_to_expiration(expiry, today)
        if T <= 0.0001:
            return 1.0 if spot > strike else 0.0
        cur_iv = _get_iv(today)
        try:
            from scipy.stats import norm
            d1 = (math.log(spot / strike) + (RISK_FREE_RATE + 0.5 * cur_iv**2) * T) / (cur_iv * math.sqrt(T))
            return norm.cdf(d1)
        except Exception:
            return 0.5

    def _find_strike_for_delta(spot, expiry, today, target_delta):
        """Find strike giving approximately target_delta."""
        step = _strike_step(spot, is_crypto_ticker)
        atm = _nearest_strike(spot, step)
        best_strike = atm
        best_diff = 999.0
        # Scan from OTM to deep ITM — wider range for crypto
        scan_range = 60 if is_crypto_ticker else 40
        for i in range(-15, scan_range):
            strike = atm - i * step
            if strike <= 0:
                continue
            delta = _bs_delta(spot, strike, expiry, today)
            diff = abs(delta - target_delta)
            if diff < best_diff:
                best_diff = diff
                best_strike = strike
        return best_strike

    def _get_tp_target(pos, today):
        """Get current take-profit target price based on holding duration."""
        months_held = (today - pos["open_date"]).days / 30.0
        if months_held < 4:
            return pos["open_price"] * (1 + config.tp_pct_1 / 100.0)
        elif months_held < 6:
            return pos["open_price"] * (1 + config.tp_pct_2 / 100.0)
        else:
            return pos["open_price"] * (1 + config.tp_pct_3 / 100.0)

    def _get_tp_label(pos, today):
        months_held = (today - pos["open_date"]).days / 30.0
        if months_held < 4:
            return f"+{config.tp_pct_1:.0f}%"
        elif months_held < 6:
            return f"+{config.tp_pct_2:.0f}%"
        else:
            return f"+{config.tp_pct_3:.0f}%"

    prev_spot = None

    for day_idx, today in enumerate(dates):
        spot = price_map[today]

        if progress_callback and day_idx % 20 == 0:
            await progress_callback(day_idx, total_days, today)

        # 1) Check all positions for take-profit or forced close
        closed_ids = []
        for i, pos in enumerate(positions):
            cur_price = _bs(spot, pos["strike"], pos["expiry"], today)
            months_held = (today - pos["open_date"]).days / 30.0

            # Force close if held > max_hold_months
            if months_held >= config.max_hold_months:
                proceeds = cur_price * pos["quantity"] * mult
                pnl = (cur_price - pos["open_price"]) * pos["quantity"] * mult
                cash += proceeds
                realized_pnl += pnl
                trades.append({
                    "date": today.isoformat(), "action": "FORCE_CLOSE",
                    "strike": round(pos["strike"], 2),
                    "expiry": pos["expiry"].isoformat(),
                    "spot": round(spot, 2),
                    "option_price": round(cur_price, 2),
                    "quantity": pos["quantity"],
                    "cash_flow": round(proceeds, 2),
                    "pnl": round(pnl, 2),
                    "pnl_pct": round((cur_price / pos["open_price"] - 1) * 100, 2) if pos["open_price"] > 0 else 0,
                    "delta": round(_bs_delta(spot, pos["strike"], pos["expiry"], today), 3),
                    "months_held": round(months_held, 1),
                    "note": f"持仓{months_held:.1f}个月, 强制平仓, PnL=${pnl:.2f}",
                })
                closed_ids.append(i)
                continue

            # Check take-profit
            tp_target = _get_tp_target(pos, today)
            if cur_price >= tp_target:
                proceeds = cur_price * pos["quantity"] * mult
                pnl = (cur_price - pos["open_price"]) * pos["quantity"] * mult
                cash += proceeds
                realized_pnl += pnl
                tp_label = _get_tp_label(pos, today)
                trades.append({
                    "date": today.isoformat(), "action": "TAKE_PROFIT",
                    "strike": round(pos["strike"], 2),
                    "expiry": pos["expiry"].isoformat(),
                    "spot": round(spot, 2),
                    "option_price": round(cur_price, 2),
                    "quantity": pos["quantity"],
                    "cash_flow": round(proceeds, 2),
                    "pnl": round(pnl, 2),
                    "pnl_pct": round((cur_price / pos["open_price"] - 1) * 100, 2) if pos["open_price"] > 0 else 0,
                    "delta": round(_bs_delta(spot, pos["strike"], pos["expiry"], today), 3),
                    "months_held": round(months_held, 1),
                    "note": f"止盈({tp_label}), 持仓{months_held:.1f}月, PnL=${pnl:.2f}",
                })
                closed_ids.append(i)

        # Remove closed positions (reverse order to keep indices valid)
        for i in sorted(closed_ids, reverse=True):
            positions.pop(i)

        # 2) Check dip condition for new position
        if prev_spot is not None and prev_spot > 0:
            daily_change_pct = (spot - prev_spot) / prev_spot * 100
            if daily_change_pct <= -config.dip_threshold and cash > 0 and len(positions) < config.max_positions:
                expiry = _find_expiry(today, config.expiry_months * 30, is_crypto_ticker)
                if expiry:
                    strike = _find_strike_for_delta(spot, expiry, today, config.target_delta)
                    price = _bs(spot, strike, expiry, today)
                    cost = price * qty * mult
                    delta = _bs_delta(spot, strike, expiry, today)

                    if cost > 0 and cost <= cash:
                        cash -= cost
                        pos = {
                            "strike": strike, "expiry": expiry,
                            "quantity": qty, "open_price": price,
                            "open_date": today, "open_spot": spot,
                        }
                        positions.append(pos)
                        trades.append({
                            "date": today.isoformat(), "action": "OPEN",
                            "strike": round(strike, 2),
                            "expiry": expiry.isoformat(),
                            "spot": round(spot, 2),
                            "option_price": round(price, 2),
                            "quantity": qty,
                            "cash_flow": round(-cost, 2),
                            "pnl": 0,
                            "pnl_pct": 0,
                            "delta": round(delta, 3),
                            "months_held": 0,
                            "note": f"跌{daily_change_pct:.2f}%触发, K={strike}, Δ={delta:.3f}, "
                                    f"到期{expiry}, 止盈目标+{config.tp_pct_1:.0f}%",
                        })
                    elif cost > cash:
                        trades.append({
                            "date": today.isoformat(), "action": "SKIP",
                            "strike": round(strike, 2),
                            "expiry": expiry.isoformat(),
                            "spot": round(spot, 2),
                            "option_price": round(price, 2),
                            "quantity": qty,
                            "cash_flow": 0, "pnl": 0, "pnl_pct": 0,
                            "delta": round(delta, 3),
                            "months_held": 0,
                            "note": f"跌{daily_change_pct:.2f}%触发, 资金不足(需${cost:.0f}, 有${cash:.0f})",
                        })

        # Compute daily change % before updating prev_spot
        day_chg = round((spot - prev_spot) / prev_spot * 100, 2) if prev_spot and prev_spot > 0 and day_idx > 0 else 0
        prev_spot = spot

        # 3) Mark-to-market all positions — 用仓位和期权价格计算权益
        holdings = 0.0
        unrealized_pnl = 0.0
        for pos in positions:
            mtm = _bs(spot, pos["strike"], pos["expiry"], today)
            pos_value = mtm * pos["quantity"] * mult
            holdings += pos_value
            unrealized_pnl += (mtm - pos["open_price"]) * pos["quantity"] * mult

        total_equity = config.initial_capital + realized_pnl + unrealized_pnl
        capital_usage_pct = round(holdings / total_equity * 100, 1) if total_equity > 0 else 0.0

        # Attach capital_usage_pct to all trades that happened today
        for t in trades:
            if t["date"] == today.isoformat() and "capital_usage_pct" not in t:
                t["capital_usage_pct"] = capital_usage_pct

        # 4) Generate daily MTM records for each open position (skip open day)
        for pos in positions:
            if pos["open_date"] == today:
                continue  # 开仓当天已有OPEN记录，不重复生成MTM
            mtm_price = _bs(spot, pos["strike"], pos["expiry"], today)
            pos_pnl = (mtm_price - pos["open_price"]) * pos["quantity"] * mult
            pos_pnl_pct = (mtm_price / pos["open_price"] - 1) * 100 if pos["open_price"] > 0 else 0
            months_held = (today - pos["open_date"]).days / 30.0
            tp_target = _get_tp_target(pos, today)
            tp_label = _get_tp_label(pos, today)
            trades.append({
                "date": today.isoformat(), "action": "MTM",
                "strike": round(pos["strike"], 2),
                "expiry": pos["expiry"].isoformat(),
                "spot": round(spot, 2),
                "option_price": round(mtm_price, 2),
                "quantity": pos["quantity"],
                "cash_flow": 0,
                "pnl": round(pos_pnl, 2),
                "pnl_pct": round(pos_pnl_pct, 2),
                "delta": round(_bs_delta(spot, pos["strike"], pos["expiry"], today), 3),
                "months_held": round(months_held, 1),
                "capital_usage_pct": capital_usage_pct,
                "note": f"开仓价{pos['open_price']:.2f}, 止盈目标{tp_label}={tp_target:.2f}",
            })

        equity_curve.append({
            "date": today.isoformat(),
            "equity": round(total_equity, 2),
            "spot": round(spot, 2),
            "cash": round(cash, 2),
            "holdings": round(holdings, 2),
            "unrealized_pnl": round(unrealized_pnl, 2),
            "realized_pnl": round(realized_pnl, 2),
            "num_positions": len(positions),
            "capital_usage_pct": capital_usage_pct,
            "iv": round(_get_iv(today), 4),
            "daily_change_pct": day_chg,
        })

    # Close remaining positions
    if positions and dates:
        last = dates[-1]
        last_spot = price_map[last]
        for pos in positions:
            cp = _bs(last_spot, pos["strike"], pos["expiry"], last)
            proceeds = cp * pos["quantity"] * mult
            pnl = (cp - pos["open_price"]) * pos["quantity"] * mult
            months_held = (last - pos["open_date"]).days / 30.0
            cash += proceeds
            realized_pnl += pnl
            trades.append({
                "date": last.isoformat(), "action": "CLOSE",
                "strike": round(pos["strike"], 2),
                "expiry": pos["expiry"].isoformat(),
                "spot": round(last_spot, 2),
                "option_price": round(cp, 2),
                "quantity": pos["quantity"],
                "cash_flow": round(proceeds, 2),
                "pnl": round(pnl, 2),
                "pnl_pct": round((cp / pos["open_price"] - 1) * 100, 2) if pos["open_price"] > 0 else 0,
                "delta": round(_bs_delta(last_spot, pos["strike"], pos["expiry"], last), 3),
                "months_held": round(months_held, 1),
                "capital_usage_pct": 0,
                "note": f"回测结束平仓, 持仓{months_held:.1f}月, PnL=${pnl:.2f}",
            })
        positions = []
        if equity_curve:
            final_eq = config.initial_capital + realized_pnl
            equity_curve[-1]["equity"] = round(final_eq, 2)
            equity_curve[-1]["holdings"] = 0.0
            equity_curve[-1]["unrealized_pnl"] = 0.0
            equity_curve[-1]["realized_pnl"] = round(realized_pnl, 2)
            equity_curve[-1]["num_positions"] = 0

    # Summary
    final_equity = config.initial_capital + realized_pnl
    total_pnl = final_equity - config.initial_capital
    days_total = (dates[-1] - dates[0]).days if len(dates) > 1 else 1
    years = max(days_total / 365.25, 0.01)

    max_dd = 0.0
    peak = config.initial_capital
    for e in equity_curve:
        if e["equity"] > peak:
            peak = e["equity"]
        dd = (peak - e["equity"]) / peak if peak > 0 else 0
        max_dd = max(max_dd, dd)

    ann_ret = ((final_equity / config.initial_capital) ** (1 / years) - 1) * 100 if final_equity > 0 else -100.0
    return_pct = total_pnl / config.initial_capital * 100

    # Sharpe (crypto: 365 trading days, equities: 252)
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
                strategy_sharpe = (mean_r / std_r) * math.sqrt(iv_annualize_days)

    # B&H comparison
    bh_start = equity_curve[0]["spot"] if equity_curve else 1
    bh_end = equity_curve[-1]["spot"] if equity_curve else 1
    bh_return = (bh_end / bh_start - 1) * 100 if bh_start > 0 else 0
    bh_ann = ((bh_end / bh_start) ** (1 / years) - 1) * 100 if bh_start > 0 else 0
    bh_max_dd = 0.0
    bh_peak = bh_start
    for e in equity_curve:
        if e["spot"] > bh_peak:
            bh_peak = e["spot"]
        dd = (bh_peak - e["spot"]) / bh_peak if bh_peak > 0 else 0
        bh_max_dd = max(bh_max_dd, dd)

    open_count = sum(1 for t in trades if t["action"] == "OPEN")
    tp_count = sum(1 for t in trades if t["action"] == "TAKE_PROFIT")
    fc_count = sum(1 for t in trades if t["action"] == "FORCE_CLOSE")
    skip_count = sum(1 for t in trades if t["action"] == "SKIP")

    # Win rate (take profit vs force close)
    closed_trades = [t for t in trades if t["action"] in ("TAKE_PROFIT", "FORCE_CLOSE", "CLOSE")]
    win_trades = [t for t in closed_trades if t.get("pnl", 0) > 0]
    win_rate = len(win_trades) / len(closed_trades) * 100 if closed_trades else 0

    summary = {
        "ticker": config.ticker,
        "initial_capital": config.initial_capital,
        "final_equity": round(final_equity, 2),
        "total_pnl": round(total_pnl, 2),
        "return_pct": round(return_pct, 2),
        "annualized_return_pct": round(ann_ret, 2),
        "max_drawdown_pct": round(max_dd * 100, 2),
        "sharpe_ratio": round(strategy_sharpe, 3),
        "open_count": open_count,
        "tp_count": tp_count,
        "force_close_count": fc_count,
        "skip_count": skip_count,
        "win_rate": round(win_rate, 1),
        "total_trades": sum(1 for t in trades if t["action"] != "MTM"),
        "backtest_days": days_total,
        "default_iv": iv,
        "iv_mode": "动态(30日滚动)",
        "target_delta": config.target_delta,
        "dip_threshold": config.dip_threshold,
        "bh_return_pct": round(bh_return, 2),
        "bh_annualized_pct": round(bh_ann, 2),
        "bh_max_drawdown_pct": round(bh_max_dd * 100, 2),
    }

    return {"equity_curve": equity_curve, "trades": trades, "summary": summary}


# ── API Endpoints ────────────────────────────────────────────────────

@router.post("/backtest-stream")
async def qqq_leaps_stream(req: QQQLeapsRequest):
    """Run QQQ LEAPS dip-buying backtest with SSE progress streaming."""
    if req.start_date >= req.end_date:
        raise HTTPException(status_code=400, detail="开始日期必须早于结束日期")

    async def event_generator():
        yield f"data: {json.dumps({'type': 'progress', 'pct': 0, 'status': f'正在获取 {req.ticker} 历史价格...'})}\n\n"

        try:
            price_map = await _fetch_prices(req.ticker, req.start_date, req.end_date)
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': f'获取{req.ticker}价格失败: {str(e)}'})}\n\n"
            return

        if not price_map:
            yield f"data: {json.dumps({'type': 'error', 'message': '无价格数据'})}\n\n"
            return

        total = len(price_map)
        yield f"data: {json.dumps({'type': 'progress', 'pct': 5, 'status': f'获取到 {total} 天价格数据, 开始回测...'})}\n\n"

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
                cp = await _fetch_prices(ct, req.start_date, req.end_date)
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
            prices = await _fetch_prices(t, start, end)
            if prices:
                sorted_d = sorted(prices.keys())
                first = prices[sorted_d[0]]
                last = prices[sorted_d[-1]]
                total_ret = (last / first - 1) * 100
                ann_ret = ((last / first) ** (1 / years) - 1) * 100
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
