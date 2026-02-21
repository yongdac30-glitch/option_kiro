"""Application configuration."""
from pydantic_settings import BaseSettings
from typing import List, Optional
import os
import httpx


class Settings(BaseSettings):
    """Application settings."""
    
    # Database - use absolute path based on project root
    DATABASE_URL: str = f"sqlite:///{os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'options_monitor.db')}"
    
    # API
    API_HOST: str = "0.0.0.0"
    API_PORT: int = 8000
    API_RELOAD: bool = True
    
    # CORS
    CORS_ORIGINS: List[str] = ["http://localhost:3000", "http://localhost:5173", "http://localhost:5174"]
    
    # Financial
    RISK_FREE_RATE: float = 0.05
    
    # Proxy (v2rayN etc.)
    PROXY_URL: str = ""  # e.g. socks5://127.0.0.1:10808 or http://127.0.0.1:10809
    
    class Config:
        env_file = ".env"
        case_sensitive = True


settings = Settings()

# ── Runtime proxy state (can be toggled via API without restart) ──
_proxy_enabled: bool = False
_proxy_url: str = settings.PROXY_URL or "socks5://127.0.0.1:10808"


def set_proxy(enabled: bool, url: Optional[str] = None):
    global _proxy_enabled, _proxy_url
    _proxy_enabled = enabled
    if url:
        _proxy_url = url
    # Also update env vars for libraries that read proxy from env (yfinance/requests)
    apply_proxy_env()


def get_proxy_config() -> dict:
    return {"enabled": _proxy_enabled, "url": _proxy_url}


def create_http_client(
    timeout: float = 30.0,
    connect_timeout: float = 10.0,
    use_proxy: Optional[bool] = None,
) -> httpx.AsyncClient:
    """Create an httpx.AsyncClient with optional proxy support.
    
    Args:
        timeout: Total request timeout in seconds
        connect_timeout: Connection timeout in seconds
        use_proxy: Override proxy setting. None = use global setting.
    """
    should_proxy = use_proxy if use_proxy is not None else _proxy_enabled
    
    kwargs = {
        "timeout": httpx.Timeout(timeout, connect=connect_timeout),
    }
    
    if should_proxy and _proxy_url:
        kwargs["proxy"] = _proxy_url
    
    return httpx.AsyncClient(**kwargs)


def apply_proxy_env():
    """Set HTTP_PROXY/HTTPS_PROXY env vars based on current proxy state.
    
    Call this before using libraries that read proxy from env (e.g. yfinance/requests).
    """
    if _proxy_enabled and _proxy_url:
        os.environ["HTTP_PROXY"] = _proxy_url
        os.environ["HTTPS_PROXY"] = _proxy_url
    else:
        os.environ.pop("HTTP_PROXY", None)
        os.environ.pop("HTTPS_PROXY", None)
