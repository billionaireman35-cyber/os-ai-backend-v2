import requests
import logging
from app.core.config import settings

logger = logging.getLogger(__name__)

COINGECKO_API = "https://api.coingecko.com/api/v3"
COINGECKO_ONCHAIN_API = "https://api.coingecko.com/api/v3/onchain"

CLOSE_TOKEN_ADDRESS = "0x3c6833cFDdED80fE76474a3Cb2Cc050Daec91fe8"
CLOSE_TOKEN_NETWORK = "polygon_pos"


def _cg_params(extra=None):
    """Shared helper: build query params with the demo API key attached,
    if one is configured."""
    params = dict(extra or {})
    if settings.COINGECKO_KEY:
        params["x_cg_demo_api_key"] = settings.COINGECKO_KEY
    return params


def get_top_tokens(limit: int = 50, currency: str = "usd"):
    """Fetch top cryptocurrencies by market cap, including 7-day sparkline
    data (used for the mini price-trend chart in Pulse)."""
    try:
        url = f"{COINGECKO_API}/coins/markets"
        params = _cg_params({
            "vs_currency": currency,
            "order": "market_cap_desc",
            "per_page": limit,
            "page": 1,
            "sparkline": "true",
            "price_change_percentage": "24h"
        })
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
        params = _cg_params({
            "ids": token_id,
            "vs_currencies": currency
        })
        resp = requests.get(url, params=params, timeout=10)
        if resp.status_code == 200:
            return resp.json()
        else:
            return {}
    except Exception as e:
        logger.error(f"Price fetch error: {e}")
        return {}


def get_token_detail(token_id: str):
    """Fetch full detail for a single CoinGecko-listed token (description,
    links, market data, ATH/ATL, etc.) - used when a user taps a token in
    Pulse. Returns None on any failure so the caller can show a clean
    'not found' state rather than a broken partial object."""
    try:
        url = f"{COINGECKO_API}/coins/{token_id}"
        params = _cg_params({
            "localization": "false",
            "tickers": "false",
            "market_data": "true",
            "community_data": "false",
            "developer_data": "false",
            "sparkline": "true",
        })
        resp = requests.get(url, params=params, timeout=10)
        if resp.status_code == 200:
            return resp.json()
        logger.error(f"Coingecko detail error for {token_id}: {resp.status_code} - {resp.text}")
        return None
    except Exception as e:
        logger.error(f"Coingecko detail service error: {e}")
        return None


def get_close_token_data():
    """Fetch live price/liquidity data for the CLOSE token directly from
    on-chain DEX pools via CoinGecko's on-chain API, since CLOSE is not a
    CoinGecko-listed asset and won't appear in get_top_tokens(). Returns
    None on failure - caller should treat a missing CLOSE entry as
    'temporarily unavailable', not as an error to surface to the user.
    """
    try:
        url = f"{COINGECKO_ONCHAIN_API}/networks/{CLOSE_TOKEN_NETWORK}/tokens/{CLOSE_TOKEN_ADDRESS}"
        params = _cg_params()
        resp = requests.get(url, params=params, timeout=10)
        if resp.status_code != 200:
            logger.error(f"Coingecko onchain error for CLOSE: {resp.status_code} - {resp.text}")
            return None

        attrs = resp.json().get("data", {}).get("attributes", {})
        if not attrs:
            return None

        # Normalize to the same shape as get_top_tokens() entries so the
        # frontend can render CLOSE alongside regular tokens without a
        # special case. No sparkline_in_7d - the on-chain endpoint doesn't
        # provide one, so the frontend must handle a token with no chart.
        price = attrs.get("price_usd")
        change_24h = attrs.get("price_change_percentage", {}).get("h24")
        return {
            "id": "close-token",
            "symbol": attrs.get("symbol", "CLOSE").lower(),
            "name": attrs.get("name", "CLOSE"),
            "image": None,
            "current_price": float(price) if price is not None else None,
            "price_change_percentage_24h": float(change_24h) if change_24h is not None else None,
            "sparkline_in_7d": None,
            "market_cap": None,
            "is_pinned": True,
        }
    except Exception as e:
        logger.error(f"Coingecko onchain service error: {e}")
        return None


def get_market_aggregate():
    """Fetch true market-wide stats for the Market Pulse strip: total
    market cap, its 24h change, and global gainers/losers counts.
    Uses CoinGecko's /global endpoint for cap totals, and the existing
    top-tokens list (already fetched with 24h change data) to count
    movers - counting movers across the full market would need a much
    larger, paginated pull, so this scopes 'movers' to the same top-N
    universe already shown in the feed rather than the entire market.
    Returns None on failure so the caller can hide the strip cleanly
    instead of showing broken/zeroed numbers.
    """
    try:
        url = f"{COINGECKO_API}/global"
        params = _cg_params()
        resp = requests.get(url, params=params, timeout=10)
        if resp.status_code != 200:
            logger.error(f"Coingecko global error: {resp.status_code} - {resp.text}")
            return None

        data = resp.json().get("data", {})
        market_cap_change_pct = data.get("market_cap_change_percentage_24h_usd")

        top = get_top_tokens(limit=100)
        gainers = sum(1 for t in top if (t.get("price_change_percentage_24h") or 0) > 0)
        losers = sum(1 for t in top if (t.get("price_change_percentage_24h") or 0) < 0)
        total = gainers + losers

        sentiment_pct = round((gainers / total) * 100, 1) if total else None

        return {
            "market_cap_change_percentage_24h": market_cap_change_pct,
            "gainers": gainers,
            "losers": losers,
            "sentiment_pct": sentiment_pct,
        }
    except Exception as e:
        logger.error(f"Coingecko global service error: {e}")
        return None
