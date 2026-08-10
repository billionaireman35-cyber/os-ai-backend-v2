import requests
import json
from fastapi import APIRouter, Depends, HTTPException, Body
from app.core.config import settings
from app.core.security import get_current_user
from app.core.database import get_db
import logging

router = APIRouter()
logger = logging.getLogger(__name__)

ONEINCH_API_BASE = "https://api.1inch.dev/swap/v5.2"

def _get_headers():
    return {
        "Authorization": f"Bearer {settings.ONEINCH_API_KEY}",
        "Accept": "application/json"
    }

@router.get("/quote")
async def get_swap_quote(
    chain: str,
    fromTokenAddress: str,
    toTokenAddress: str,
    amount: str,
    user=Depends(get_current_user)
):
    if not user:
        raise HTTPException(401, "Authentication required")
    if chain not in settings.SUPPORTED_CHAINS:
        raise HTTPException(400, f"Unsupported chain: {chain}")
    url = f"{ONEINCH_API_BASE}/{chain}/quote"
    params = {
        "fromTokenAddress": fromTokenAddress,
        "toTokenAddress": toTokenAddress,
        "amount": amount,
    }
    try:
        resp = requests.get(url, headers=_get_headers(), params=params, timeout=10)
        if resp.status_code != 200:
            logger.error(f"1inch quote error: {resp.text}")
            raise HTTPException(400, f"Failed to get quote: {resp.text}")
        return resp.json()
    except Exception as e:
        logger.error(f"1inch quote exception: {e}")
        raise HTTPException(500, "Internal server error")

@router.post("/swap")
async def execute_swap(
    chain: str = Body(...),
    fromTokenAddress: str = Body(...),
    toTokenAddress: str = Body(...),
    amount: str = Body(...),
    fromAddress: str = Body(...),
    slippage: float = Body(1.0),
    user=Depends(get_current_user)
):
    if not user:
        raise HTTPException(401, "Authentication required")
    if chain not in settings.SUPPORTED_CHAINS:
        raise HTTPException(400, f"Unsupported chain: {chain}")

    url = f"{ONEINCH_API_BASE}/{chain}/swap"
    params = {
        "fromTokenAddress": fromTokenAddress,
        "toTokenAddress": toTokenAddress,
        "amount": amount,
        "fromAddress": fromAddress,
        "slippage": slippage,
        "disableEstimate": "false",
    }
    try:
        resp = requests.get(url, headers=_get_headers(), params=params, timeout=15)
        if resp.status_code != 200:
            logger.error(f"1inch swap error: {resp.text}")
            raise HTTPException(400, f"Swap preparation failed: {resp.text}")
        return resp.json()
    except Exception as e:
        logger.error(f"1inch swap exception: {e}")
        raise HTTPException(500, "Internal server error")
