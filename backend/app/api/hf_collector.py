"""高频期权数据收集 API — 每分钟快照 Deribit 期权盘口数据。

使用 Deribit REST API:
- public/get_book_summary_by_currency?currency=BTC&kind=option
  返回所有活跃期权的 bid/ask/last/mark/volume/OI 等
- public/get_index_price?index_name=btc_usd
  返回当前指数价格
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime, date, timedelta, timezone
import asyncio
import json
import re

from app.core.database import SessionLocal
from app.core.config import create_http_client
from app.models.hf_option_tick import HFOptionTick
from sqlalchemy import func as sa_func, distinct

router = APIRouter(prefix="/api/hf-collector", tags=["hf-collector"])

DERIBIT_BASE = "https://www.deribit.com/api/v2"

# ── Background task state ────────────────────────────────────────────
_collector_task: Optional[asyncio.Task] = None
_collector_running = False
_collector_status = {
    "running": False,
    "underlying": "BTC",
    "interval_sec": 60,
    "last_snapshot": None,
    "last_count": 0,
    "total_snapshots": 0,
    "error": None,
}


def _parse_instrument_name(name: str):
    """Parse Deribit instrument name like BTC-28MAR26-100000-P.
    Returns (underlying, expiry_str, strike, option_type) or None."""
    parts = name.split("-")
    if len(parts) != 4:
        return None
    underlying = parts[0]
    expiry_str = parts[1]
    try:
        strike = float(parts[2])
    except ValueError:
        return None
    opt_char = parts[3].upper()
    option_type = "PUT" if opt_char == "P" else "CALL" if opt_char == "C" else None
    if not option_type:
        return None

    # Parse expiry: e.g. "28MAR26" -> date
    month_map = {
        "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
        "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
    }
    m = re.match(r"(\d{1,2})([A-Z]{3})(\d{2})", expiry_str)
    if not m:
        return None
    day = int(m.group(1))
    month = month_map.get(m.group(2))
    year = 2000 + int(m.group(3))
    if not month:
        return None
    try:
        expiry_date = date(year, month, day)
    except ValueError:
        return None

    return underlying, expiry_date.isoformat(), strike, option_type


async def _fetch_snapshot(underlying: str) -> dict:
    """Fetch one snapshot of all option book summaries + index price."""
    currency = underlying.upper()
    async with create_http_client(timeout=15.0) as client:
        # Get index price
        idx_resp = await client.get(
            f"{DERIBIT_BASE}/public/get_index_price",
            params={"index_name": f"{currency.lower()}_usd"},
        )
        idx_resp.raise_for_status()
        idx_data = idx_resp.json()
        spot = idx_data.get("result", {}).get("index_price", 0)

        # Get all option book summaries
        book_resp = await client.get(
            f"{DERIBIT_BASE}/public/get_book_summary_by_currency",
            params={"currency": currency, "kind": "option"},
        )
        book_resp.raise_for_status()
        book_data = book_resp.json()
        instruments = book_data.get("result", [])

    return {"spot": spot, "instruments": instruments}


async def _save_snapshot(underlying: str, spot: float, instruments: list, snap_time: datetime):
    """Save snapshot to DB."""
    db = SessionLocal()
    count = 0
    try:
        for inst in instruments:
            name = inst.get("instrument_name", "")
            parsed = _parse_instrument_name(name)
            if not parsed:
                continue
            _, expiry_str, strike, option_type = parsed

            bid = inst.get("bid_price")
            ask = inst.get("ask_price")
            last = inst.get("last")
            mark = inst.get("mark_price")
            vol = inst.get("volume")
            oi = inst.get("open_interest")

            # Convert None to actual None (Deribit may return 0 or null)
            if bid is not None and bid <= 0:
                bid = None
            if ask is not None and ask <= 0:
                ask = None

            tick = HFOptionTick(
                underlying=underlying,
                instrument_name=name,
                expiry_date=expiry_str,
                option_type=option_type,
                strike=strike,
                snapshot_time=snap_time,
                spot_price=spot,
                bid_price=bid,
                ask_price=ask,
                last_price=last if last and last > 0 else None,
                mark_price=mark if mark and mark > 0 else None,
                bid_usd=round(bid * spot, 2) if bid and spot else None,
                ask_usd=round(ask * spot, 2) if ask and spot else None,
                last_usd=round(last * spot, 2) if last and last > 0 and spot else None,
                mark_usd=round(mark * spot, 2) if mark and mark > 0 and spot else None,
                volume_24h=vol,
                open_interest=oi,
                iv_mark=inst.get("mark_iv"),
            )
            db.add(tick)
            count += 1

        db.commit()
    except Exception as e:
        db.rollback()
        raise
    finally:
        db.close()
    return count


async def _collector_loop(underlying: str, interval_sec: int):
    """Background loop: fetch + save every interval_sec seconds."""
    global _collector_running, _collector_status
    _collector_running = True
    _collector_status["running"] = True
    _collector_status["error"] = None

    while _collector_running:
        try:
            snap_time = datetime.utcnow().replace(second=0, microsecond=0)
            data = await _fetch_snapshot(underlying)
            count = await _save_snapshot(
                underlying, data["spot"], data["instruments"], snap_time
            )
            _collector_status["last_snapshot"] = snap_time.isoformat()
            _collector_status["last_count"] = count
            _collector_status["total_snapshots"] += 1
            _collector_status["error"] = None
            print(f"[HF] Snapshot {underlying}: {count} ticks at {snap_time.strftime('%H:%M')}")
        except asyncio.CancelledError:
            break
        except Exception as e:
            _collector_status["error"] = str(e)
            print(f"[HF] Error: {e}")

        # Sleep until next interval
        try:
            await asyncio.sleep(interval_sec)
        except asyncio.CancelledError:
            break

    _collector_running = False
    _collector_status["running"] = False


# ── API Endpoints ────────────────────────────────────────────────────

class CollectorConfig(BaseModel):
    underlying: str = Field(default="BTC")
    interval_sec: int = Field(default=60, ge=10, le=3600)


@router.get("/status")
async def get_status():
    """获取收集器状态。"""
    return _collector_status


@router.post("/start")
async def start_collector(config: CollectorConfig):
    """启动高频数据收集。"""
    global _collector_task, _collector_running, _collector_status
    if _collector_running:
        return {"message": "收集器已在运行", **_collector_status}

    _collector_status["underlying"] = config.underlying
    _collector_status["interval_sec"] = config.interval_sec
    _collector_status["total_snapshots"] = 0

    _collector_task = asyncio.create_task(
        _collector_loop(config.underlying, config.interval_sec)
    )
    return {"message": f"已启动 {config.underlying} 高频收集 (间隔 {config.interval_sec}s)"}


@router.post("/stop")
async def stop_collector():
    """停止高频数据收集。"""
    global _collector_task, _collector_running
    if not _collector_running:
        return {"message": "收集器未在运行"}

    _collector_running = False
    if _collector_task:
        _collector_task.cancel()
        try:
            await _collector_task
        except asyncio.CancelledError:
            pass
        _collector_task = None

    return {"message": "已停止收集"}


@router.post("/snapshot")
async def manual_snapshot(config: CollectorConfig):
    """手动触发一次快照。"""
    snap_time = datetime.utcnow().replace(second=0, microsecond=0)
    data = await _fetch_snapshot(config.underlying)
    count = await _save_snapshot(
        config.underlying, data["spot"], data["instruments"], snap_time
    )
    return {
        "message": f"已保存 {count} 条记录",
        "snapshot_time": snap_time.isoformat(),
        "spot": data["spot"],
        "instrument_count": len(data["instruments"]),
        "saved_count": count,
    }


@router.get("/available-times")
async def get_available_times(
    underlying: str = "BTC",
    date_str: Optional[str] = None,
):
    """获取某天可用的快照时间列表。"""
    db = SessionLocal()
    try:
        query = db.query(
            distinct(HFOptionTick.snapshot_time)
        ).filter(
            HFOptionTick.underlying == underlying,
        )
        if date_str:
            d = date.fromisoformat(date_str)
            start = datetime(d.year, d.month, d.day)
            end = start + timedelta(days=1)
            query = query.filter(
                HFOptionTick.snapshot_time >= start,
                HFOptionTick.snapshot_time < end,
            )
        else:
            # Default: last 24 hours
            since = datetime.utcnow() - timedelta(hours=24)
            query = query.filter(HFOptionTick.snapshot_time >= since)

        rows = query.order_by(HFOptionTick.snapshot_time.desc()).limit(1440).all()
        times = [r[0].isoformat() if hasattr(r[0], 'isoformat') else str(r[0]) for r in rows]
        return {"underlying": underlying, "times": times, "count": len(times)}
    finally:
        db.close()


@router.get("/available-dates")
async def get_available_dates(underlying: str = "BTC"):
    """获取有高频数据的日期列表。"""
    db = SessionLocal()
    try:
        # SQLite: use date() function to extract date part
        from sqlalchemy import cast, Date
        rows = db.query(
            sa_func.date(HFOptionTick.snapshot_time).label("d"),
            sa_func.count(distinct(HFOptionTick.snapshot_time)).label("snap_count"),
        ).filter(
            HFOptionTick.underlying == underlying,
        ).group_by(
            sa_func.date(HFOptionTick.snapshot_time)
        ).order_by(
            sa_func.date(HFOptionTick.snapshot_time).desc()
        ).limit(365).all()

        return {
            "underlying": underlying,
            "dates": [{"date": str(r.d), "snapshots": r.snap_count} for r in rows],
        }
    finally:
        db.close()


@router.get("/snapshot-data")
async def get_snapshot_data(
    underlying: str = "BTC",
    snapshot_time: Optional[str] = None,
    option_type: str = "PUT",
):
    """获取某个时间点的快照数据，格式与 data-availability 一致。
    返回 strike × expiry 矩阵。"""
    from app.api.deribit import (
        find_all_deribit_expiries, get_strike_step, find_nearest_strike,
        interpolate_iv_at_strike,
    )
    from app.services.pricing import black_scholes_price, calculate_time_to_expiration
    RISK_FREE_RATE = 0.05

    db = SessionLocal()
    try:
        if not snapshot_time:
            # Get latest snapshot time
            latest = db.query(
                sa_func.max(HFOptionTick.snapshot_time)
            ).filter(
                HFOptionTick.underlying == underlying,
            ).scalar()
            if not latest:
                return {"error": "无高频数据", "cells": [], "strikes": [], "expiries": []}
            snap_dt = latest
        else:
            # Parse and find nearest snapshot
            target = datetime.fromisoformat(snapshot_time.replace("Z", "").replace("+00:00", ""))
            # Find closest snapshot within 2 minutes
            window_start = target - timedelta(minutes=2)
            window_end = target + timedelta(minutes=2)
            nearest = db.query(HFOptionTick.snapshot_time).filter(
                HFOptionTick.underlying == underlying,
                HFOptionTick.snapshot_time >= window_start,
                HFOptionTick.snapshot_time <= window_end,
            ).order_by(
                sa_func.abs(
                    sa_func.julianday(HFOptionTick.snapshot_time) -
                    sa_func.julianday(target)
                )
            ).first()
            if not nearest:
                return {"error": f"未找到 {snapshot_time} 附近的快照", "cells": [], "strikes": [], "expiries": []}
            snap_dt = nearest[0]

        # Load all ticks for this snapshot
        rows = db.query(HFOptionTick).filter(
            HFOptionTick.underlying == underlying,
            HFOptionTick.snapshot_time == snap_dt,
            HFOptionTick.option_type == option_type,
        ).all()

        if not rows:
            return {
                "underlying": underlying,
                "snapshot_time": str(snap_dt),
                "option_type": option_type,
                "spot_price": None,
                "expiries": [], "strikes": [], "cells": [],
                "summary": {"total": 0, "real": 0, "estimated": 0, "no_data": 0},
            }

        spot = rows[0].spot_price

        # Build data map and smile
        data_map = {}
        smile_by_expiry = {}
        real_expiry_set = set()
        real_strike_set = set()

        for r in rows:
            exp_str = r.expiry_date
            real_expiry_set.add(exp_str)
            real_strike_set.add(r.strike)
            data_map[(exp_str, r.strike)] = {
                "bid": r.bid_price,
                "ask": r.ask_price,
                "last": r.last_price,
                "mark": r.mark_price,
                "bid_usd": r.bid_usd,
                "ask_usd": r.ask_usd,
                "last_usd": r.last_usd,
                "mark_usd": r.mark_usd,
                "volume": r.volume_24h,
                "oi": r.open_interest,
                "iv": r.iv_mark,
                "instrument": r.instrument_name,
            }
            if r.iv_mark and r.iv_mark > 0:
                if exp_str not in smile_by_expiry:
                    smile_by_expiry[exp_str] = []
                smile_by_expiry[exp_str].append((r.strike, r.iv_mark / 100.0))

        # Generate strike grid
        step = get_strike_step(underlying, spot)
        atm = find_nearest_strike(spot, step)
        num_steps = 15
        all_strikes = set()
        for i in range(-num_steps, num_steps + 1):
            s = atm + i * step
            if s > 0:
                all_strikes.add(s)
        for s in real_strike_set:
            all_strikes.add(s)
        all_strikes = sorted(all_strikes)

        # Use real expiries from the snapshot (these are the actual active instruments)
        all_expiries = sorted(real_expiry_set)

        # Build cells
        cells = []
        real_count = 0
        estimated_count = 0
        no_data_count = 0

        for exp_str in all_expiries:
            smile = smile_by_expiry.get(exp_str, [])
            has_smile = len(smile) >= 2
            try:
                exp_date = date.fromisoformat(exp_str)
                snap_date = snap_dt.date() if hasattr(snap_dt, 'date') else date.today()
                T = calculate_time_to_expiration(exp_date, snap_date)
            except Exception:
                T = 0

            for strike in all_strikes:
                key = (exp_str, strike)
                if key in data_map:
                    d = data_map[key]
                    cells.append({
                        "expiry": exp_str,
                        "strike": strike,
                        "status": "real",
                        **d,
                    })
                    real_count += 1
                elif has_smile and T > 0.001:
                    iv_est = interpolate_iv_at_strike(smile, strike)
                    if iv_est and 0.01 < iv_est < 5.0:
                        price_est = black_scholes_price(
                            spot, strike, T, RISK_FREE_RATE, iv_est, option_type
                        )
                        cells.append({
                            "expiry": exp_str,
                            "strike": strike,
                            "status": "estimated",
                            "iv": round(iv_est, 6),
                            "mark_usd": round(float(price_est), 2),
                        })
                        estimated_count += 1
                    else:
                        cells.append({"expiry": exp_str, "strike": strike, "status": "no_data"})
                        no_data_count += 1
                else:
                    cells.append({"expiry": exp_str, "strike": strike, "status": "no_data"})
                    no_data_count += 1

        return {
            "underlying": underlying,
            "snapshot_time": str(snap_dt),
            "option_type": option_type,
            "spot_price": spot,
            "expiries": all_expiries,
            "strikes": all_strikes,
            "cells": cells,
            "summary": {
                "total": real_count + estimated_count + no_data_count,
                "real": real_count,
                "estimated": estimated_count,
                "no_data": no_data_count,
            },
        }
    finally:
        db.close()


@router.get("/stats")
async def get_hf_stats(underlying: str = "BTC"):
    """获取高频数据统计。"""
    db = SessionLocal()
    try:
        total = db.query(sa_func.count(HFOptionTick.id)).filter(
            HFOptionTick.underlying == underlying,
        ).scalar() or 0

        snap_count = db.query(
            sa_func.count(distinct(HFOptionTick.snapshot_time))
        ).filter(
            HFOptionTick.underlying == underlying,
        ).scalar() or 0

        latest = db.query(sa_func.max(HFOptionTick.snapshot_time)).filter(
            HFOptionTick.underlying == underlying,
        ).scalar()

        earliest = db.query(sa_func.min(HFOptionTick.snapshot_time)).filter(
            HFOptionTick.underlying == underlying,
        ).scalar()

        return {
            "underlying": underlying,
            "total_ticks": total,
            "snapshot_count": snap_count,
            "earliest": str(earliest) if earliest else None,
            "latest": str(latest) if latest else None,
        }
    finally:
        db.close()


@router.delete("/data")
async def clear_hf_data(
    underlying: Optional[str] = None,
    before_date: Optional[str] = None,
):
    """清除高频数据。"""
    db = SessionLocal()
    try:
        query = db.query(HFOptionTick)
        if underlying:
            query = query.filter(HFOptionTick.underlying == underlying)
        if before_date:
            d = date.fromisoformat(before_date)
            dt = datetime(d.year, d.month, d.day)
            query = query.filter(HFOptionTick.snapshot_time < dt)
        count = query.delete()
        db.commit()
        return {"message": f"已删除 {count} 条记录", "count": count}
    finally:
        db.close()


@router.get("/instrument-series")
async def get_instrument_series(
    underlying: str = "BTC",
    expiry_date: str = "",
    strike: float = 0,
    option_type: str = "PUT",
):
    """获取某个期权合约的全部时间序列数据（所有日期拼接）。"""
    db = SessionLocal()
    try:
        query = db.query(HFOptionTick).filter(
            HFOptionTick.underlying == underlying,
            HFOptionTick.expiry_date == expiry_date,
            HFOptionTick.strike == strike,
            HFOptionTick.option_type == option_type,
        )
        rows = query.order_by(HFOptionTick.snapshot_time.asc()).limit(50000).all()

        suffix = "P" if option_type == "PUT" else "C"
        instrument = f"{underlying}-{expiry_date}-{int(strike)}-{suffix}" if rows == [] else (rows[0].instrument_name if rows else "")

        series = []
        for r in rows:
            series.append({
                "time": r.snapshot_time.isoformat() if hasattr(r.snapshot_time, 'isoformat') else str(r.snapshot_time),
                "bid_usd": r.bid_usd,
                "ask_usd": r.ask_usd,
                "last_usd": r.last_usd,
                "mark_usd": r.mark_usd,
                "bid": r.bid_price,
                "ask": r.ask_price,
                "last": r.last_price,
                "mark": r.mark_price,
                "iv": r.iv_mark,
                "volume": r.volume_24h,
                "oi": r.open_interest,
                "spot": r.spot_price,
            })

        return {
            "underlying": underlying,
            "expiry_date": expiry_date,
            "strike": strike,
            "option_type": option_type,
            "instrument": instrument,
            "count": len(series),
            "series": series,
        }
    finally:
        db.close()
