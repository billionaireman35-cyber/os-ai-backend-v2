from fastapi import APIRouter, Request
import requests
import time
from app.core.config import settings
import logging

router = APIRouter()
logger = logging.getLogger(__name__)

# Simple in-memory cache for prices
_price_cache = {}
_price_cache_time = 0
CACHE_TTL = 60  # seconds

# Token mapping: symbol -> CoinGecko ID (or custom)
TOKEN_IDS = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "POL": "matic-network",
    "USDC": "usd-coin",
    "USDT": "tether",
    "DAI": "dai",
    "CLOSE": "close-token",  # if listed, otherwise we'll use a fallback
    "OSINA": "osina",        # if listed
}

def get_prices() -> dict:
    """Fetch current USD prices for all supported tokens."""
    global _price_cache, _price_cache_time
    now = time.time()
    if _price_cache and (now - _price_cache_time) < CACHE_TTL:
        return _price_cache

    prices = {}
    # Use CoinGecko
    if settings.COINGECKO_KEY:
        try:
            ids = ",".join(TOKEN_IDS.values())
            url = "https://api.coingecko.com/api/v3/simple/price"
            resp = requests.get(
                url,
                params={"ids": ids, "vs_currencies": "usd"},
                headers={"x-cg-demo-api-key": settings.COINGECKO_KEY},
                timeout=10
            )
            if resp.status_code == 200:
                data = resp.json()
                for symbol, cg_id in TOKEN_IDS.items():
                    if cg_id in data:
                        prices[symbol] = data[cg_id].get("usd", 0.0)
                _price_cache = prices
                _price_cache_time = now
                return prices
        except Exception as e:
            logger.error(f"CoinGecko price fetch error: {e}")

    # Fallback: hardcoded (if API fails)
    fallback = {
        "BTC": 60000,
        "ETH": 3000,
        "POL": 0.5,
        "USDC": 1.0,
        "USDT": 1.0,
        "DAI": 1.0,
        "CLOSE": 0.00009776,  # from settings
        "OSINA": 0.01,
    }
    prices.update(fallback)
    _price_cache = prices
    _price_cache_time = now
    return prices

@router.get("/prices")
async def get_price_endpoint():
    return get_prices()
