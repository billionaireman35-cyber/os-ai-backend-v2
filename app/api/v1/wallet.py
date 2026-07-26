import hashlib
import hmac
import json
import logging
import uuid
from urllib.parse import urlencode, urlparse, parse_qsl

from fastapi import APIRouter, Depends, HTTPException

from app.core.config import settings
from app.core.database import get_db
from app.core.security import get_current_user
from app.services.blockchain import broadcast_signed_transaction, get_all_balances
# from app.services.safe import create_safe, list_safes_for_user
from app.services.wallet_service import create_wallet_for_user

router = APIRouter(prefix="/safe", tags=["Safe"])
wallet_router = APIRouter(tags=["Wallet"])
logger = logging.getLogger(__name__)


# ---------------- Gnosis Safe (multisig) ----------------

#@router.post("/create")
#async def deploy_safe(req: dict, user=Depends(get_current_user)):
#    if not user:
#        raise HTTPException(401, "Authentication required")
#    owners = req.get("owners", [])
#    threshold = req.get("threshold", 1)
#    chain = req.get("chain", "polygon")
#    if not owners or threshold < 1 or threshold > len(owners):
#        raise HTTPException(400, "Invalid owners or threshold")
#    try:
#        safe_address = create_safe(owners, threshold, chain)
#        with get_db() as conn:
#            with conn.cursor() as c:
#                c.execute("""
#                    INSERT INTO user_safes (id, user_id, safe_address, chain, owners, threshold)
#                    VALUES (%s, %s, %s, %s, %s, %s)
#                """, (str(uuid.uuid4()), user["id"], safe_address, chain, json.dumps(owners), threshold))
#                conn.commit()
#        return {"safe_address": safe_address, "chain": chain}
#    except Exception as e:
#        logger.error(f"Safe creation failed: {e}")
#        raise HTTPException(500, "Safe creation failed")
#
#
#@router.get("/list")
#async def list_safes(user=Depends(get_current_user)):
#    if not user:
#        raise HTTPException(401, "Authentication required")
#    safes = list_safes_for_user(user["id"])
#    return {"safes": safes}
#
#
## ---------------- Standard (EOA) wallet ----------------
#
#@wallet_router.post("/create")
#async def create_wallet(req: dict, user=Depends(get_current_user)):
#    """
#    Create a standard (non-Safe) wallet for the current user, encrypted with a
#    wallet-specific password chosen by the user. Returns the address and the
#    one-time seed phrase for backup — this is the only time the seed phrase
#    is ever shown in plaintext.
#    """
#    if not user:
#        raise HTTPException(401, "Authentication required")
#    password = req.get("password")
#    if not password or len(password) < 8:
#        raise HTTPException(400, "Wallet password must be at least 8 characters")
#
#    with get_db() as conn:
#        with conn.cursor() as c:
#            c.execute("SELECT wallet_address FROM users WHERE id = %s", (user["id"],))
#            row = c.fetchone()
#            if row and row[0]:
#                raise HTTPException(400, "Wallet already exists for this account")
#
#    try:
#        result = create_wallet_for_user(user["id"], password)
#        return {
#            "address": result["address"],
#            "seed_phrase": result["seed_phrase"],
#            "message": "Save your seed phrase now — it will not be shown again.",
#        }
#    except Exception as e:
#        logger.error(f"Wallet creation failed: {e}")
#        raise HTTPException(500, "Wallet creation failed")
#
#
#@wallet_router.get("/seed")
#async def get_seed(user=Depends(get_current_user)):
#    """Return the encrypted seed so the frontend can decrypt it locally with the wallet password."""
#    if not user:
#        raise HTTPException(401, "Authentication required")
#    with get_db() as conn:
#        with conn.cursor() as c:
#            c.execute("SELECT wallet_encrypted_seed FROM users WHERE id = %s", (user["id"],))
#            row = c.fetchone()
#            if not row or not row[0]:
#                raise HTTPException(404, "No wallet found for this account")
#    return {"encrypted_seed": row[0]}
#
#
#@wallet_router.get("/balance")
#async def get_balance(user=Depends(get_current_user)):
#    if not user:
#        raise HTTPException(401, "Authentication required")
#    with get_db() as conn:
#        with conn.cursor() as c:
#            c.execute("SELECT wallet_address FROM users WHERE id = %s", (user["id"],))
#            row = c.fetchone()
#    if not row or not row[0]:
#        raise HTTPException(404, "No wallet found for this account")
#
#    try:
#        return get_all_balances(row[0])
#    except Exception as e:
#        logger.error(f"Balance fetch failed: {e}")
#        raise HTTPException(500, "Failed to fetch balances")
#
#
#@wallet_router.post("/broadcast")
#async def broadcast(req: dict, user=Depends(get_current_user)):
#    """Relay a client-signed transaction to the chain. The private key never touches the backend."""
#    if not user:
#        raise HTTPException(401, "Authentication required")
#    signed_tx = req.get("signed_tx")
#    chain = req.get("chain", "polygon")
#    if not signed_tx:
#        raise HTTPException(400, "signed_tx is required")
#    try:
#        tx_hash = broadcast_signed_transaction(chain, signed_tx)
#        return {"tx_hash": tx_hash}
#    except Exception as e:
#        logger.error(f"Broadcast failed: {e}")
#        raise HTTPException(400, f"Broadcast failed: {e}")
#
#
## ---------------- MoonPay widget URL signing ----------------
#
#@wallet_router.post("/moonpay-sign")
#async def moonpay_sign(req: dict, user=Depends(get_current_user)):
#    """
#    Sign a MoonPay widget URL's query string with HMAC-SHA256 using the
#    secret key. The secret key never leaves the backend.
#    """
#    if not user:
#        raise HTTPException(401, "Authentication required")
#    widget_url = req.get("url")
#    if not widget_url:
#        raise HTTPException(400, "url is required")
#    if not settings.MOONPAY_SECRET_KEY:
#        raise HTTPException(500, "MoonPay is not configured")
#
#    parsed = urlparse(widget_url)
#    query_string = f"?{parsed.query}" if parsed.query else ""
#    signature = hmac.new(
#        settings.MOONPAY_SECRET_KEY.encode("utf-8"),
#        query_string.encode("utf-8"),
#        hashlib.sha256,
#    ).digest()
#    import base64
#    signature_b64 = base64.b64encode(signature).decode("utf-8")
#
#    return {"signature": signature_b64}
