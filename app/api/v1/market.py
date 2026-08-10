from fastapi import APIRouter, Depends, HTTPException, Query
from app.core.security import get_current_user
from app.services.coingecko_service import get_top_tokens, get_token_price
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
    """Get top tokens by market cap."""
    if not user:
        raise HTTPException(401, "Authentication required")
    data = get_top_tokens(limit, currency)
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
