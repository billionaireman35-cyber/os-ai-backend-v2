from fastapi import APIRouter, Depends, HTTPException
from app.core.security import get_current_user
from app.core.database import get_db
import logging

router = APIRouter()
logger = logging.getLogger(__name__)

def founder_only(user=Depends(get_current_user)):
    if not user:
        raise HTTPException(401, "Authentication required")
    if not user.get("is_founder") and user.get("stake_tier") != "founder":
        raise HTTPException(403, "Founder access required")
    return user

@router.get("/dashboard")
async def get_dashboard(user=Depends(founder_only)):
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute("SELECT COUNT(*) FROM users")
            total_users = c.fetchone()[0]
            c.execute("SELECT COUNT(*) FROM users WHERE last_active > NOW() - INTERVAL '24 hours'")
            active_users = c.fetchone()[0]
            c.execute("SELECT COALESCE(SUM(amount),0) FROM close_transactions WHERE type='burn' AND status='completed'")
            total_burned = c.fetchone()[0]
            c.execute("SELECT COALESCE(SUM(close_balance),0) FROM users")
            total_close = c.fetchone()[0]
    return {
        "total_users": total_users,
        "active_users": active_users,
        "total_burned": total_burned,
        "total_close_in_circulation": total_close,
        "total_revenue_usd": 0
    }
