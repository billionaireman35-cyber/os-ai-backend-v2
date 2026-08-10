import requests
import logging
from app.core.config import settings

logger = logging.getLogger(__name__)

COINGECKO_API = "https://api.coingecko.com/api/v3"

def get_top_tokens(limit: int = 50, currency: str = "usd"):
    """Fetch top cryptocurrencies by market cap."""
    try:
        url = f"{COINGECKO_API}/coins/markets"
        params = {
            "vs_currency": currency,
            "order": "market_cap_desc",
            "per_page": limit,
            "page": 1,
            "sparkline": "false",
            "price_change_percentage": "24h"
        }
        if settings.COINGECKO_KEY:
            params["x_cg_demo_api_key"] = settings.COINGECKO_KEY
        resp = requests.get(url, params=params, timeout=10)
        if resp.status_code == 200:
            return resp.json()
        else:
            logger.error(f"Coingecko error: {resp.status_code} - {resp.text}")
            return []
    except Exception as e:
        logger.error(f"Coingecko service error: {e}")
        return []

def get_token_price(token_id: str, currency: str = "usd"):
    """Get price for a single token."""
    try:
        url = f"{COINGECKO_API}/simple/price"
        params = {
            "ids": token_id,
            "vs_currencies": currency
        }
        if settings.COINGECKO_KEY:
            params["x_cg_demo_api_key"] = settings.COINGECKO_KEY
        resp = requests.get(url, params=params, timeout=10)
        if resp.status_code == 200:
            return resp.json()
        else:
            return {}
    except Exception as e:
        logger.error(f"Price fetch error: {e}")
        return {}
