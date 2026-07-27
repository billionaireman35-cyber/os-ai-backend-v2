import hashlib
import hmac
import json
import logging
import uuid
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException

from app.core.config import settings
from app.core.database import get_db
from app.core.security import get_current_user
from app.services.blockchain import broadcast_signed_transaction, get_all_balances
from app.services.wallet_service import create_wallet_for_user
from app.api.v1.market import get_prices

router = APIRouter(prefix="/safe", tags=["Safe"])
wallet_router = APIRouter(tags=["Wallet"])
logger = logging.getLogger(__name__)

@wallet_router.post("/create")
async def create_wallet(req: dict, user=Depends(get_current_user)):
    if not user:
        raise HTTPException(401, "Authentication required")
    password = req.get("password")
    if not password or len(password) < 8:
        raise HTTPException(400, "Wallet password must be at least 8 characters")

    with get_db() as conn:
        with conn.cursor() as c:
            c.execute("SELECT wallet_address FROM users WHERE id = %s", (user["id"],))
            row = c.fetchone()
            if row and row[0]:
                raise HTTPException(400, "Wallet already exists")

    try:
        result = create_wallet_for_user(user["id"], password)
        return {
            "address": result["address"],
            "seed_phrase": result["seed_phrase"],
            "message": "Save your seed phrase now — it will not be shown again.",
        }
    except Exception as e:
        logger.error(f"Wallet creation failed: {e}")
        raise HTTPException(500, "Wallet creation failed")

@wallet_router.get("/seed")
async def get_seed(user=Depends(get_current_user)):
    if not user:
        raise HTTPException(401, "Authentication required")
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute("SELECT wallet_encrypted_seed FROM users WHERE id = %s", (user["id"],))
            row = c.fetchone()
            if not row or not row[0]:
                raise HTTPException(404, "No wallet found")
    return {"encrypted_seed": row[0]}

@wallet_router.get("/balance")
async def get_balance(user=Depends(get_current_user)):
    if not user:
        raise HTTPException(401, "Authentication required")
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute("SELECT wallet_address, close_balance, close_staked FROM users WHERE id = %s", (user["id"],))
            row = c.fetchone()
    if not row or not row[0]:
        raise HTTPException(404, "No wallet found")

    try:
        # Get balances (without USD)
        balances = get_all_balances(row[0])
        # Get prices
        prices = get_prices()
        # Add USD values
        for chain in balances:
            if chain == "close":
                continue
            native = balances[chain]["native"]
            symbol = native["symbol"]
            native["usd"] = prices.get(symbol, 0.0) * float(native["balance"])
            # Also price tokens if we have any
            for token_symbol, token_data in balances[chain]["tokens"].items():
                token_data["usd"] = prices.get(token_symbol, 0.0) * float(token_data["balance"])
        # Add CLOSE
        balances["close"] = {
            "balance": row[1] or 0,
            "staked": row[2] or 0,
            "usd": prices.get("CLOSE", 0.0) * (row[1] or 0)
        }
        return balances
    except Exception as e:
        logger.error(f"Balance fetch failed: {e}")
        raise HTTPException(500, "Failed to fetch balances")

@wallet_router.post("/broadcast")
async def broadcast(req: dict, user=Depends(get_current_user)):
    if not user:
        raise HTTPException(401, "Authentication required")
    signed_tx = req.get("signed_tx")
    chain = req.get("chain", "polygon")
    if not signed_tx:
        raise HTTPException(400, "signed_tx is required")
    try:
        tx_hash = broadcast_signed_transaction(chain, signed_tx)
        return {"tx_hash": tx_hash}
    except Exception as e:
        logger.error(f"Broadcast failed: {e}")
        raise HTTPException(400, f"Broadcast failed: {e}")

@wallet_router.post("/moonpay-sign")
async def moonpay_sign(req: dict, user=Depends(get_current_user)):
    if not user:
        raise HTTPException(401, "Authentication required")
    widget_url = req.get("url")
    if not widget_url:
        raise HTTPException(400, "url is required")
    if not settings.MOONPAY_SECRET_KEY:
        raise HTTPException(500, "MoonPay is not configured")

    parsed = urlparse(widget_url)
    query_string = f"?{parsed.query}" if parsed.query else ""
    signature = hmac.new(
        settings.MOONPAY_SECRET_KEY.encode("utf-8"),
        query_string.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    import base64
    signature_b64 = base64.b64encode(signature).decode("utf-8")
    return {"signature": signature_b64}
