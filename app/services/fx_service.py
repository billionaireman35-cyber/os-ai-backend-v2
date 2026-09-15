import logging
import time

import requests

logger = logging.getLogger(__name__)

FX_API_BASE = "https://api.frankfurter.dev/v2"
CACHE_TTL_SECONDS = 300

_rates_cache = {
    "data": None,
    "fetched_at": 0,
}


def _fetch_latest_rates():
    now = time.time()

    if (
        _rates_cache["data"] is not None
        and (now - _rates_cache["fetched_at"]) < CACHE_TTL_SECONDS
    ):
        return _rates_cache["data"], _rates_cache["fetched_at"]

    try:
        resp = requests.get(
            f"{FX_API_BASE}/rates",
            params={"base": "USD"},
            timeout=10,
        )
        resp.raise_for_status()

        rows = resp.json()
        if not isinstance(rows, list) or not rows:
            raise ValueError("Frankfurter returned no FX rates")

        rates = {}
        rate_dates = {}

        for row in rows:
            if not isinstance(row, dict):
                continue
            currency = str(row.get("quote", "")).upper()
            value = row.get("rate")
            if currency and value is not None:
                rates[currency] = float(value)
                rate_dates[currency] = row.get("date")

        if not rates:
            raise ValueError("Frankfurter returned no usable FX rates")

        _rates_cache["data"] = {
            "rates": rates,
            "dates": rate_dates,
        }
        _rates_cache["fetched_at"] = now

        return _rates_cache["data"], now

    except Exception as e:
        logger.error(f"FX latest-rate fetch failed: {e}")

        raise RuntimeError("Live FX rates are currently unavailable") from e


def get_fx_rate(target_currency: str) -> float:
    """Return USD -> target currency for existing wallet valuation."""
    target_currency = (target_currency or "USD").upper().strip()

    if target_currency == "USD":
        return 1.0

    try:
        data, _ = _fetch_latest_rates()
    except RuntimeError:
        return 1.0

    rate = data["rates"].get(target_currency)

    if rate is None:
        logger.warning(
            f"No FX rate found for {target_currency}; "
            "legacy wallet valuation fallback=1.0"
        )
        return 1.0

    return float(rate)


def get_commercial_fx_quote(target_currency: str) -> dict:
    """Return a fail-closed FX quote for Goldx commercial pricing."""
    target_currency = (target_currency or "USD").upper().strip()

    if target_currency == "USD":
        return {
            "currency": "USD",
            "rate": 1.0,
            "source": "USD",
            "rate_timestamp": None,
            "stale": False,
            "age_seconds": None,
        }

    data, fetched_at = _fetch_latest_rates()
    rate = data["rates"].get(target_currency)

    if rate is None or rate <= 0:
        raise ValueError(
            f"Live FX rate unavailable for {target_currency}"
        )

    rate_date = data["dates"].get(target_currency)

    return {
        "currency": target_currency,
        "rate": float(rate),
        "source": "frankfurter.dev",
        "rate_timestamp": (
            f"{rate_date}T00:00:00Z" if rate_date else None
        ),
        "stale": False,
        "age_seconds": max(0, int(time.time() - fetched_at)),
    }


def get_supported_fx_currencies():
    """Return currencies currently supplied by Frankfurter."""
    try:
        data, _ = _fetch_latest_rates()
    except RuntimeError:
        return []

    return sorted(data["rates"].keys())
