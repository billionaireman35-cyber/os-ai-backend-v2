from fastapi import APIRouter, Depends, HTTPException, Query
from app.core.security import get_current_user
from app.core.database import get_db
import logging

router = APIRouter()
logger = logging.getLogger(__name__)


def _serialize_persistent(row):
    return {
        "id": str(row[0]),
        "type": row[1],
        "title": row[2],
        "description": row[3],
        "body": row[3],
        "url": row[4],
        "data": row[5] or {},
        "read": row[6],
        "event_key": row[7],
        "created_at": row[8].isoformat() if row[8] else None,
    }


@router.get("/")
async def get_notifications(
    limit: int = Query(20, ge=1, le=50),
    user=Depends(get_current_user),
):
    if not user:
        raise HTTPException(401, "Authentication required")

    user_id = user["id"]
    notifications = []

    with get_db() as conn:
        with conn.cursor() as c:
            # 1. Persistent notifications
            c.execute(
                """
                SELECT id, type, title, body, url, data,
                       read, event_key, created_at
                FROM notifications
                WHERE user_id = %s
                ORDER BY created_at DESC
                LIMIT %s
                """,
                (user_id, limit),
            )
            rows = c.fetchall()

            for row in rows:
                notifications.append(_serialize_persistent(row))

            # 2. Wallet created
            c.execute(
                """
                SELECT 'wallet_created' as type, created_at,
                       'Wallet created' as title,
                       CONCAT('Address: ', address) as description
                FROM os_wallets
                WHERE user_id = %s
                ORDER BY created_at DESC
                LIMIT 3
                """,
                (user_id,),
            )
            rows = c.fetchall()

            for row in rows:
                notifications.append({
                    "id": None,
                    "type": row[0],
                    "created_at": row[1].isoformat() if row[1] else None,
                    "title": row[2],
                    "description": row[3],
                    "body": row[3],
                    "url": None,
                    "data": {},
                    "read": False,
                    "event_key": None,
                })

            # 3. Transactions (burn, send, receive)
            c.execute(
                """
                SELECT type, amount, chain, status, created
                FROM close_transactions
                WHERE user_id = %s
                ORDER BY created DESC
                LIMIT 5
                """,
                (user_id,),
            )
            rows = c.fetchall()

            for row in rows:
                notifications.append({
                    "id": None,
                    "type": "transaction",
                    "created_at": row[4].isoformat() if row[4] else None,
                    "title": f"Transaction {row[0]}",
                    "description": f"{row[1]} on {row[2]} - {row[3]}",
                    "body": f"{row[1]} on {row[2]} - {row[3]}",
                    "url": None,
                    "data": {},
                    "read": False,
                    "event_key": None,
                })

            # 4. Workspace invites (pending)
            c.execute(
                """
                SELECT w.name, wm.joined_at, wm.status
                FROM workspace_members wm
                JOIN workspaces w ON wm.workspace_id = w.id
                WHERE wm.user_id = %s AND wm.status = 'pending'
                ORDER BY wm.joined_at DESC
                LIMIT 3
                """,
                (user_id,),
            )
            rows = c.fetchall()

            for row in rows:
                notifications.append({
                    "id": None,
                    "type": "workspace_invite",
                    "created_at": row[1].isoformat() if row[1] else None,
                    "title": f"Invited to {row[0]}",
                    "description": f"Status: {row[2]}",
                    "body": f"Status: {row[2]}",
                    "url": None,
                    "data": {},
                    "read": False,
                    "event_key": None,
                })

    notifications.sort(
        key=lambda x: x["created_at"] or "",
        reverse=True,
    )

    return notifications[:limit]


@router.get("/unread-count")
async def get_unread_notification_count(
    user=Depends(get_current_user),
):
    if not user:
        raise HTTPException(401, "Authentication required")

    with get_db() as conn:
        with conn.cursor() as c:
            c.execute(
                """
                SELECT COUNT(*)
                FROM notifications
                WHERE user_id = %s
                  AND read = FALSE
                """,
                (user["id"],),
            )
            count = c.fetchone()[0]

    return {"count": count}


@router.patch("/{notification_id}/read")
async def mark_notification_read(
    notification_id: str,
    user=Depends(get_current_user),
):
    if not user:
        raise HTTPException(401, "Authentication required")

    with get_db() as conn:
        with conn.cursor() as c:
            c.execute(
                """
                UPDATE notifications
                SET read = TRUE
                WHERE id = %s
                  AND user_id = %s
                RETURNING id, read
                """,
                (notification_id, user["id"]),
            )
            row = c.fetchone()

            if not row:
                raise HTTPException(404, "Notification not found")

            conn.commit()

    return {
        "id": str(row[0]),
        "read": row[1],
    }


@router.post("/read-all")
async def mark_all_notifications_read(
    user=Depends(get_current_user),
):
    if not user:
        raise HTTPException(401, "Authentication required")

    with get_db() as conn:
        with conn.cursor() as c:
            c.execute(
                """
                UPDATE notifications
                SET read = TRUE
                WHERE user_id = %s
                  AND read = FALSE
                """,
                (user["id"],),
            )
            updated = c.rowcount
            conn.commit()

    return {
        "message": "All notifications marked as read",
        "updated": updated,
    }
