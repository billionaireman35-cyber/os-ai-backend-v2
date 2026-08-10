from fastapi import APIRouter, Depends, HTTPException, Body
from app.core.security import get_current_user
from app.services.moonpay_service import generate_buy_url, generate_sell_url
from app.core.config import settings
import logging

router = APIRouter()
logger = logging.getLogger(__name__)

@router.post("/buy")
async def moonpay_buy(
    currency_code: str = Body(..., embed=True),
    fiat_currency: str = Body("usd", embed=True),
    fiat_amount: float = Body(50.0, embed=True),
    user=Depends(get_current_user)
):
    if not user:
        raise HTTPException(401, "Authentication required")
    wallet_address = user.get("wallet_address")
    if not wallet_address:
        raise HTTPException(400, "No wallet address found. Please create a wallet first.")
    try:
        url = generate_buy_url(
            wallet_address=wallet_address,
            currency_code=currency_code,
            fiat_currency=fiat_currency,
            fiat_amount=fiat_amount,
            redirect_url=f"{settings.FRONTEND_URL}/vault"
        )
        return {"url": url}
    except Exception as e:
        logger.error(f"MoonPay buy URL generation failed: {e}")
        raise HTTPException(500, "Failed to generate buy URL")

@router.post("/sell")
async def moonpay_sell(
    currency_code: str = Body(..., embed=True),
    fiat_currency: str = Body("usd", embed=True),
    crypto_amount: float = Body(None, embed=True),
    user=Depends(get_current_user)
):
    if not user:
        raise HTTPException(401, "Authentication required")
    wallet_address = user.get("wallet_address")
    if not wallet_address:
        raise HTTPException(400, "No wallet address found. Please create a wallet first.")
    try:
        url = generate_sell_url(
            wallet_address=wallet_address,
            currency_code=currency_code,
            fiat_currency=fiat_currency,
            crypto_amount=crypto_amount,
            redirect_url=f"{settings.FRONTEND_URL}/vault"
        )
        return {"url": url}
    except Exception as e:
        logger.error(f"MoonPay sell URL generation failed: {e}")
        raise HTTPException(500, "Failed to generate sell URL")
