from fastapi import APIRouter, Depends, HTTPException, Request
from app.models.schemas import BridgeQuoteRequest
from app.core.security import get_current_user
from app.core.config import settings
import logging

router = APIRouter()
logger = logging.getLogger(__name__)

@router.post("/quote")
async def get_bridge_quote(req: BridgeQuoteRequest, request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(401, "Authentication required")
    fee = req.amount * (settings.BRIDGE_FEE_PERCENT / 100)
    return {
        "from_chain": req.from_chain,
        "to_chain": req.to_chain,
        "token": req.token,
        "amount_in": req.amount,
        "amount_out": req.amount * 0.99,
        "fee_usd": fee,
        "fee_percent": settings.BRIDGE_FEE_PERCENT
    }
