"""OKX XAU tick data model for persistent storage."""
from sqlalchemy import Column, Integer, Float, Index
from app.core.database import Base


class OkxXauTick(Base):
    """1-second tick data for XAUT-USDT spot and XAU-USDT-SWAP perpetual."""
    __tablename__ = "okx_xau_ticks"

    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(Float, nullable=False)  # unix timestamp
    spot_ask = Column(Float, nullable=False)
    spot_bid = Column(Float, nullable=False)
    swap_ask = Column(Float, nullable=False)
    swap_bid = Column(Float, nullable=False)
    basis_sell = Column(Float)  # swap_bid - spot_ask
    basis_buy = Column(Float)   # swap_ask - spot_bid
    funding_rate = Column(Float)

    __table_args__ = (
        Index('ix_okx_xau_ts', 'timestamp'),
    )
