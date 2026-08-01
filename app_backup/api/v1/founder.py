from fastapi import APIRouter, Depends, HTTPException, Request
from app.core.security import get_current_user, create_token, hash_password, now_utc
from app.core.database import get_db
from app.core.config import settings
from app.services.wallet_service import create_wallet_for_user
from app.services.blockchain import send_close_from_distribution
from app.services.blockchain import get_all_balances
import uuid
import hmac
import secrets
from datetime import timedelta
import logging

router = APIRouter(prefix="/founder", tags=["Founder"])
logger = logging.getLogger(__name__)

@router.post("/")
async def founder_login(req: dict, request: Request):
    code = req.get("code", "")
    if not hmac.compare_digest(code, settings.FOUNDER_KEY):
        raise HTTPException(403, "Invalid founder code")
    
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute("SELECT id, wallet_address FROM users WHERE email = 'founder@osai.io'")
            existing = c.fetchone()
            if existing:
                user_id = existing[0]
                wallet_address = existing[1]
                c.execute("UPDATE users SET stake_tier='founder', is_founder=TRUE, close_balance=999999999 WHERE id=%s", (user_id,))
            else:
                user_id = str(uuid.uuid4())
                random_pass = secrets.token_urlsafe(32)
                c.execute("""
                    INSERT INTO users (id, email, password_hash, name, close_balance, stake_tier, is_founder)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                """, (user_id, "founder@osai.io", hash_password(random_pass), "OS AI Founder", 999999999, "founder", True))
                wallet_address = None
            
            token = create_token(user_id)
            c.execute("INSERT INTO user_sessions (user_id, token, expires_at) VALUES (%s, %s, %s)",
                      (user_id, token, now_utc() + timedelta(days=365)))
            conn.commit()
    
    # If founder has no wallet, create one and fund it with real CLOSE
    if not wallet_address:
        # Create wallet with a default password (we'll prompt user to set one later)
        # For simplicity, we'll create a wallet with a random password that the user can change
        temp_password = secrets.token_urlsafe(16)
        wallet_info = create_wallet_for_user(user_id, temp_password)
        wallet_address = wallet_info["address"]
        logger.info(f"Created wallet for founder: {wallet_address}")
        
        # Send real CLOSE from distribution wallet
        try:
            tx_hash = send_close_from_distribution(wallet_address, 1000000)  # 1 million CLOSE
            logger.info(f"Sent 1,000,000 CLOSE to founder wallet: {tx_hash}")
        except Exception as e:
            logger.error(f"Failed to send CLOSE to founder: {e}")
            # Still proceed, but log the error
    
    # Verify the balance (real)
    try:
        balances = get_all_balances(wallet_address)
        close_balance = balances.get("close", {}).get("balance", 0)
        # Update DB with real balance
        with get_db() as conn:
            with conn.cursor() as c:
                c.execute("UPDATE users SET close_balance = %s WHERE id = %s", (int(close_balance), user_id))
                conn.commit()
    except Exception as e:
        logger.error(f"Failed to fetch real balance: {e}")
    
    # Return user with real balance
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute("SELECT id, email, name, close_balance, close_staked, stake_tier, is_founder FROM users WHERE id = %s", (user_id,))
            row = c.fetchone()
            user_data = {
                "id": row[0],
                "email": row[1],
                "name": row[2] or row[1].split('@')[0],
                "close_balance": row[3] or 0,
                "close_staked": row[4] or 0,
                "stake_tier": row[5] or "none",
                "is_founder": row[6] or False,
            }
    
    return {
        "verified": True,
        "token": token,
        "user": user_data
    }
