from fastapi import APIRouter, Depends, HTTPException
from app.core.config import settings
from app.core.security import get_current_user
from app.core.database import get_db
from app.services.blockchain import burn_close
import uuid
import logging

router = APIRouter()
logger = logging.getLogger(__name__)

@router.post("/burn")
async def burn_tokens(amount: int, user=Depends(get_current_user)):
    if not user:
        raise HTTPException(401, "Authentication required")
    user_id = user["id"]
    close_balance = user.get("close_balance", 0)

    if close_balance < amount:
        raise HTTPException(400, f"Insufficient CLOSE balance. You have {close_balance}, requested {amount}")

    burn_tx_id = str(uuid.uuid4())
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute(
                "UPDATE users SET close_balance = close_balance - %s WHERE id = %s AND close_balance >= %s",
                (amount, user_id, amount)
            )
            if c.rowcount == 0:
                raise HTTPException(400, "Insufficient balance during update")
            c.execute("""
                INSERT INTO close_transactions (id, user_id, type, amount, status)
                VALUES (%s, %s, %s, %s, %s)
            """, (burn_tx_id, user_id, "burn", amount, "pending"))
            conn.commit()

    try:
        tx_hash = burn_close(amount)
        with get_db() as conn:
            with conn.cursor() as c:
                c.execute("""
                    UPDATE close_transactions SET status = 'completed', tx_hash = %s
                    WHERE id = %s
                """, (tx_hash, burn_tx_id))
                conn.commit()
        return {"success": True, "tx_hash": tx_hash, "burned": amount, "new_balance": close_balance - amount}
    except Exception as e:
        logger.error(f"Burn transaction failed: {e}")
        with get_db() as conn:
            with conn.cursor() as c:
                c.execute("UPDATE users SET close_balance = close_balance + %s WHERE id = %s", (amount, user_id))
                c.execute("""
                    UPDATE close_transactions SET status = 'failed'
                    WHERE id = %s
                """, (burn_tx_id,))
                conn.commit()
        raise HTTPException(500, f"Burn failed: {str(e)}")

# Alias for frontend compatibility
@router.post("/burn/burn")
async def burn_tokens_alias(amount: int, user=Depends(get_current_user)):
    return await burn_tokens(amount, user)
