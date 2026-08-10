import hmac
import hashlib
import time
import urllib.parse
from app.core.config import settings
import logging

logger = logging.getLogger(__name__)

MOONPAY_API_BASE = "https://api.moonpay.io"
MOONPAY_WIDGET_BASE = "https://buy.moonpay.com"

def generate_buy_url(
    wallet_address: str,
    currency_code: str = "eth",
    fiat_currency: str = "usd",
    fiat_amount: float = 50.0,
    redirect_url: str = None,
) -> str:
    """Generate a signed MoonPay buy URL."""
    # Build payload
    payload = {
        "apiKey": settings.MOONPAY_PUBLIC_KEY,
        "currencyCode": currency_code,
        "walletAddress": wallet_address,
        "fiatCurrency": fiat_currency,
        "fiatAmount": fiat_amount,
        "redirectUrl": redirect_url or settings.FRONTEND_URL,
    }
    # Sort keys for signing
    sorted_keys = sorted(payload.keys())
    query_parts = []
    for key in sorted_keys:
        query_parts.append(f"{key}={urllib.parse.quote(str(payload[key]))}")
    query_string = "&".join(query_parts)
    # Sign
    signature = hmac.new(
        settings.MOONPAY_SECRET_KEY.encode(),
        query_string.encode(),
        hashlib.sha256
    ).hexdigest()
    full_url = f"{MOONPAY_WIDGET_BASE}?{query_string}&signature={signature}"
    return full_url

def generate_sell_url(
    wallet_address: str,
    currency_code: str = "eth",
    fiat_currency: str = "usd",
    crypto_amount: float = None,
    redirect_url: str = None,
) -> str:
    """Generate a signed MoonPay sell URL."""
    payload = {
        "apiKey": settings.MOONPAY_PUBLIC_KEY,
        "currencyCode": currency_code,
        "walletAddress": wallet_address,
        "fiatCurrency": fiat_currency,
        "redirectUrl": redirect_url or settings.FRONTEND_URL,
    }
    if crypto_amount:
        payload["cryptoAmount"] = crypto_amount
    sorted_keys = sorted(payload.keys())
    query_parts = []
    for key in sorted_keys:
        query_parts.append(f"{key}={urllib.parse.quote(str(payload[key]))}")
    query_string = "&".join(query_parts)
    signature = hmac.new(
        settings.MOONPAY_SECRET_KEY.encode(),
        query_string.encode(),
        hashlib.sha256
    ).hexdigest()
    full_url = f"{MOONPAY_WIDGET_BASE}/sell?{query_string}&signature={signature}"
    return full_url
