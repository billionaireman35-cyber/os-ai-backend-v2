from fastapi import APIRouter, Depends, HTTPException, Request
from app.models.schemas import SwapQuoteRequest
from app.core.security import get_current_user
from app.core.config import settings
import requests
import logging

router = APIRouter()
logger = logging.getLogger(__name__)

@router.post("/quote")
async def get_swap_quote(req: SwapQuoteRequest, request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(401, "Authentication required")
    
    chain_id = settings.ONEINCH_CHAIN_IDS.get(req.chain)
    if not chain_id:
        raise HTTPException(400, f"Unsupported chain: {req.chain}")
    
    # Use 1inch Business API
    url = f"{settings.ONEINCH_BASE_URL}/{chain_id}/quote"
    params = {
        "fromTokenAddress": req.from_token,
        "toTokenAddress": req.to_token,
        "amount": str(int(req.amount * 10**18)),  # assuming 18 decimals
        "slippage": req.slippage,
    }
    headers = {"Authorization": f"Bearer {settings.ONEINCH_API_KEY}"}
    
    try:
        resp = requests.get(url, params=params, headers=headers, timeout=15)
        if resp.status_code != 200:
            logger.error(f"1inch Business API quote error: {resp.text}")
            raise HTTPException(500, "Failed to get quote from 1inch")
        quote = resp.json()
        
        # Calculate our fee (0.75%)
        fee_percent = settings.SWAP_FEE_PERCENT / 100
        output_amount = int(quote.get("toTokenAmount", 0))
        fee_amount = int(output_amount * fee_percent) if output_amount else 0
        adjusted_output = output_amount - fee_amount
        
        return {
            "from_token": req.from_token,
            "to_token": req.to_token,
            "amount_in": req.amount,
            "amount_out": adjusted_output / 10**18,  # convert back
            "fee_usd": quote.get("estimatedGas", 0) * 0.001,  # approximate fee in USD (we can improve later)
            "fee_percent": settings.SWAP_FEE_PERCENT,
            "slippage": req.slippage,
            "quote": quote,  # full quote for frontend to build tx
        }
    except Exception as e:
        logger.error(f"Swap quote error: {e}")
        raise HTTPException(500, "Swap quote failed")

@router.post("/build")
async def build_swap(req: dict, request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(401, "Authentication required")
    
    chain_id = settings.ONEINCH_CHAIN_IDS.get(req.get("chain", "polygon"))
    if not chain_id:
        raise HTTPException(400, "Unsupported chain")
    
    url = f"{settings.ONEINCH_BASE_URL}/{chain_id}/swap"
    headers = {"Authorization": f"Bearer {settings.ONEINCH_API_KEY}"}
    
    try:
        # The quote from the frontend
        quote = req.get("quote")
        from_address = req.get("from_address")
        if not quote or not from_address:
            raise HTTPException(400, "Missing quote or from_address")
        
        # 1inch Business API expects the following payload
        payload = {
            "fromTokenAddress": quote["fromToken"]["address"],
            "toTokenAddress": quote["toToken"]["address"],
            "amount": quote["fromTokenAmount"],
            "fromAddress": from_address,
            "slippage": quote.get("slippage", 0.5),
            "destReceiver": from_address,
        }
        resp = requests.post(url, json=payload, headers=headers, timeout=15)
        if resp.status_code != 200:
            logger.error(f"1inch Business API build error: {resp.text}")
            raise HTTPException(500, "Failed to build swap transaction")
        
        tx_data = resp.json()
        return tx_data
    except Exception as e:
        logger.error(f"Swap build error: {e}")
        raise HTTPException(500, "Failed to build swap transaction")