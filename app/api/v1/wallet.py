from fastapi import APIRouter, Depends, HTTPException, Query, Body
from app.core.security import get_current_user
from app.services.wallet_service import (
    get_user_balance,
    get_user_transactions,
    create_wallet_for_user,
    send_transaction,
    get_user_private_key
)
import logging

router = APIRouter()
logger = logging.getLogger(__name__)

wallet_router = router

@router.get("/balance")
async def get_balance(user=Depends(get_current_user)):
    if not user:
        raise HTTPException(401, "Authentication required")
    try:
        balances = get_user_balance(user["id"])
        return {"balances": balances}
    except Exception as e:
        logger.error(f"Balance fetch failed: {e}")
        raise HTTPException(500, "Failed to fetch balances")

@router.get("/transactions")
async def get_transactions(
    limit: int = Query(20, ge=1, le=100),
    user=Depends(get_current_user)
):
    if not user:
        raise HTTPException(401, "Authentication required")
    try:
        txs = get_user_transactions(user["id"], limit)
        return {"transactions": txs}
    except Exception as e:
        logger.error(f"Transactions fetch failed: {e}")
        raise HTTPException(500, "Failed to fetch transactions")

@router.post("/create")
async def create_wallet(
    password: str = Body(..., embed=True),
    user=Depends(get_current_user)
):
    if not user:
        raise HTTPException(401, "Authentication required")
    if len(password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters")
    try:
        result = create_wallet_for_user(user["id"], password)
        return {"wallet": result}
    except Exception as e:
        logger.error(f"Wallet creation failed: {e}")
        raise HTTPException(500, "Failed to create wallet")

@router.post("/send")
async def send(
    chain: str = Body(...),
    to_address: str = Body(...),
    amount_wei: int = Body(...),
    password: str = Body(...),
    token_address: str = Body(None),
    data: str = Body("0x"),
    user=Depends(get_current_user)
):
    if not user:
        raise HTTPException(401, "Authentication required")
    if len(password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters")
    try:
        tx_hash = send_transaction(
            user_id=user["id"],
            password=password,
            chain=chain,
            to_address=to_address,
            amount_wei=amount_wei,
            token_address=token_address,
            data=data
        )
        return {"tx_hash": tx_hash}
    except Exception as e:
        logger.error(f"Send failed: {e}")
        raise HTTPException(500, f"Send failed: {str(e)}")

@router.post("/sign")
async def sign(
    chain: str = Body(...),
    transaction: dict = Body(...),  # expects {to, value, data?, gas?, gasPrice?, nonce?}
    password: str = Body(...),
    user=Depends(get_current_user)
):
    if not user:
        raise HTTPException(401, "Authentication required")
    if len(password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters")
    try:
        private_key_hex = get_user_private_key(user["id"], password)
        from app.services.transaction import sign_transaction
        # We need from_address; get it from user's wallet
        from app.core.database import get_db
        with get_db() as conn:
            with conn.cursor() as c:
                c.execute("SELECT wallet_address FROM users WHERE id = %s", (user["id"],))
                row = c.fetchone()
                if not row or not row[0]:
                    raise HTTPException(400, "No wallet address found")
                from_address = row[0]
        signed_hex = sign_transaction(
            chain=chain,
            from_address=from_address,
            to_address=transaction["to"],
            value_wei=transaction.get("value", 0),
            private_key_hex=private_key_hex,
            data=transaction.get("data", "0x"),
            gas_limit=transaction.get("gas"),
            gas_price=transaction.get("gasPrice"),
            nonce=transaction.get("nonce")
        )
        return {"signed_tx": signed_hex}
    except Exception as e:
        logger.error(f"Sign failed: {e}")
        raise HTTPException(500, f"Sign failed: {str(e)}")
