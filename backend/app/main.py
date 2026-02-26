"""FastAPI application entry point."""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.core.config import settings
from app.core.database import init_db
from app.api import positions, market_prices, volatility, pnl, portfolio, iv, hedge, okx, backtest, deribit, deribit_debug, okx_xau, leaps, leaps_ultimate, leaps_ultimate_v2, us_leaps, data_center, qqq_leaps, hf_collector
from app.models import DeribitPriceCache, DeribitIVCache, OkxXauTick, OkxPriceCache, DataCollectionLog, HFOptionTick  # ensure tables are registered

# Create FastAPI application
app = FastAPI(
    title="Options Risk Monitor API",
    description="API for monitoring options portfolio risk",
    version="1.0.0"
)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all origins for development
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)

# Register routers
app.include_router(portfolio.router)
app.include_router(positions.router)
app.include_router(market_prices.router)
app.include_router(volatility.router)
app.include_router(pnl.router)
app.include_router(iv.router)
app.include_router(hedge.router)
app.include_router(okx.router)
app.include_router(backtest.router)
app.include_router(deribit.router)
app.include_router(deribit_debug.router)
app.include_router(okx_xau.router)
app.include_router(leaps.router)
app.include_router(leaps_ultimate.router)
app.include_router(leaps_ultimate_v2.router)
app.include_router(us_leaps.router)
app.include_router(data_center.router)
app.include_router(qqq_leaps.router)
app.include_router(hf_collector.router)


@app.on_event("startup")
async def startup_event():
    """Initialize database on startup."""
    init_db()
    # Auto-start HF collector (BTC, every 60s)
    from app.api.hf_collector import start_collector, CollectorConfig
    try:
        await start_collector(CollectorConfig(underlying="BTC", interval_sec=60))
        print("[Startup] HF collector auto-started (BTC, 60s)")
    except Exception as e:
        print(f"[Startup] HF collector auto-start failed: {e}")


@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "message": "Options Risk Monitor API",
        "version": "1.0.0",
        "status": "running"
    }


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy"}
