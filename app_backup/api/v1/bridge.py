from fastapi import APIRouter, Depends, HTTPException
from app.core.security import get_current_user
from app.core.config import settings
import requests
import logging

router = APIRouter()
logger = logging.getLogger(__name__)

# Socket API base URL (free tier)
SOCKET_API_URL = "https://api.socket.tech/v2"

@router.post("/quote")
async def bridge_quote(req: dict, user=Depends(get_current_user)):
    if not user:
        raise HTTPException(401, "Authentication required")
    from_chain = req.get("from_chain")
    to_chain = req.get("to_chain")
    from_token = req.get("from_token")
    to_token = req.get("to_token")
    amount = req.get("amount")  # in wei
    if not from_chain or not to_chain or not from_token or not to_token or not amount:
        raise HTTPException(400, "Missing required fields")

    # Map chain names to Socket chain IDs
    chain_ids = {
        "polygon": 137,
        "ethereum": 1,
        "bsc": 56,
        "arbitrum": 42161,
        "base": 8453,
    }
    from_id = chain_ids.get(from_chain)
    to_id = chain_ids.get(to_chain)
    if not from_id or not to_id:
        raise HTTPException(400, "Unsupported chain")

    try:
        resp = requests.get(
            f"{SOCKET_API_URL}/quote",
            params={
                "fromChainId": from_id,
                "toChainId": to_id,
                "fromTokenAddress": from_token,
                "toTokenAddress": to_token,
                "amount": amount,
                "fromAddress": user.get("wallet_address"),
                "slippage": 0.5,
            },
            timeout=15
        )
        if resp.status_code != 200:
            logger.error(f"Socket quote error: {resp.text}")
            raise HTTPException(500, "Failed to get bridge quote")
        quote = resp.json()
        # Add our service fee (0.3%)
        fee_percent = settings.BRIDGE_FEE_PERCENT / 100
        output_amount = int(quote.get("toAmount", 0))
        fee_amount = int(output_amount * fee_percent) if output_amount else 0
        adjusted_output = output_amount - fee_amount
        quote["toAmount"] = str(adjusted_output)
        quote["fee"] = fee_amount
        return quote
    except Exception as e:
        logger.error(f"Bridge quote error: {e}")
        raise HTTPException(500, "Bridge quote failed")

@router.post("/build")
async def build_bridge(req: dict, user=Depends(get_current_user)):
    if not user:
        raise HTTPException(401, "Authentication required")
    # Build transaction from Socket
    # We'll use the quote from the frontend
    quote = req.get("quote")
    from_address = user.get("wallet_address")
    if not quote or not from_address:
        raise HTTPException(400, "Missing quote or address")

    try:
        # Socket's build endpoint
        resp = requests.post(
            f"{SOCKET_API_URL}/build",
            json={
                "quote": quote,
                "fromAddress": from_address,
                "slippage": 0.5,
            },
            timeout=15
        )
        if resp.status_code != 200:
            logger.error(f"Socket build error: {resp.text}")
            raise HTTPException(500, "Failed to build bridge transaction")
        return resp.json()
    except Exception as e:
        logger.error(f"Bridge build error: {e}")
        raise HTTPException(500, "Bridge build failed")
