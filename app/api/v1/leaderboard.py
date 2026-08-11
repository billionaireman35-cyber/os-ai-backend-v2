from fastapi import APIRouter, Depends, HTTPException, Query
from app.core.security import get_current_user
from app.core.database import get_db
import logging
import math
from datetime import datetime, timezone

router = APIRouter()
logger = logging.getLogger(__name__)

MONTHLY_POOL = 450000
TOP_N = 1000

def harmonic_sum(n):
    return sum(1.0 / i for i in range(1, n+1))

H_N = harmonic_sum(TOP_N)

@router.get("/monthly")
async def get_monthly_leaderboard(
    limit: int = Query(TOP_N, ge=1, le=TOP_N),
    user=Depends(get_current_user)
):
    if not user:
        raise HTTPException(401, "Authentication required")

    now = datetime.now(timezone.utc)
    start_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    try:
        with get_db() as conn:
            with conn.cursor() as c:
                # 1. Get top burners this month (completed burns only)
                c.execute("""
                    SELECT u.id, u.name, u.email,
                           COALESCE(SUM(ct.amount), 0) as total_burned
                    FROM users u
                    LEFT JOIN close_transactions ct ON u.id = ct.user_id
                        AND ct.type = 'burn'
                        AND ct.status = 'completed'
                        AND ct.created >= %s
                    GROUP BY u.id
                    HAVING COALESCE(SUM(ct.amount), 0) > 0
                    ORDER BY total_burned DESC
                    LIMIT %s
                """, (start_of_month, limit))
                rows = c.fetchall()

                leaderboard = []
                for idx, row in enumerate(rows, start=1):
                    rank = idx
                    total_burned = row[3]
                    reward = math.floor(MONTHLY_POOL * (1.0 / rank) / H_N) if rank <= TOP_N else 0
                    leaderboard.append({
                        "rank": rank,
                        "user_id": row[0],
                        "name": row[1] or row[2].split('@')[0],
                        "email": row[2],
                        "total_burned": total_burned,
                        "reward": reward,
                    })

                # 2. User's own rank and total burned
                c.execute("""
                    SELECT COALESCE(SUM(amount), 0)
                    FROM close_transactions
                    WHERE user_id = %s AND type = 'burn' AND status = 'completed' AND created >= %s
                """, (user["id"], start_of_month))
                user_total = c.fetchone()[0] or 0

                # Rank: count users with more burns than this user
                c.execute("""
                    SELECT COUNT(DISTINCT u.id) + 1 as rank
                    FROM users u
                    LEFT JOIN close_transactions ct ON u.id = ct.user_id
                        AND ct.type = 'burn'
                        AND ct.status = 'completed'
                        AND ct.created >= %s
                    GROUP BY u.id
                    HAVING COALESCE(SUM(ct.amount), 0) > %s
                """, (start_of_month, user_total))
                rank_row = c.fetchone()
                user_rank = rank_row[0] if rank_row else None

                user_reward = 0
                if user_rank and user_rank <= TOP_N:
                    user_reward = math.floor(MONTHLY_POOL * (1.0 / user_rank) / H_N)

                return {
                    "leaderboard": leaderboard,
                    "user_rank": user_rank,
                    "user_total_burned": user_total,
                    "user_reward": user_reward,
                    "pool": MONTHLY_POOL,
                    "month": start_of_month.strftime("%B %Y"),
                }
    except Exception as e:
        logger.error(f"Leaderboard error: {e}")
        raise HTTPException(500, f"Leaderboard error: {str(e)}")
