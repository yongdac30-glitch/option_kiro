"""OKX XAU real-time data collection.
Collects XAUT-USDT spot and XAU-USDT-SWAP perpetual orderbook (best ask/bid),
plus funding rate for the swap.
Priority: WebSocket → REST API polling fallback.
Data is streamed to frontend via SSE and persisted to database."""
from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse
import asyncio
import json
import time
import httpx
from datetime import datetime, timezone
from typing import Dict, List, Optional
from app.core.database import SessionLocal
from app.core.config import create_http_client
from app.models.okx_xau_tick import OkxXauTick

router = APIRouter(prefix="/api/okx-xau", tags=["okx-xau"])

# ── Instrument IDs (confirmed via REST API) ──
SPOT_INST = "XAUT-USDT"
SWAP_INST = "XAU-USDT-SWAP"

# ── Global collector state ──
_collector_task: Optional[asyncio.Task] = None
_collecting = False
_collect_mode = "unknown"  # "ws" or "rest"
_latest_data: Dict = {
    "spot": None,
    "swap": None,
    "funding": None,
    "updated_at": None,
}
_data_history = []
_MAX_HISTORY = 3600  # 1 hour of 1s data
_sse_clients = []
_db_buffer: List[dict] = []
_DB_FLUSH_INTERVAL = 10  # flush to DB every 10 seconds
_last_db_flush = 0.0

OKX_WS_PUBLIC = "wss://ws.okx.com:8443/ws/v5/public"
OKX_REST_BASE = "https://www.okx.com"


def _make_snapshot():
    """Create a data snapshot from latest data."""
    if not _latest_data["spot"] or not _latest_data["swap"]:
        return None
    return {
        "time": datetime.now(timezone.utc).strftime("%H:%M:%S"),
        "timestamp": time.time(),
        "spot_ask": _latest_data["spot"]["askPx"],
        "spot_bid": _latest_data["spot"]["bidPx"],
        "swap_ask": _latest_data["swap"]["askPx"],
        "swap_bid": _latest_data["swap"]["bidPx"],
        "funding_rate": _latest_data["funding"]["fundingRate"] if _latest_data["funding"] else None,
        "basis": round(_latest_data["swap"]["bidPx"] - _latest_data["spot"]["askPx"], 4),
        "basis_sell": round(_latest_data["swap"]["bidPx"] - _latest_data["spot"]["askPx"], 4),
        "basis_buy": round(_latest_data["swap"]["askPx"] - _latest_data["spot"]["bidPx"], 4),
    }


def _push_snapshot(snapshot):
    """Push snapshot to history, SSE clients, and DB buffer."""
    global _data_history, _db_buffer, _last_db_flush
    _data_history.append(snapshot)
    if len(_data_history) > _MAX_HISTORY:
        _data_history = _data_history[-_MAX_HISTORY:]

    payload = json.dumps(snapshot)
    dead = []
    for q in _sse_clients:
        try:
            q.put_nowait(payload)
        except asyncio.QueueFull:
            dead.append(q)
    for q in dead:
        _sse_clients.remove(q)

    # Buffer for DB write
    _db_buffer.append(snapshot)
    now = time.time()
    if now - _last_db_flush >= _DB_FLUSH_INTERVAL:
        _flush_db_buffer()
        _last_db_flush = now


def _flush_db_buffer():
    """Batch-write buffered snapshots to database."""
    global _db_buffer
    if not _db_buffer:
        return
    batch = _db_buffer[:]
    _db_buffer = []
    try:
        db = SessionLocal()
        rows = [
            OkxXauTick(
                timestamp=s["timestamp"],
                spot_ask=s["spot_ask"],
                spot_bid=s["spot_bid"],
                swap_ask=s["swap_ask"],
                swap_bid=s["swap_bid"],
                basis_sell=s.get("basis_sell", s.get("basis")),
                basis_buy=s.get("basis_buy"),
                funding_rate=s.get("funding_rate"),
            )
            for s in batch
        ]
        db.add_all(rows)
        db.commit()
        db.close()
    except Exception as e:
        print(f"[OKX XAU] DB flush error: {e}")


# ── WebSocket collector ──
async def _ws_collector():
    """Collect via OKX WebSocket. Returns False if connection fails."""
    global _collecting, _latest_data, _collect_mode
    try:
        import websockets
    except ImportError:
        print("[OKX XAU] websockets not installed, skipping WS")
        return False

    try:
        async with websockets.connect(OKX_WS_PUBLIC, ping_interval=20, open_timeout=8) as ws:
            sub_msg = {
                "op": "subscribe",
                "args": [
                    {"channel": "bbo-tbt", "instId": SPOT_INST},
                    {"channel": "bbo-tbt", "instId": SWAP_INST},
                    {"channel": "funding-rate", "instId": SWAP_INST},
                ]
            }
            await ws.send(json.dumps(sub_msg))
            _collect_mode = "ws"
            print(f"[OKX XAU] WebSocket connected to {OKX_WS_PUBLIC}")

            last_snapshot_time = 0

            async for raw_msg in ws:
                if not _collecting:
                    break

                try:
                    msg = json.loads(raw_msg)
                except json.JSONDecodeError:
                    continue

                if "event" in msg:
                    ev = msg.get("event")
                    if ev == "error":
                        print(f"[OKX XAU] WS error: {msg}")
                    continue

                arg = msg.get("arg", {})
                channel = arg.get("channel", "")
                inst_id = arg.get("instId", "")
                data_list = msg.get("data", [])
                if not data_list:
                    continue
                d = data_list[0]

                if channel == "bbo-tbt" and inst_id == SPOT_INST:
                    # bbo-tbt format: asks/bids are arrays: [["price","size","0","count"]]
                    asks = d.get("asks", [])
                    bids = d.get("bids", [])
                    _latest_data["spot"] = {
                        "instId": inst_id,
                        "askPx": float(asks[0][0]) if asks else 0,
                        "askSz": float(asks[0][1]) if asks else 0,
                        "bidPx": float(bids[0][0]) if bids else 0,
                        "bidSz": float(bids[0][1]) if bids else 0,
                        "ts": int(d.get("ts", 0)),
                    }
                elif channel == "bbo-tbt" and inst_id == SWAP_INST:
                    asks = d.get("asks", [])
                    bids = d.get("bids", [])
                    _latest_data["swap"] = {
                        "instId": inst_id,
                        "askPx": float(asks[0][0]) if asks else 0,
                        "askSz": float(asks[0][1]) if asks else 0,
                        "bidPx": float(bids[0][0]) if bids else 0,
                        "bidSz": float(bids[0][1]) if bids else 0,
                        "ts": int(d.get("ts", 0)),
                    }
                elif channel == "funding-rate" and inst_id == SWAP_INST:
                    _latest_data["funding"] = {
                        "instId": inst_id,
                        "fundingRate": float(d.get("fundingRate", 0) or 0),
                        "nextFundingRate": float(d.get("nextFundingRate", 0) or 0),
                        "fundingTime": int(d.get("fundingTime", 0)),
                        "nextFundingTime": int(d.get("nextFundingTime", 0)),
                    }

                now = time.time()
                if now - last_snapshot_time >= 1.0:
                    snapshot = _make_snapshot()
                    if snapshot:
                        last_snapshot_time = now
                        _latest_data["updated_at"] = datetime.now(timezone.utc).isoformat()
                        _push_snapshot(snapshot)

            return True  # normal exit (stopped by user)

    except Exception as e:
        print(f"[OKX XAU] WS failed: {type(e).__name__}: {e}")
        return False


# ── REST API polling collector ──
async def _rest_collector():
    """Fallback: poll OKX REST API every ~1 second."""
    global _collecting, _latest_data, _collect_mode
    _collect_mode = "rest"
    print("[OKX XAU] Using REST API polling fallback (1s interval)")

    async with create_http_client(timeout=10.0, connect_timeout=10.0) as client:
        while _collecting:
            try:
                # Fetch spot + swap tickers in parallel
                spot_req = client.get(
                    f"{OKX_REST_BASE}/api/v5/market/ticker",
                    params={"instId": SPOT_INST},
                )
                swap_req = client.get(
                    f"{OKX_REST_BASE}/api/v5/market/ticker",
                    params={"instId": SWAP_INST},
                )
                spot_resp, swap_resp = await asyncio.gather(spot_req, swap_req)

                spot_data = spot_resp.json()
                swap_data = swap_resp.json()

                if spot_data.get("code") == "0" and spot_data.get("data"):
                    t = spot_data["data"][0]
                    _latest_data["spot"] = {
                        "instId": SPOT_INST,
                        "askPx": float(t.get("askPx", 0) or 0),
                        "askSz": float(t.get("askSz", 0) or 0),
                        "bidPx": float(t.get("bidPx", 0) or 0),
                        "bidSz": float(t.get("bidSz", 0) or 0),
                        "ts": int(t.get("ts", 0)),
                    }

                if swap_data.get("code") == "0" and swap_data.get("data"):
                    t = swap_data["data"][0]
                    _latest_data["swap"] = {
                        "instId": SWAP_INST,
                        "askPx": float(t.get("askPx", 0) or 0),
                        "askSz": float(t.get("askSz", 0) or 0),
                        "bidPx": float(t.get("bidPx", 0) or 0),
                        "bidSz": float(t.get("bidSz", 0) or 0),
                        "ts": int(t.get("ts", 0)),
                    }

                # Funding rate (less frequent, every 10s is fine)
                if not _latest_data["funding"] or time.time() % 10 < 1.5:
                    try:
                        fr_resp = await client.get(
                            f"{OKX_REST_BASE}/api/v5/public/funding-rate",
                            params={"instId": SWAP_INST},
                        )
                        fr_data = fr_resp.json()
                        if fr_data.get("code") == "0" and fr_data.get("data"):
                            f = fr_data["data"][0]
                            _latest_data["funding"] = {
                                "instId": SWAP_INST,
                                "fundingRate": float(f.get("fundingRate", 0) or 0),
                                "nextFundingRate": float(f.get("nextFundingRate", 0) or 0) if f.get("nextFundingRate") else 0,
                                "fundingTime": int(f.get("fundingTime", 0)),
                                "nextFundingTime": int(f.get("nextFundingTime", 0)),
                            }
                    except Exception:
                        pass

                snapshot = _make_snapshot()
                if snapshot:
                    _latest_data["updated_at"] = datetime.now(timezone.utc).isoformat()
                    _push_snapshot(snapshot)

            except Exception as e:
                print(f"[OKX XAU] REST poll error: {e}")

            await asyncio.sleep(1.0)


# ── Main collector: try WS first, fallback to REST ──
async def _run_collector():
    """Main collector loop. Tries WebSocket first, falls back to REST polling."""
    global _collecting, _collect_mode

    while _collecting:
        # Try WebSocket first
        ws_ok = await _ws_collector()
        if not _collecting:
            break
        if ws_ok:
            # WS exited normally (user stopped), don't retry
            break

        # WS failed → fallback to REST
        print("[OKX XAU] WebSocket unavailable, falling back to REST API polling")
        await _rest_collector()

    _collect_mode = "unknown"
    print("[OKX XAU] Collector stopped")


@router.post("/start")
async def start_collection():
    """Start real-time data collection."""
    global _collector_task, _collecting

    if _collecting:
        return {"status": "already_running", "mode": _collect_mode, "records": len(_data_history)}

    _collecting = True
    _collector_task = asyncio.create_task(_run_collector())
    return {"status": "started"}


@router.post("/stop")
async def stop_collection():
    """Stop real-time data collection."""
    global _collector_task, _collecting

    if not _collecting:
        return {"status": "already_stopped"}

    _collecting = False
    if _collector_task:
        _collector_task.cancel()
        try:
            await _collector_task
        except asyncio.CancelledError:
            pass
        _collector_task = None
    # Flush remaining buffer to DB
    _flush_db_buffer()
    return {"status": "stopped", "records": len(_data_history)}


@router.get("/status")
async def get_status():
    """Get current collection status and latest data."""
    return {
        "running": _collecting,
        "mode": _collect_mode,
        "records": len(_data_history),
        "latest": _latest_data,
    }


@router.get("/history")
async def get_history(limit: int = 100):
    """Get recent data history."""
    return {
        "total": len(_data_history),
        "data": _data_history[-limit:],
    }


@router.get("/stream")
async def stream_data():
    """SSE endpoint to stream live data to frontend."""
    queue = asyncio.Queue(maxsize=50)
    _sse_clients.append(queue)

    async def event_generator():
        try:
            while True:
                try:
                    data = await asyncio.wait_for(queue.get(), timeout=30)
                    yield f"data: {data}\n\n"
                except asyncio.TimeoutError:
                    yield f": keepalive\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            if queue in _sse_clients:
                _sse_clients.remove(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/clear")
async def clear_history():
    """Clear collected data history."""
    global _data_history
    _data_history = []
    return {"status": "cleared"}


@router.get("/db-history")
async def get_db_history(
    start_ts: Optional[float] = Query(None, description="Start unix timestamp"),
    end_ts: Optional[float] = Query(None, description="End unix timestamp"),
    limit: int = Query(1000, le=10000),
):
    """Query historical tick data from database."""
    db = SessionLocal()
    try:
        q = db.query(OkxXauTick)
        if start_ts is not None:
            q = q.filter(OkxXauTick.timestamp >= start_ts)
        if end_ts is not None:
            q = q.filter(OkxXauTick.timestamp <= end_ts)
        q = q.order_by(OkxXauTick.timestamp.desc()).limit(limit)
        rows = q.all()
        data = [
            {
                "timestamp": r.timestamp,
                "spot_ask": r.spot_ask,
                "spot_bid": r.spot_bid,
                "swap_ask": r.swap_ask,
                "swap_bid": r.swap_bid,
                "basis_sell": r.basis_sell,
                "basis_buy": r.basis_buy,
                "funding_rate": r.funding_rate,
            }
            for r in reversed(rows)
        ]
        total = db.query(OkxXauTick).count()
        return {"total_in_db": total, "returned": len(data), "data": data}
    finally:
        db.close()


@router.get("/db-stats")
async def get_db_stats():
    """Get database storage statistics."""
    db = SessionLocal()
    try:
        total = db.query(OkxXauTick).count()
        first = db.query(OkxXauTick).order_by(OkxXauTick.timestamp.asc()).first()
        last = db.query(OkxXauTick).order_by(OkxXauTick.timestamp.desc()).first()
        return {
            "total_records": total,
            "first_timestamp": first.timestamp if first else None,
            "last_timestamp": last.timestamp if last else None,
            "first_time": datetime.fromtimestamp(first.timestamp, tz=timezone.utc).isoformat() if first else None,
            "last_time": datetime.fromtimestamp(last.timestamp, tz=timezone.utc).isoformat() if last else None,
        }
    finally:
        db.close()


@router.get("/db-export-csv")
async def export_db_csv():
    """Export all database tick data as a streaming CSV download."""
    def generate():
        db = SessionLocal()
        try:
            yield "timestamp,spot_ask,spot_bid,swap_ask,swap_bid,basis_sell,basis_buy,funding_rate\n"
            batch_size = 5000
            offset = 0
            while True:
                rows = (
                    db.query(OkxXauTick)
                    .order_by(OkxXauTick.timestamp.asc())
                    .offset(offset)
                    .limit(batch_size)
                    .all()
                )
                if not rows:
                    break
                for r in rows:
                    fr = r.funding_rate if r.funding_rate is not None else ""
                    bs = r.basis_sell if r.basis_sell is not None else ""
                    bb = r.basis_buy if r.basis_buy is not None else ""
                    yield f"{r.timestamp},{r.spot_ask},{r.spot_bid},{r.swap_ask},{r.swap_bid},{bs},{bb},{fr}\n"
                offset += batch_size
        finally:
            db.close()

    return StreamingResponse(
        generate(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=okx_xau_all_data.csv"},
    )


@router.post("/arb-backtest")
async def arb_backtest(params: dict):
    """
    Perpetual-spot arbitrage backtest using collected tick data.

    Strategy:
    - Open: short swap + buy spot when basis_sell > open_threshold
    - Close: when basis_buy < close_threshold
    - Funding: accumulated every funding_interval_hours while position is open
    - Fee: taker fee applied on open and close (both legs)

    Params:
      open_threshold: float  - basis to open position (e.g. 8.0)
      close_threshold: float - basis to close position (e.g. 2.0)
      quantity: float         - notional in gold oz (e.g. 1)
      fee_rate: float         - one-way taker fee rate (e.g. 0.0005 = 0.05%)
      funding_interval_hours: float - funding settlement interval (default 8)
    """
    open_thresh = float(params.get("open_threshold", 8.0))
    close_thresh = float(params.get("close_threshold", 2.0))
    quantity = float(params.get("quantity", 1))
    fee_rate = float(params.get("fee_rate", 0.0005))
    funding_interval = float(params.get("funding_interval_hours", 8)) * 3600  # to seconds

    db = SessionLocal()
    try:
        rows = (
            db.query(OkxXauTick)
            .filter(OkxXauTick.spot_ask > 0, OkxXauTick.swap_bid > 0)
            .order_by(OkxXauTick.timestamp.asc())
            .all()
        )
    finally:
        db.close()

    if not rows:
        return {"error": "No data in database"}

    trades = []
    equity_curve = []
    in_position = False
    entry_spot = 0
    entry_swap = 0
    entry_ts = 0
    last_funding_ts = 0
    total_pnl = 0
    total_funding = 0
    total_fees = 0
    cumulative_pnl = 0

    # Sample every 10 seconds for equity curve to keep it manageable
    last_eq_ts = 0

    for r in rows:
        basis_sell = r.basis_sell if r.basis_sell is not None else (r.swap_bid - r.spot_ask)
        basis_buy = r.basis_buy if r.basis_buy is not None else (r.swap_ask - r.spot_bid)
        fr = r.funding_rate if r.funding_rate is not None else 0

        if not in_position:
            # Check open condition: basis wide enough
            if basis_sell > open_thresh:
                in_position = True
                entry_spot = r.spot_ask  # buy spot at ask
                entry_swap = r.swap_bid  # short swap at bid
                entry_ts = r.timestamp
                last_funding_ts = r.timestamp
                # Opening fees: spot buy + swap sell
                open_fee = (entry_spot + entry_swap) * quantity * fee_rate
                total_fees += open_fee
        else:
            # Accumulate funding (short swap receives funding when rate > 0)
            if r.timestamp - last_funding_ts >= funding_interval:
                funding_pnl = fr * entry_swap * quantity
                total_funding += funding_pnl
                last_funding_ts = r.timestamp

            # Check close condition: basis narrowed
            if basis_buy < close_thresh:
                # Close: sell spot at bid, buy swap at ask
                close_spot = r.spot_bid
                close_swap = r.swap_ask
                # PnL from basis convergence
                spot_pnl = (close_spot - entry_spot) * quantity
                swap_pnl = (entry_swap - close_swap) * quantity
                close_fee = (close_spot + close_swap) * quantity * fee_rate
                total_fees += close_fee
                trade_pnl = spot_pnl + swap_pnl
                total_pnl += trade_pnl
                cumulative_pnl = total_pnl + total_funding - total_fees

                trades.append({
                    "open_ts": entry_ts,
                    "close_ts": r.timestamp,
                    "hold_seconds": round(r.timestamp - entry_ts),
                    "entry_basis": round(entry_swap - entry_spot, 4),
                    "exit_basis": round(close_swap - close_spot, 4),
                    "entry_spot": entry_spot,
                    "entry_swap": entry_swap,
                    "close_spot": close_spot,
                    "close_swap": close_swap,
                    "spot_pnl": round(spot_pnl, 4),
                    "swap_pnl": round(swap_pnl, 4),
                    "trade_pnl": round(trade_pnl, 4),
                    "fees": round(open_fee + close_fee, 4),
                    "cumulative_pnl": round(cumulative_pnl, 4),
                })
                in_position = False
                open_fee = 0

        # Equity curve sampling
        if r.timestamp - last_eq_ts >= 10:
            unrealized = 0
            if in_position:
                unrealized = (r.spot_bid - entry_spot + entry_swap - r.swap_ask) * quantity
            eq = total_pnl + total_funding - total_fees + unrealized
            equity_curve.append({
                "timestamp": r.timestamp,
                "equity": round(eq, 4),
                "basis_sell": round(basis_sell, 4),
                "in_position": in_position,
            })
            last_eq_ts = r.timestamp

    # If still in position at end, show unrealized
    unrealized = 0
    if in_position and rows:
        last = rows[-1]
        unrealized = (last.spot_bid - entry_spot + entry_swap - last.swap_ask) * quantity

    return {
        "params": {
            "open_threshold": open_thresh,
            "close_threshold": close_thresh,
            "quantity": quantity,
            "fee_rate": fee_rate,
        },
        "summary": {
            "total_trades": len(trades),
            "total_pnl": round(total_pnl, 4),
            "total_funding": round(total_funding, 4),
            "total_fees": round(total_fees, 4),
            "net_pnl": round(total_pnl + total_funding - total_fees, 4),
            "unrealized": round(unrealized, 4),
            "in_position": in_position,
            "data_points": len(rows),
        },
        "trades": trades,
        "equity_curve": equity_curve[-500:],  # limit for frontend
    }
