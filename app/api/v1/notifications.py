from fastapi import APIRouter, Depends, HTTPException, Query
from app.core.security import get_current_user
from app.core.database import get_db
import logging

router = APIRouter()
logger = logging.getLogger(__name__)

@router.get("/")
async def get_notifications(
    limit: int = Query(20, ge=1, le=50),
    user=Depends(get_current_user)
):
    if not user:
        raise HTTPException(401, "Authentication required")
    user_id = user["id"]
    notifications = []

    with get_db() as conn:
        with conn.cursor() as c:
            # 1. Wallet created
            c.execute("""
                SELECT 'wallet_created' as type, created_at, 
                       'Wallet created' as title, 
                       CONCAT('Address: ', address) as description
                FROM os_wallets
                WHERE user_id = %s
                ORDER BY created_at DESC
                LIMIT 3
            """, (user_id,))
            rows = c.fetchall()
            for row in rows:
                notifications.append({
                    "type": row[0],
                    "created_at": row[1].isoformat() if row[1] else None,
                    "title": row[2],
                    "description": row[3],
                })

            # 2. Transactions (burn, send, receive)
            c.execute("""
                SELECT type, amount, chain, status, created
                FROM close_transactions
                WHERE user_id = %s
                ORDER BY created DESC
                LIMIT 5
            """, (user_id,))
            rows = c.fetchall()
            for row in rows:
                notifications.append({
                    "type": "transaction",
                    "created_at": row[4].isoformat() if row[4] else None,
                    "title": f"Transaction {row[0]}",
                    "description": f"{row[1]} on {row[2]} - {row[3]}",
                })

            # 3. Workspace invites (pending)
            c.execute("""
                SELECT w.name, wm.created_at, wm.status
                FROM workspace_members wm
                JOIN workspaces w ON wm.workspace_id = w.id
                WHERE wm.user_id = %s AND wm.status = 'pending'
                ORDER BY wm.created_at DESC
                LIMIT 3
            """, (user_id,))
            rows = c.fetchall()
            for row in rows:
                notifications.append({
                    "type": "workspace_invite",
                    "created_at": row[1].isoformat() if row[1] else None,
                    "title": f"Invited to {row[0]}",
                    "description": f"Status: {row[2]}",
                })

            # 4. AI messages? Skip to avoid noise.

            # Sort by created_at descending
            notifications.sort(key=lambda x: x["created_at"] or "", reverse=True)
            return notifications[:limit]
