import requests
from fastapi import APIRouter, Depends, HTTPException, Body
from app.core.config import settings
from app.core.security import get_current_user
from app.core.database import get_db
import logging

router = APIRouter()
logger = logging.getLogger(__name__)

# KyberSwap Aggregator - free, keyless (X-Client-Id is a self-chosen label,
# not a secret). See docs.kyberswap.com/developer-guide/aggregator-api.
# NOTE: KyberSwap's docs mention a newer gated gateway
# (api.kyberswap.com/swap/, requires a requested API key) that this legacy
# free endpoint may eventually be migrated/deprecated toward. If this ever
# stops working, that's the first thing to check.
KYBERSWAP_API_BASE = "https://aggregator-api.kyberswap.com"
KYBERSWAP_CLIENT_ID = "OS-AI"


@router.get("/quote")
async def get_swap_quote(
    chain: str,
    fromTokenAddress: str,
    toTokenAddress: str,
    amount: str,
    user=Depends(get_current_user)
):
    """
    Returns a route preview - price/output estimate only, no calldata yet.
    Matches KyberSwap's [V1] Get Swap Route.
    """
    if not user:
        raise HTTPException(401, "Authentication required")
    if chain not in settings.SUPPORTED_CHAINS:
        raise HTTPException(400, f"Unsupported chain: {chain}")

    url = f"{KYBERSWAP_API_BASE}/{chain}/api/v1/routes"
    params = {
        "tokenIn": fromTokenAddress,
        "tokenOut": toTokenAddress,
        "amountIn": amount,
    }
    headers = {"X-Client-Id": KYBERSWAP_CLIENT_ID}
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=10)
        if resp.status_code != 200:
            logger.error(f"KyberSwap route error: {resp.text}")
            raise HTTPException(400, f"Failed to get quote: {resp.text}")
        data = resp.json()
        route_summary = data.get("data", {}).get("routeSummary")
        if not route_summary:
            raise HTTPException(400, "No route found for this pair")
        return {
            "routeSummary": route_summary,
            "routerAddress": data["data"]["routerAddress"],
            "amountOut": route_summary.get("amountOut"),
            "amountOutUsd": route_summary.get("amountOutUsd"),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"KyberSwap route exception: {e}")
        raise HTTPException(500, "Internal server error")


@router.post("/swap")
async def get_swap_calldata(
    chain: str = Body(...),
    routeSummary: dict = Body(..., description="The routeSummary object exactly as returned by /quote"),
    fromAddress: str = Body(...),
    slippageBps: int = Body(50, description="Slippage tolerance in bps, e.g. 50 = 0.5%"),
    user=Depends(get_current_user)
):
    """
    Encodes the swap into ready-to-sign calldata. Does NOT sign or
    broadcast anything - returns {to, data, value} for the frontend to sign
    with the user's own wallet (via /wallet/send-style signing), matching
    the non-custodial pattern used everywhere else in this app. Matches
    KyberSwap's [V1] Post Swap Route For Encoded Data.
    """
    if not user:
        raise HTTPException(401, "Authentication required")
    if chain not in settings.SUPPORTED_CHAINS:
        raise HTTPException(400, f"Unsupported chain: {chain}")
    if not fromAddress:
        raise HTTPException(400, "fromAddress is required")

    url = f"{KYBERSWAP_API_BASE}/{chain}/api/v1/route/build"
    headers = {"X-Client-Id": KYBERSWAP_CLIENT_ID, "Content-Type": "application/json"}
    body = {
        "routeSummary": routeSummary,
        "sender": fromAddress,
        "recipient": fromAddress,
        "slippageTolerance": slippageBps,
    }
    try:
        resp = requests.post(url, headers=headers, json=body, timeout=15)
        if resp.status_code != 200:
            logger.error(f"KyberSwap build error: {resp.text}")
            raise HTTPException(400, f"Swap preparation failed: {resp.text}")
        data = resp.json()["data"]
        return {
            "to": data["routerAddress"],
            "data": data["data"],
            "value": data.get("transactionValue", "0"),
            "amountOut": data.get("amountOut"),
            "amountOutUsd": data.get("amountOutUsd"),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"KyberSwap build exception: {e}")
        raise HTTPException(500, "Internal server error")
