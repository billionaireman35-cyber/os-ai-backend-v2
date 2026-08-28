import requests
import logging
import time
from app.core.config import settings

logger = logging.getLogger(__name__)

FX_API_BASE = "https://api.fxapi.com/v1"

# Cached in-process. Free-tier fxapi.com is rate-limited (10 req/min,
# monthly quota) and FX rates don't move fast enough to need per-request
# freshness, so we fetch the full rate table (USD-based) at most once per
# CACHE_TTL_SECONDS and serve individual currency lookups from it.
CACHE_TTL_SECONDS = 3600
_rates_cache = {"data": None, "fetched_at": 0}


def _fetch_latest_rates():
    """Fetch the full USD-based rate table from fxapi.com, using the
    in-process cache when fresh."""
    now = time.time()
    if _rates_cache["data"] is not None and (now - _rates_cache["fetched_at"]) < CACHE_TTL_SECONDS:
        return _rates_cache["data"]
    try:
        url = f"{FX_API_BASE}/latest"
        headers = {"apikey": settings.FX_API_KEY}
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code == 200:
            data = resp.json().get("data", {})
            _rates_cache["data"] = data
            _rates_cache["fetched_at"] = now
            return data
        else:
            logger.error(f"fxapi.com latest error: {resp.status_code} - {resp.text}")
            return _rates_cache["data"] or {}
    except Exception as e:
        logger.error(f"fxapi.com latest exception: {e}")
        return _rates_cache["data"] or {}


def get_fx_rate(target_currency: str) -> float:
    """Returns the USD -> target_currency rate. Returns 1.0 for USD or
    for any currency fxapi doesn't recognize (safe fallback - displays
    as USD rather than showing a broken/zeroed-out value)."""
    target_currency = (target_currency or "USD").upper()
    if target_currency == "USD":
        return 1.0
    rates = _fetch_latest_rates()
    entry = rates.get(target_currency)
    if not entry:
        logger.warning(f"No fx rate found for {target_currency}, falling back to USD (1.0)")
        return 1.0
    return entry.get("value", 1.0)


def get_supported_fx_currencies():
    """Currency codes fxapi.com can convert USD into, derived from the
    same cached rate table (its keys are exactly the convertible
    currencies)."""
    rates = _fetch_latest_rates()
    return sorted(rates.keys())
