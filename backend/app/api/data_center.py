"""数据中心 API — 管理回测所需的历史数据预取和缓存。

功能：
1. 查看各品种已缓存的数据统计
2. 一键收取指定品种、时间跨度的历史价格和IV数据
3. 标记无数据的到期日（3次失败后不再请求）
4. 回测时优先从数据库读取，仅缺失时才请求API
"""
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from typing import List, Optional, Dict
from datetime import datetime, date, timedelta, timezone
import asyncio
import json
import httpx

from app.core.database import SessionLocal
from app.core.config import create_http_client, get_proxy_config, set_proxy
from app.models.deribit_cache import DeribitPriceCache, DeribitIVCache
from app.models.data_collection import OkxPriceCache, DataCollectionLog
from sqlalchemy import func as sa_func, distinct

router = APIRouter(prefix="/api/data-center", tags=["data-center"])

OKX_BASE = "https://www.okx.com"
DERIBIT_BASE = "https://www.deribit.com/api/v2"
MAX_RETRY = 3


# ── Helper: OKX price fetch with DB cache ────────────────────────────

async def _fetch_okx_prices_with_cache(
    underlying: str, start_date: date, end_date: date,
    force: bool = False,
) -> Dict[date, float]:
    """Fetch OKX daily prices, using DB cache first.
    Returns {date: close_price}."""
    inst_id = underlying if "-" in underlying else f"{underlying}-USD"
    db = SessionLocal()
    try:
        # Check cache
        if not force:
            rows = db.query(OkxPriceCache).filter(
                OkxPriceCache.underlying == inst_id,
                OkxPriceCache.trade_date >= start_date,
                OkxPriceCache.trade_date <= end_date,
            ).all()
            cached = {r.trade_date: r.close_price for r in rows}
            expected = (end_date - start_date).days
            if len(cached) >= expected * 0.8:
                return cached
    finally:
        db.close()

    # Fetch from API
    all_candles = {}
    end_ts = int(datetime.combine(end_date + timedelta(days=1), datetime.min.time(),
                                   tzinfo=timezone.utc).timestamp() * 1000)
    start_ts = int(datetime.combine(start_date, datetime.min.time(),
                                     tzinfo=timezone.utc).timestamp() * 1000)
    current_after = end_ts

    try:
        async with create_http_client() as client:
            for _ in range(50):
                url = f"{OKX_BASE}/api/v5/market/history-index-candles"
                params = {"instId": inst_id, "bar": "1D", "limit": "100", "after": str(current_after)}
                resp = await client.get(url, params=params, headers={"User-Agent": "DataCenter/1.0"})
                if resp.status_code == 429:
                    await asyncio.sleep(1.0)
                    resp = await client.get(url, params=params, headers={"User-Agent": "DataCenter/1.0"})
                resp.raise_for_status()
                data = resp.json()
                if data.get("code") != "0" or not data.get("data"):
                    break
                for c in data["data"]:
                    ts = int(c[0])
                    if ts < start_ts:
                        continue
                    dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).date()
                    all_candles[dt] = {
                        "open": float(c[1]), "high": float(c[2]),
                        "low": float(c[3]), "close": float(c[4]),
                    }
                oldest_ts = int(data["data"][-1][0])
                if oldest_ts <= start_ts:
                    break
                current_after = oldest_ts
                await asyncio.sleep(0.25)
    except Exception as e:
        print(f"[OKX] API error fetching prices: {e}")
        if not all_candles:
            # Return whatever we have in cache
            db2 = SessionLocal()
            try:
                rows = db2.query(OkxPriceCache).filter(
                    OkxPriceCache.underlying == inst_id,
                    OkxPriceCache.trade_date >= start_date,
                    OkxPriceCache.trade_date <= end_date,
                ).all()
                if rows:
                    print(f"[OKX] Falling back to {len(rows)} cached prices")
                    return {r.trade_date: r.close_price for r in rows}
            finally:
                db2.close()
            raise

    # Save to DB
    db = SessionLocal()
    try:
        for dt, ohlc in all_candles.items():
            exists = db.query(OkxPriceCache).filter(
                OkxPriceCache.underlying == inst_id,
                OkxPriceCache.trade_date == dt,
            ).first()
            if not exists:
                db.add(OkxPriceCache(
                    underlying=inst_id, trade_date=dt,
                    open_price=ohlc["open"], high_price=ohlc["high"],
                    low_price=ohlc["low"], close_price=ohlc["close"],
                ))
        db.commit()
    finally:
        db.close()

    return {dt: ohlc["close"] for dt, ohlc in all_candles.items()}


def get_okx_cached_prices(underlying: str, start_date: date, end_date: date) -> Dict[date, float]:
    """Load OKX cached prices from DB (sync)."""
    inst_id = underlying if "-" in underlying else f"{underlying}-USD"
    db = SessionLocal()
    try:
        rows = db.query(OkxPriceCache).filter(
            OkxPriceCache.underlying == inst_id,
            OkxPriceCache.trade_date >= start_date,
            OkxPriceCache.trade_date <= end_date,
        ).all()
        return {r.trade_date: r.close_price for r in rows}
    finally:
        db.close()


# ── Schemas ──────────────────────────────────────────────────────────

class CollectRequest(BaseModel):
    source: str = Field(..., description="数据源: deribit, okx")
    underlying: str = Field(default="BTC")
    start_date: date
    end_date: date
    collect_iv: bool = Field(default=False, description="是否同时收取IV数据")
    iv_sample_interval: int = Field(default=7, description="IV采样间隔天数(1=每天,7=每周,30=每月)")


class CollectResponse(BaseModel):
    source: str
    underlying: str
    price_count: int
    iv_count: int = 0
    message: str


# ── API Endpoints ────────────────────────────────────────────────────

@router.get("/stats")
async def get_data_stats():
    """获取所有缓存数据的统计信息。"""
    db = SessionLocal()
    try:
        # Deribit prices
        deribit_prices = db.query(
            DeribitPriceCache.underlying,
            sa_func.count(DeribitPriceCache.id).label("count"),
            sa_func.min(DeribitPriceCache.trade_date).label("min_date"),
            sa_func.max(DeribitPriceCache.trade_date).label("max_date"),
        ).group_by(DeribitPriceCache.underlying).all()

        # Deribit IV (exclude sentinels)
        deribit_iv = db.query(
            DeribitIVCache.underlying,
            sa_func.count(DeribitIVCache.id).label("count"),
            sa_func.min(DeribitIVCache.target_date).label("min_date"),
            sa_func.max(DeribitIVCache.target_date).label("max_date"),
            sa_func.count(distinct(DeribitIVCache.expiry_date)).label("expiry_count"),
        ).filter(DeribitIVCache.strike > 0).group_by(DeribitIVCache.underlying).all()

        # Deribit IV sentinels (no-data markers)
        deribit_sentinels = db.query(
            DeribitIVCache.underlying,
            sa_func.count(DeribitIVCache.id).label("count"),
        ).filter(DeribitIVCache.strike == -1).group_by(DeribitIVCache.underlying).all()

        # OKX prices
        okx_prices = db.query(
            OkxPriceCache.underlying,
            sa_func.count(OkxPriceCache.id).label("count"),
            sa_func.min(OkxPriceCache.trade_date).label("min_date"),
            sa_func.max(OkxPriceCache.trade_date).label("max_date"),
        ).group_by(OkxPriceCache.underlying).all()

        # Collection logs
        failed_logs = db.query(
            DataCollectionLog.source,
            DataCollectionLog.underlying,
            sa_func.count(DataCollectionLog.id).label("count"),
        ).filter(DataCollectionLog.no_data_confirmed == True).group_by(
            DataCollectionLog.source, DataCollectionLog.underlying
        ).all()

        return {
            "deribit_prices": [
                {"underlying": r.underlying, "count": r.count,
                 "min_date": r.min_date.isoformat() if r.min_date else None,
                 "max_date": r.max_date.isoformat() if r.max_date else None}
                for r in deribit_prices
            ],
            "deribit_iv": [
                {"underlying": r.underlying, "count": r.count,
                 "min_date": r.min_date.isoformat() if r.min_date else None,
                 "max_date": r.max_date.isoformat() if r.max_date else None,
                 "expiry_count": r.expiry_count}
                for r in deribit_iv
            ],
            "deribit_sentinels": [
                {"underlying": r.underlying, "no_data_count": r.count}
                for r in deribit_sentinels
            ],
            "okx_prices": [
                {"underlying": r.underlying, "count": r.count,
                 "min_date": r.min_date.isoformat() if r.min_date else None,
                 "max_date": r.max_date.isoformat() if r.max_date else None}
                for r in okx_prices
            ],
            "failed_collections": [
                {"source": r.source, "underlying": r.underlying, "count": r.count}
                for r in failed_logs
            ],
        }
    finally:
        db.close()


@router.post("/collect-stream")
async def collect_data_stream(req: CollectRequest):
    """收取数据（SSE流式进度）。"""

    async def event_generator():
        source = req.source.lower()
        underlying = req.underlying
        total_days = (req.end_date - req.start_date).days

        yield f"data: {json.dumps({'type': 'progress', 'pct': 0, 'message': f'开始收取 {source}/{underlying} 数据 ({req.start_date} ~ {req.end_date})...'})}\n\n"

        try:
            if source == "deribit":
                from app.api.deribit import (
                    fetch_deribit_index_prices, fetch_iv_smile,
                    find_deribit_expiry, is_cached_no_data,
                    get_cached_iv_smile, find_all_deribit_expiries,
                )

                price_map = await fetch_deribit_index_prices(
                    underlying, req.start_date, req.end_date, force=True)
                price_count = len(price_map)

                yield f"data: {json.dumps({'type': 'progress', 'pct': 20, 'message': f'已收取 {price_count} 天 Deribit 价格数据'})}\n\n"

                iv_count = 0
                if req.collect_iv and price_map:
                    sorted_dates = sorted(price_map.keys())
                    # Sample dates based on interval
                    interval = max(1, req.iv_sample_interval)
                    sampled_dates = sorted_dates[::interval]
                    # Always include last date
                    if sorted_dates[-1] not in sampled_dates:
                        sampled_dates.append(sorted_dates[-1])
                    total_sampled = len(sampled_dates)
                    skipped = 0

                    yield f"data: {json.dumps({'type': 'progress', 'pct': 22, 'message': f'开始收取IV数据: 共 {len(sorted_dates)} 天, 采样 {total_sampled} 天 (间隔 {interval} 天)'})}\n\n"

                    consecutive_errors = 0
                    MAX_CONSECUTIVE_ERRORS = 5

                    async with create_http_client() as client:
                        for idx, td in enumerate(sampled_dates):
                            if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                                yield f"data: {json.dumps({'type': 'progress', 'pct': 20 + int(80 * (idx + 1) / total_sampled), 'message': f'连续 {MAX_CONSECUTIVE_ERRORS} 次网络错误, 停止IV收取 (已获取 {iv_count} 个IV点)'})}\n\n"
                                break

                            spot = price_map[td]
                            if not spot or spot <= 0:
                                continue

                            # Find all valid Deribit expiries for this date
                            expiries = find_all_deribit_expiries(td)
                            # Limit: skip expiries > 2 years out to avoid too many requests
                            max_expiry = td + timedelta(days=730)
                            expiries = [e for e in expiries if e <= max_expiry]

                            day_had_error = False
                            for expiry in expiries:
                                for opt_type in ["PUT", "CALL"]:
                                    cached = get_cached_iv_smile(
                                        underlying, expiry, opt_type, td
                                    )
                                    if cached:
                                        skipped += 1
                                        continue
                                    if is_cached_no_data(
                                        underlying, expiry, opt_type, td
                                    ):
                                        skipped += 1
                                        continue
                                    try:
                                        smile = await fetch_iv_smile(
                                            client, underlying, expiry,
                                            spot, opt_type, td,
                                            num_strikes=11,
                                        )
                                        iv_count += len(smile)
                                        consecutive_errors = 0
                                    except (httpx.ConnectError, httpx.ConnectTimeout) as e:
                                        consecutive_errors += 1
                                        day_had_error = True
                                        print(
                                            f"[DataCenter] Network error ({consecutive_errors}/{MAX_CONSECUTIVE_ERRORS}): {e}"
                                        )
                                    except Exception as e:
                                        print(
                                            f"[DataCenter] IV error: "
                                            f"{underlying} {opt_type} "
                                            f"expiry={expiry} date={td}: {e}"
                                        )

                            pct = 20 + int(
                                80 * (idx + 1) / total_sampled
                            )
                            if (idx + 1) % 3 == 0 or idx == total_sampled - 1:
                                yield f"data: {json.dumps({'type': 'progress', 'pct': pct, 'message': f'IV收取进度: {idx+1}/{total_sampled} 天, 已获取 {iv_count} 个IV点, 跳过 {skipped} 个已缓存'})}\n\n"

                yield f"data: {json.dumps({'type': 'result', 'data': {'source': source, 'underlying': underlying, 'price_count': price_count, 'iv_count': iv_count, 'message': f'成功收取 {price_count} 天价格, {iv_count} 个IV数据点'}})}\n\n"

            elif source == "okx":
                price_map = await _fetch_okx_prices_with_cache(
                    underlying, req.start_date, req.end_date, force=True)
                price_count = len(price_map)

                yield f"data: {json.dumps({'type': 'progress', 'pct': 100, 'message': f'已收取 {price_count} 天 OKX 价格数据'})}\n\n"
                yield f"data: {json.dumps({'type': 'result', 'data': {'source': source, 'underlying': underlying, 'price_count': price_count, 'iv_count': 0, 'message': f'成功收取 {price_count} 天价格数据'}})}\n\n"

            else:
                yield f"data: {json.dumps({'type': 'error', 'message': f'不支持的数据源: {source}'})}\n\n"
                return

        except Exception as e:
            import traceback
            traceback.print_exc()
            yield f"data: {json.dumps({'type': 'error', 'message': f'数据收取失败: {str(e)}'})}\n\n"

        yield "data: {\"type\": \"done\"}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.get("/prices")
async def get_cached_prices(
    source: str = "deribit",
    underlying: str = "BTC",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    limit: int = 100,
):
    """查看已缓存的价格数据。"""
    db = SessionLocal()
    try:
        if source == "deribit":
            query = db.query(DeribitPriceCache).filter(
                DeribitPriceCache.underlying == underlying)
            if start_date:
                query = query.filter(DeribitPriceCache.trade_date >= date.fromisoformat(start_date))
            if end_date:
                query = query.filter(DeribitPriceCache.trade_date <= date.fromisoformat(end_date))
            rows = query.order_by(DeribitPriceCache.trade_date.desc()).limit(limit).all()
            return {
                "source": "deribit", "underlying": underlying,
                "count": len(rows),
                "data": [{"date": r.trade_date.isoformat(), "close": r.close_price} for r in rows],
            }
        elif source == "okx":
            inst_id = underlying if "-" in underlying else f"{underlying}-USD"
            query = db.query(OkxPriceCache).filter(OkxPriceCache.underlying == inst_id)
            if start_date:
                query = query.filter(OkxPriceCache.trade_date >= date.fromisoformat(start_date))
            if end_date:
                query = query.filter(OkxPriceCache.trade_date <= date.fromisoformat(end_date))
            rows = query.order_by(OkxPriceCache.trade_date.desc()).limit(limit).all()
            return {
                "source": "okx", "underlying": inst_id,
                "count": len(rows),
                "data": [{"date": r.trade_date.isoformat(), "close": r.close_price,
                          "open": r.open_price, "high": r.high_price, "low": r.low_price}
                         for r in rows],
            }
        else:
            raise HTTPException(status_code=400, detail=f"不支持的数据源: {source}")
    finally:
        db.close()


@router.get("/iv-data")
async def get_cached_iv_data(
    underlying: str = "BTC",
    expiry_date: Optional[str] = None,
    target_date: Optional[str] = None,
    limit: int = 200,
):
    """查看已缓存的IV数据。"""
    db = SessionLocal()
    try:
        query = db.query(DeribitIVCache).filter(
            DeribitIVCache.underlying == underlying,
            DeribitIVCache.strike > 0,
        )
        if expiry_date:
            query = query.filter(DeribitIVCache.expiry_date == date.fromisoformat(expiry_date))
        if target_date:
            query = query.filter(DeribitIVCache.target_date == date.fromisoformat(target_date))
        rows = query.order_by(DeribitIVCache.target_date.desc(), DeribitIVCache.strike).limit(limit).all()

        # Group by expiry
        grouped = {}
        for r in rows:
            key = r.expiry_date.isoformat()
            if key not in grouped:
                grouped[key] = []
            grouped[key].append({
                "target_date": r.target_date.isoformat(),
                "strike": r.strike, "iv": r.iv,
                "price_usd": r.trade_price_usd,
                "spot": r.spot_price,
                "option_type": r.option_type,
                "instrument": r.instrument,
            })

        return {
            "underlying": underlying,
            "total_points": len(rows),
            "expiries": grouped,
        }
    finally:
        db.close()


@router.delete("/cache")
async def clear_cache(source: Optional[str] = None, underlying: Optional[str] = None):
    """清除缓存数据。"""
    db = SessionLocal()
    try:
        deleted = {}
        if source is None or source == "deribit":
            q1 = db.query(DeribitPriceCache)
            q2 = db.query(DeribitIVCache)
            if underlying:
                q1 = q1.filter(DeribitPriceCache.underlying == underlying)
                q2 = q2.filter(DeribitIVCache.underlying == underlying)
            c1 = q1.delete()
            c2 = q2.delete()
            deleted["deribit_prices"] = c1
            deleted["deribit_iv"] = c2

        if source is None or source == "okx":
            q = db.query(OkxPriceCache)
            if underlying:
                inst_id = underlying if "-" in underlying else f"{underlying}-USD"
                q = q.filter(OkxPriceCache.underlying == inst_id)
            c = q.delete()
            deleted["okx_prices"] = c

        if source is None or source == "logs":
            q = db.query(DataCollectionLog)
            if underlying:
                q = q.filter(DataCollectionLog.underlying == underlying)
            c = q.delete()
            deleted["collection_logs"] = c

        db.commit()
        return {"message": "缓存已清除", "deleted": deleted}
    finally:
        db.close()


@router.delete("/sentinels")
async def clear_sentinels(underlying: Optional[str] = None):
    """清除无数据标记（sentinel），允许重新尝试获取。"""
    db = SessionLocal()
    try:
        q = db.query(DeribitIVCache).filter(DeribitIVCache.strike == -1)
        if underlying:
            q = q.filter(DeribitIVCache.underlying == underlying)
        count = q.delete()
        db.commit()
        return {"message": f"已清除 {count} 条无数据标记", "count": count}
    finally:
        db.close()


# ── IV / Price record editing ────────────────────────────────────────

class UpdateIVRecord(BaseModel):
    iv: Optional[float] = None
    trade_price_usd: Optional[float] = None


@router.put("/iv-record/{record_id}")
async def update_iv_record(record_id: int, body: UpdateIVRecord):
    """修改单条IV缓存记录的IV值或价格。"""
    db = SessionLocal()
    try:
        row = db.query(DeribitIVCache).filter(DeribitIVCache.id == record_id).first()
        if not row:
            raise HTTPException(status_code=404, detail="记录不存在")
        if body.iv is not None:
            row.iv = body.iv
        if body.trade_price_usd is not None:
            row.trade_price_usd = body.trade_price_usd
        db.commit()
        return {
            "id": row.id, "underlying": row.underlying,
            "expiry_date": row.expiry_date.isoformat(),
            "option_type": row.option_type,
            "target_date": row.target_date.isoformat(),
            "strike": row.strike, "iv": row.iv,
            "trade_price_usd": row.trade_price_usd,
            "instrument": row.instrument,
        }
    finally:
        db.close()


@router.delete("/iv-record/{record_id}")
async def delete_iv_record(record_id: int):
    """删除单条IV缓存记录。"""
    db = SessionLocal()
    try:
        row = db.query(DeribitIVCache).filter(DeribitIVCache.id == record_id).first()
        if not row:
            raise HTTPException(status_code=404, detail="记录不存在")
        db.delete(row)
        db.commit()
        return {"message": "已删除", "id": record_id}
    finally:
        db.close()


class UpdatePriceRecord(BaseModel):
    close_price: Optional[float] = None


@router.put("/price-record/deribit/{record_id}")
async def update_deribit_price(record_id: int, body: UpdatePriceRecord):
    """修改Deribit价格缓存记录。"""
    db = SessionLocal()
    try:
        row = db.query(DeribitPriceCache).filter(DeribitPriceCache.id == record_id).first()
        if not row:
            raise HTTPException(status_code=404, detail="记录不存在")
        if body.close_price is not None:
            row.close_price = body.close_price
        db.commit()
        return {"id": row.id, "underlying": row.underlying,
                "trade_date": row.trade_date.isoformat(), "close_price": row.close_price}
    finally:
        db.close()


@router.delete("/price-record/deribit/{record_id}")
async def delete_deribit_price(record_id: int):
    """删除Deribit价格缓存记录。"""
    db = SessionLocal()
    try:
        row = db.query(DeribitPriceCache).filter(DeribitPriceCache.id == record_id).first()
        if not row:
            raise HTTPException(status_code=404, detail="记录不存在")
        db.delete(row)
        db.commit()
        return {"message": "已删除", "id": record_id}
    finally:
        db.close()


@router.put("/price-record/okx/{record_id}")
async def update_okx_price(record_id: int, body: UpdatePriceRecord):
    """修改OKX价格缓存记录。"""
    db = SessionLocal()
    try:
        row = db.query(OkxPriceCache).filter(OkxPriceCache.id == record_id).first()
        if not row:
            raise HTTPException(status_code=404, detail="记录不存在")
        if body.close_price is not None:
            row.close_price = body.close_price
        db.commit()
        return {"id": row.id, "underlying": row.underlying,
                "trade_date": row.trade_date.isoformat(), "close_price": row.close_price}
    finally:
        db.close()


@router.delete("/price-record/okx/{record_id}")
async def delete_okx_price(record_id: int):
    """删除OKX价格缓存记录。"""
    db = SessionLocal()
    try:
        row = db.query(OkxPriceCache).filter(OkxPriceCache.id == record_id).first()
        if not row:
            raise HTTPException(status_code=404, detail="记录不存在")
        db.delete(row)
        db.commit()
        return {"message": "已删除", "id": record_id}
    finally:
        db.close()


# ── IV data with IDs (for editing) ──────────────────────────────────

@router.get("/iv-data-editable")
async def get_iv_data_editable(
    underlying: str = "BTC",
    expiry_date: Optional[str] = None,
    target_date: Optional[str] = None,
    option_type: Optional[str] = None,
    min_strike: Optional[float] = None,
    max_strike: Optional[float] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    limit: int = 500,
):
    """查看可编辑的IV数据（包含record id）。"""
    db = SessionLocal()
    try:
        query = db.query(DeribitIVCache).filter(
            DeribitIVCache.underlying == underlying,
            DeribitIVCache.strike > 0,
        )
        if expiry_date:
            query = query.filter(DeribitIVCache.expiry_date == date.fromisoformat(expiry_date))
        if target_date:
            query = query.filter(DeribitIVCache.target_date == date.fromisoformat(target_date))
        if start_date:
            query = query.filter(DeribitIVCache.target_date >= date.fromisoformat(start_date))
        if end_date:
            query = query.filter(DeribitIVCache.target_date <= date.fromisoformat(end_date))
        if option_type:
            query = query.filter(DeribitIVCache.option_type == option_type)
        if min_strike is not None:
            query = query.filter(DeribitIVCache.strike >= min_strike)
        if max_strike is not None:
            query = query.filter(DeribitIVCache.strike <= max_strike)
        rows = query.order_by(
            DeribitIVCache.expiry_date, DeribitIVCache.target_date, DeribitIVCache.strike
        ).limit(limit).all()

        # Collect distinct expiry dates and target dates for filters
        all_expiries = sorted(set(r.expiry_date.isoformat() for r in rows))
        all_targets = sorted(set(r.target_date.isoformat() for r in rows))
        strike_range = [min((r.strike for r in rows), default=0),
                        max((r.strike for r in rows), default=0)] if rows else [0, 0]

        return {
            "underlying": underlying,
            "total": len(rows),
            "expiry_dates": all_expiries,
            "target_dates": all_targets,
            "strike_range": strike_range,
            "data": [{
                "id": r.id,
                "expiry_date": r.expiry_date.isoformat(),
                "option_type": r.option_type,
                "target_date": r.target_date.isoformat(),
                "spot_price": r.spot_price,
                "strike": r.strike,
                "iv": r.iv,
                "trade_price_usd": r.trade_price_usd,
                "instrument": r.instrument,
            } for r in rows],
        }
    finally:
        db.close()


@router.get("/prices-editable")
async def get_prices_editable(
    source: str = "deribit",
    underlying: str = "BTC",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    limit: int = 1000,
):
    """查看可编辑的价格数据（包含record id）。"""
    db = SessionLocal()
    try:
        if source == "deribit":
            query = db.query(DeribitPriceCache).filter(
                DeribitPriceCache.underlying == underlying)
            if start_date:
                query = query.filter(DeribitPriceCache.trade_date >= date.fromisoformat(start_date))
            if end_date:
                query = query.filter(DeribitPriceCache.trade_date <= date.fromisoformat(end_date))
            rows = query.order_by(DeribitPriceCache.trade_date.desc()).limit(limit).all()
            return {
                "source": "deribit", "underlying": underlying,
                "count": len(rows),
                "data": [{"id": r.id, "date": r.trade_date.isoformat(),
                          "close": r.close_price} for r in rows],
            }
        elif source == "okx":
            inst_id = underlying if "-" in underlying else f"{underlying}-USD"
            query = db.query(OkxPriceCache).filter(OkxPriceCache.underlying == inst_id)
            if start_date:
                query = query.filter(OkxPriceCache.trade_date >= date.fromisoformat(start_date))
            if end_date:
                query = query.filter(OkxPriceCache.trade_date <= date.fromisoformat(end_date))
            rows = query.order_by(OkxPriceCache.trade_date.desc()).limit(limit).all()
            return {
                "source": "okx", "underlying": inst_id,
                "count": len(rows),
                "data": [{"id": r.id, "date": r.trade_date.isoformat(),
                          "close": r.close_price, "open": r.open_price,
                          "high": r.high_price, "low": r.low_price} for r in rows],
            }
        else:
            raise HTTPException(status_code=400, detail=f"不支持的数据源: {source}")
    finally:
        db.close()


class BatchDeleteIV(BaseModel):
    """批量删除IV记录（按条件筛选）。"""
    underlying: str = Field(default="BTC")
    expiry_date: Optional[str] = None
    target_date: Optional[str] = None
    option_type: Optional[str] = None
    min_strike: Optional[float] = None
    max_strike: Optional[float] = None


@router.post("/iv-batch-delete")
async def batch_delete_iv(body: BatchDeleteIV):
    """批量删除符合条件的IV缓存记录。"""
    db = SessionLocal()
    try:
        query = db.query(DeribitIVCache).filter(
            DeribitIVCache.underlying == body.underlying,
            DeribitIVCache.strike > 0,
        )
        if body.expiry_date:
            query = query.filter(DeribitIVCache.expiry_date == date.fromisoformat(body.expiry_date))
        if body.target_date:
            query = query.filter(DeribitIVCache.target_date == date.fromisoformat(body.target_date))
        if body.option_type:
            query = query.filter(DeribitIVCache.option_type == body.option_type)
        if body.min_strike is not None:
            query = query.filter(DeribitIVCache.strike >= body.min_strike)
        if body.max_strike is not None:
            query = query.filter(DeribitIVCache.strike <= body.max_strike)
        count = query.delete()
        db.commit()
        return {"message": f"已删除 {count} 条记录", "count": count}
    finally:
        db.close()


# ── Proxy settings ───────────────────────────────────────────────────

class ProxySettings(BaseModel):
    enabled: bool = False
    url: Optional[str] = None


@router.get("/proxy")
async def get_proxy():
    """获取当前代理设置。"""
    return get_proxy_config()


@router.put("/proxy")
async def update_proxy(body: ProxySettings):
    """更新代理设置（运行时生效，无需重启）。"""
    set_proxy(body.enabled, body.url)
    cfg = get_proxy_config()
    status = "已启用" if cfg["enabled"] else "已关闭"
    return {"message": f"代理{status}", **cfg}


@router.get("/proxy/test")
async def test_proxy():
    """测试代理连接是否正常。"""
    results = {}
    for use_proxy in [False, True]:
        label = "proxy" if use_proxy else "direct"
        try:
            client = create_http_client(timeout=10.0, connect_timeout=5.0, use_proxy=use_proxy)
            async with client:
                resp = await client.get("https://www.deribit.com/api/v2/public/get_time")
                data = resp.json()
                results[label] = {
                    "ok": True,
                    "status": resp.status_code,
                    "server_time": data.get("result"),
                }
        except Exception as e:
            results[label] = {"ok": False, "error": str(e)}
    return results


# ── Sentinel analysis & retry ────────────────────────────────────────

@router.get("/sentinels/detail")
async def get_sentinel_details(underlying: Optional[str] = None):
    """获取所有无数据标记(sentinel)的详细信息，分析可能的原因。"""
    from app.api.deribit import last_friday_of_month
    db = SessionLocal()
    try:
        query = db.query(DeribitIVCache).filter(DeribitIVCache.strike == -1)
        if underlying:
            query = query.filter(DeribitIVCache.underlying == underlying)
        rows = query.order_by(
            DeribitIVCache.underlying,
            DeribitIVCache.expiry_date,
            DeribitIVCache.target_date,
        ).all()

        sentinels = []
        for r in rows:
            # Analyze: is the expiry a valid Deribit expiry?
            exp = r.expiry_date
            year, month = exp.year, exp.month
            actual_last_fri = last_friday_of_month(year, month)

            # Deribit quarterly months
            quarterly_months = {3, 6, 9, 12}
            is_quarterly = month in quarterly_months
            is_last_friday = exp == actual_last_fri

            # Days between target_date and expiry
            days_to_expiry = (exp - r.target_date).days

            # Possible reasons
            reasons = []
            if not is_last_friday:
                reasons.append(f"到期日{exp}不是当月最后一个周五({actual_last_fri})")
            if days_to_expiry < 0:
                reasons.append(f"查询日{r.target_date}在到期日之后")
            elif days_to_expiry < 7:
                reasons.append(f"查询日距到期仅{days_to_expiry}天，合约可能已下架")
            if not is_quarterly and days_to_expiry > 90:
                reasons.append(f"非季度到期月({month}月)，超过3个月的月度合约可能不存在")

            # Check if there's real IV data for the same expiry (different dates)
            has_real_data = db.query(DeribitIVCache).filter(
                DeribitIVCache.underlying == r.underlying,
                DeribitIVCache.expiry_date == exp,
                DeribitIVCache.strike > 0,
            ).first() is not None

            if has_real_data:
                reasons.append("同一到期日有其他日期的真实数据，可能仅该查询日无数据")

            if not reasons:
                reasons.append("到期日格式正确，可能是该日期确实无交易数据")

            sentinels.append({
                "id": r.id,
                "underlying": r.underlying,
                "expiry_date": exp.isoformat(),
                "option_type": r.option_type,
                "target_date": r.target_date.isoformat(),
                "spot_price": r.spot_price,
                "days_to_expiry": days_to_expiry,
                "is_last_friday": is_last_friday,
                "actual_last_friday": actual_last_fri.isoformat(),
                "is_quarterly": is_quarterly,
                "has_real_data_same_expiry": has_real_data,
                "reasons": reasons,
            })

        # Group summary
        by_expiry = {}
        for s in sentinels:
            key = f"{s['underlying']}-{s['expiry_date']}"
            if key not in by_expiry:
                by_expiry[key] = {"count": 0, "reasons": set()}
            by_expiry[key]["count"] += 1
            for r in s["reasons"]:
                by_expiry[key]["reasons"].add(r)

        summary = [
            {"key": k, "count": v["count"], "reasons": list(v["reasons"])}
            for k, v in by_expiry.items()
        ]

        return {
            "total": len(sentinels),
            "sentinels": sentinels,
            "summary_by_expiry": summary,
        }
    finally:
        db.close()


@router.get("/data-availability")
async def get_data_availability(
    underlying: str = "BTC",
    target_date: Optional[str] = None,
    option_type: str = "PUT",
):
    """获取指定 target_date 的数据可得性矩阵 (strike × expiry)。
    返回所有 expiry_date 和 strike 的组合，标记每个格子的状态:
    - real: 有真实IV数据 (strike > 0)
    - estimated: 通过IV微笑曲线插值估算
    - no_data: 有sentinel标记 (strike == -1) 或完全无数据
    """
    from app.api.deribit import (
        find_all_deribit_expiries, get_strike_step, find_nearest_strike,
        interpolate_iv_at_strike, last_friday_of_month,
    )
    from app.services.pricing import (
        black_scholes_price, calculate_time_to_expiration,
    )
    RISK_FREE_RATE = 0.05

    db = SessionLocal()
    try:
        # If no target_date, get the list of available target_dates
        if not target_date:
            dates = db.query(distinct(DeribitIVCache.target_date)).filter(
                DeribitIVCache.underlying == underlying,
            ).order_by(DeribitIVCache.target_date.desc()).limit(500).all()
            return {
                "target_dates": [d[0].isoformat() for d in dates],
                "message": "请选择一个target_date",
            }

        td = date.fromisoformat(target_date)

        # Get all real IV data for this target_date
        real_rows = db.query(
            DeribitIVCache.expiry_date,
            DeribitIVCache.strike,
            DeribitIVCache.iv,
            DeribitIVCache.trade_price_usd,
            DeribitIVCache.spot_price,
            DeribitIVCache.instrument,
        ).filter(
            DeribitIVCache.underlying == underlying,
            DeribitIVCache.target_date == td,
            DeribitIVCache.option_type == option_type,
            DeribitIVCache.strike > 0,
        ).all()

        # Get sentinel rows for this target_date
        sentinel_rows = db.query(
            DeribitIVCache.expiry_date,
        ).filter(
            DeribitIVCache.underlying == underlying,
            DeribitIVCache.target_date == td,
            DeribitIVCache.strike == -1,
            DeribitIVCache.option_type == option_type,
        ).all()
        sentinel_expiries = set(r.expiry_date for r in sentinel_rows)

        # Determine spot price
        spot = real_rows[0].spot_price if real_rows else None
        if not spot:
            # Try to get spot from price cache
            from app.models.deribit_cache import DeribitPriceCache
            price_row = db.query(DeribitPriceCache).filter(
                DeribitPriceCache.underlying == underlying,
                DeribitPriceCache.trade_date == td,
            ).first()
            if price_row:
                spot = price_row.close_price

        if not spot:
            return {
                "underlying": underlying,
                "target_date": target_date,
                "option_type": option_type,
                "expiries": [],
                "strikes": [],
                "cells": [],
                "summary": {"total": 0, "real": 0, "estimated": 0, "no_data": 0},
                "error": "该日期无现货价格数据",
            }

        # Build data map: (expiry, strike) -> {iv, price, spot, instrument}
        data_map = {}
        real_expiry_set = set()
        real_strike_set = set()
        # Also build per-expiry smile: expiry -> [(strike, iv), ...]
        smile_by_expiry = {}
        for r in real_rows:
            real_expiry_set.add(r.expiry_date)
            real_strike_set.add(r.strike)
            data_map[(r.expiry_date, r.strike)] = {
                "iv": r.iv,
                "price_usd": r.trade_price_usd,
                "spot": r.spot_price,
                "instrument": r.instrument,
            }
            if r.expiry_date not in smile_by_expiry:
                smile_by_expiry[r.expiry_date] = []
            smile_by_expiry[r.expiry_date].append((r.strike, r.iv))

        # Generate full expiry grid using Deribit rules
        all_expiries = find_all_deribit_expiries(td)
        # Limit to 2 years out
        max_expiry_date = td + timedelta(days=730)
        all_expiries = [e for e in all_expiries if e <= max_expiry_date]
        # Also include any expiries from real data or sentinels that might not be in the generated list
        for exp in real_expiry_set:
            if exp not in all_expiries and exp > td:
                all_expiries.append(exp)
        for exp in sentinel_expiries:
            if exp not in all_expiries and exp > td:
                all_expiries.append(exp)
        all_expiries = sorted(set(all_expiries))

        # Generate full strike grid based on spot price
        step = get_strike_step(underlying, spot)
        atm_strike = find_nearest_strike(spot, step)
        # Generate strikes: 15 steps below ATM to 15 steps above ATM
        num_steps = 15
        all_strikes = []
        for i in range(-num_steps, num_steps + 1):
            s = atm_strike + i * step
            if s > 0:
                all_strikes.append(s)
        # Also include any strikes from real data
        for s in real_strike_set:
            if s not in all_strikes:
                all_strikes.append(s)
        all_strikes = sorted(set(all_strikes))

        # Build cells with interpolation
        cells = []
        real_count = 0
        estimated_count = 0
        no_data_count = 0

        for exp in all_expiries:
            is_sentinel = exp in sentinel_expiries
            has_real = exp in smile_by_expiry and len(smile_by_expiry[exp]) >= 2
            smile = smile_by_expiry.get(exp, [])

            T = calculate_time_to_expiration(exp, td)

            for strike in all_strikes:
                key = (exp, strike)
                if key in data_map:
                    d = data_map[key]
                    cells.append({
                        "expiry": exp.isoformat(),
                        "strike": strike,
                        "status": "real",
                        "iv": d["iv"],
                        "price_usd": d["price_usd"],
                        "spot": d["spot"],
                        "instrument": d["instrument"],
                    })
                    real_count += 1
                elif has_real and T > 0.001:
                    # Try to interpolate from the IV smile of this expiry
                    iv_est = interpolate_iv_at_strike(smile, strike)
                    if iv_est and 0.01 < iv_est < 5.0:
                        price_est = black_scholes_price(
                            spot, strike, T, RISK_FREE_RATE, iv_est, option_type
                        )
                        cells.append({
                            "expiry": exp.isoformat(),
                            "strike": strike,
                            "status": "estimated",
                            "iv": round(iv_est, 6),
                            "price_usd": round(float(price_est), 2),
                            "spot": spot,
                        })
                        estimated_count += 1
                    else:
                        cells.append({
                            "expiry": exp.isoformat(),
                            "strike": strike,
                            "status": "no_data",
                        })
                        no_data_count += 1
                else:
                    info = None
                    if is_sentinel:
                        info = "sentinel"
                    cells.append({
                        "expiry": exp.isoformat(),
                        "strike": strike,
                        "status": "no_data",
                        "info": info,
                    })
                    no_data_count += 1

        return {
            "underlying": underlying,
            "target_date": target_date,
            "option_type": option_type,
            "spot_price": spot,
            "expiries": [e.isoformat() for e in all_expiries],
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


@router.get("/data-availability/dates")
async def get_availability_dates(underlying: str = "BTC"):
    """获取有IV数据的所有target_date列表，从2019年1月开始。"""
    db = SessionLocal()
    try:
        min_date = date(2019, 1, 1)
        dates = db.query(distinct(DeribitIVCache.target_date)).filter(
            DeribitIVCache.underlying == underlying,
            DeribitIVCache.target_date >= min_date,
        ).order_by(DeribitIVCache.target_date.desc()).limit(2000).all()
        return {
            "underlying": underlying,
            "dates": [d[0].isoformat() for d in dates],
            "count": len(dates),
        }
    finally:
        db.close()


class RetryRequest(BaseModel):
    sentinel_ids: List[int] = Field(..., description="要重试的sentinel记录ID列表")


@router.post("/sentinels/retry-stream")
async def retry_sentinels_stream(req: RetryRequest):
    """删除指定的sentinel标记并重新尝试获取IV数据（SSE流式）。"""
    from app.api.deribit import fetch_iv_smile, get_cached_iv_smile

    async def event_generator():
        db = SessionLocal()
        try:
            # Load sentinel records
            rows = db.query(DeribitIVCache).filter(
                DeribitIVCache.id.in_(req.sentinel_ids),
                DeribitIVCache.strike == -1,
            ).all()

            if not rows:
                yield f"data: {json.dumps({'type': 'error', 'message': '未找到指定的sentinel记录'})}\n\n"
                yield 'data: {"type": "done"}\n\n'
                return

            total = len(rows)
            yield f"data: {json.dumps({'type': 'progress', 'pct': 0, 'message': f'准备重试 {total} 条记录...'})}\n\n"

            # Collect info and delete sentinels
            retry_items = []
            for r in rows:
                retry_items.append({
                    "underlying": r.underlying,
                    "expiry_date": r.expiry_date,
                    "option_type": r.option_type,
                    "target_date": r.target_date,
                    "spot_price": r.spot_price,
                })
                db.delete(r)

            # Also clear related collection logs so retry_count resets
            for item in retry_items:
                db.query(DataCollectionLog).filter(
                    DataCollectionLog.source == "deribit",
                    DataCollectionLog.data_type == "iv_smile",
                    DataCollectionLog.underlying == item["underlying"],
                    DataCollectionLog.target_date == item["target_date"],
                    DataCollectionLog.expiry_date == item["expiry_date"],
                    DataCollectionLog.option_type == item["option_type"],
                ).delete()

            db.commit()
        finally:
            db.close()

        yield f"data: {json.dumps({'type': 'progress', 'pct': 5, 'message': f'已清除 {total} 条sentinel，开始重新获取...'})}\n\n"

        success_count = 0
        fail_count = 0
        results = []

        try:
            async with create_http_client() as client:
                for idx, item in enumerate(retry_items):
                    pct = 5 + int(90 * (idx + 1) / total)
                    try:
                        smile = await fetch_iv_smile(
                            client,
                            item["underlying"],
                            item["expiry_date"],
                            item["spot_price"],
                            item["option_type"],
                            item["target_date"],
                            num_strikes=7,
                        )
                        if smile:
                            success_count += 1
                            results.append({
                                "expiry": item["expiry_date"].isoformat(),
                                "target": item["target_date"].isoformat(),
                                "type": item["option_type"],
                                "status": "success",
                                "points": len(smile),
                            })
                        else:
                            fail_count += 1
                            results.append({
                                "expiry": item["expiry_date"].isoformat(),
                                "target": item["target_date"].isoformat(),
                                "type": item["option_type"],
                                "status": "no_data",
                                "points": 0,
                            })
                    except Exception as e:
                        fail_count += 1
                        results.append({
                            "expiry": item["expiry_date"].isoformat(),
                            "target": item["target_date"].isoformat(),
                            "type": item["option_type"],
                            "status": "error",
                            "error": str(e),
                        })

                    if (idx + 1) % 3 == 0 or idx == total - 1:
                        yield f"data: {json.dumps({'type': 'progress', 'pct': pct, 'message': f'进度 {idx+1}/{total}, 成功{success_count}, 失败{fail_count}'})}\n\n"

                    await asyncio.sleep(0.3)

        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': f'重试过程出错: {str(e)}'})}\n\n"

        yield f"data: {json.dumps({'type': 'result', 'data': {'total': total, 'success': success_count, 'fail': fail_count, 'results': results}})}\n\n"
        yield 'data: {"type": "done"}\n\n'

    return StreamingResponse(event_generator(), media_type="text/event-stream")
