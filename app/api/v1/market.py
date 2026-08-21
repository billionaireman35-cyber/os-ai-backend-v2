from fastapi import APIRouter, Depends, HTTPException, Query
from app.core.security import get_current_user
from app.services.coingecko_service import (
    get_top_tokens, get_token_price, get_token_detail,
    get_close_token_data, get_market_aggregate
)
from app.services.news_service import get_crypto_news
import logging

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/top-tokens")
async def top_tokens(
    limit: int = Query(20, ge=1, le=100),
    currency: str = "usd",
    user=Depends(get_current_user)
):
    """Get top tokens by market cap, with CLOSE (our own token) pinned to
    the front of the list. CLOSE isn't CoinGecko-listed, so it's fetched
    separately from on-chain DEX data and merged in here - if that fetch
    fails, we still return the regular top-tokens list rather than error
    the whole endpoint."""
    if not user:
        raise HTTPException(401, "Authentication required")
    data = get_top_tokens(limit, currency)

    close_data = get_close_token_data()
    if close_data:
        data = [close_data] + data

    return {"tokens": data}


@router.get("/price")
async def token_price(
    token_id: str,
    currency: str = "usd",
    user=Depends(get_current_user)
):
    """Get price for a specific token."""
    if not user:
        raise HTTPException(401, "Authentication required")
    data = get_token_price(token_id, currency)
    return {"price": data}


@router.get("/token/{token_id}")
async def token_detail(
    token_id: str,
    user=Depends(get_current_user)
):
    """Get full detail for a single token - shown when a user taps a
    token in Pulse. 404s cleanly if CoinGecko doesn't recognize the id
    (e.g. someone taps the pinned CLOSE row, which uses a synthetic id
    not present on CoinGecko)."""
    if not user:
        raise HTTPException(401, "Authentication required")
    if token_id == "close-token":
        raise HTTPException(404, "Detail view isn't available for CLOSE yet")
    data = get_token_detail(token_id)
    if not data:
        raise HTTPException(404, "Token not found")
    return data


@router.get("/aggregate")
async def market_aggregate(user=Depends(get_current_user)):
    """Market-wide stats for the Market Pulse strip (sentiment, 24h cap
    change, gainers/losers). Returns 200 with aggregate: null on failure
    rather than a 500/404, so the frontend can hide the strip without
    treating it as a hard error."""
    if not user:
        raise HTTPException(401, "Authentication required")
    data = get_market_aggregate()
    return {"aggregate": data}


@router.get("/news")
async def news(
    query: str = "crypto",
    limit: int = Query(10, ge=1, le=20),
    user=Depends(get_current_user)
):
    """Get crypto news."""
    if not user:
        raise HTTPException(401, "Authentication required")
    data = get_crypto_news(query, limit)
    return {"news": data}
