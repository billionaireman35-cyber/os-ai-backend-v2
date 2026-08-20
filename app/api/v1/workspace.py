from fastapi import APIRouter, Depends, HTTPException, Body, Query
from app.core.security import get_current_user
from app.core.database import get_db
from app.services.workspace_payment_service import verify_workspace_payment, get_unresolved_payment, link_payment_to_workspace
import uuid, logging

router = APIRouter()
logger = logging.getLogger(__name__)

# Membership model (locked 2026-08-19): one-time payment, permanent access.
# No burn, no subscription, no expiry, no renewal. Payment is verified
# on-chain (see workspace_payment_service.py) rather than debited from an
# internal DB balance - CLOSE moves for real, to the platform treasury,
# before access is granted. Do not turn this into a recurring charge or
# revert to internal-ledger debiting without a deliberate product decision.
WORKSPACE_CREATE_COST = 5000
WORKSPACE_JOIN_COST = 6000


def _is_admin(c, workspace_id: str, user_id: str) -> bool:
    c.execute(
        "SELECT role FROM workspace_members WHERE workspace_id = %s AND user_id = %s AND status = 'approved'",
        (workspace_id, user_id)
    )
    row = c.fetchone()
    return bool(row and row[0] == "admin")


@router.post("/create")
async def create_workspace(
    name: str = Body(...),
    description: str = Body(""),
    is_public: bool = Body(False),
    tx_hash: str = Body(..., description="Tx hash of the 5000 CLOSE payment to the treasury address"),
    user=Depends(get_current_user)
):
    if not user:
        raise HTTPException(401, "Authentication required")

    user_id = user["id"]

    # If this exact tx_hash was already verified for a previous, failed
    # create_workspace attempt (payment succeeded, creation itself didn't),
    # skip re-verification - it would wrongly reject as "already used".
    # Resume with the existing verified payment instead of re-charging.
    payment_id = get_unresolved_payment(user_id, tx_hash, "create")
    if not payment_id:
        try:
            result = verify_workspace_payment(user_id, tx_hash, WORKSPACE_CREATE_COST, "create")
            payment_id = result["id"]
        except ValueError as e:
            raise HTTPException(402, str(e))

    room_code = ''.join(uuid.uuid4().hex[:8].upper())
    workspace_id = str(uuid.uuid4())

    with get_db() as conn:
        with conn.cursor() as c:
            c.execute("""
                INSERT INTO workspaces (id, name, description, room_code, owner_id, is_public)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (workspace_id, name, description, room_code, user_id, is_public))

            c.execute("""
                INSERT INTO workspace_members (workspace_id, user_id, role, status)
                VALUES (%s, %s, %s, %s)
            """, (workspace_id, user_id, "admin", "approved"))

            conn.commit()

    # Payment is now resolved - link it so a future retry with this same
    # tx_hash won't be treated as unresolved again.
    link_payment_to_workspace(payment_id, workspace_id)

    return {
        "id": workspace_id,
        "name": name,
        "description": description,
        "room_code": room_code,
        "owner_id": user_id,
        "is_public": is_public,
        "members": [{"user_id": user_id, "role": "admin", "status": "approved"}]
    }


@router.get("/list")
async def list_workspaces(user=Depends(get_current_user)):
    if not user:
        raise HTTPException(401, "Authentication required")
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute("""
                SELECT w.id, w.name, w.description, w.room_code, w.owner_id, w.is_public, w.created_at,
                       (SELECT COUNT(*) FROM workspace_members WHERE workspace_id = w.id AND status = 'approved') as member_count
                FROM workspaces w
                INNER JOIN workspace_members m ON w.id = m.workspace_id
                WHERE m.user_id = %s AND m.status = 'approved'
                GROUP BY w.id
                ORDER BY w.created_at DESC
            """, (user["id"],))
            rows = c.fetchall()
            return [
                {
                    "id": row[0], "name": row[1], "description": row[2], "room_code": row[3],
                    "owner_id": row[4], "is_public": row[5],
                    "created_at": row[6].isoformat() if row[6] else None,
                    "member_count": row[7], "is_member": True
                }
                for row in rows
            ]


@router.post("/join")
async def join_workspace(
    room_code: str = Body(...),
    user=Depends(get_current_user)
):
    """Free join REQUEST - no payment yet. Payment happens at approval."""
    if not user:
        raise HTTPException(401, "Authentication required")

    user_id = user["id"]

    with get_db() as conn:
        with conn.cursor() as c:
            c.execute("SELECT id FROM workspaces WHERE room_code = %s", (room_code.upper(),))
            row = c.fetchone()
            if not row:
                raise HTTPException(404, "Workspace not found")
            workspace_id = row[0]

            c.execute(
                "SELECT status FROM workspace_members WHERE workspace_id = %s AND user_id = %s",
                (workspace_id, user_id)
            )
            existing = c.fetchone()
            if existing:
                if existing[0] == "approved":
                    return {"message": "Already a member"}
                return {"message": "Join request already pending approval"}

            c.execute(
                "INSERT INTO workspace_members (workspace_id, user_id, role, status) VALUES (%s, %s, %s, %s)",
                (workspace_id, user_id, "member", "pending")
            )
            conn.commit()

    return {"message": "Join request sent — pending approval from the hub owner"}


@router.get("/{workspace_id}/requests")
async def list_join_requests(workspace_id: str, user=Depends(get_current_user)):
    if not user:
        raise HTTPException(401, "Authentication required")
    with get_db() as conn:
        with conn.cursor() as c:
            if not _is_admin(c, workspace_id, user["id"]):
                raise HTTPException(403, "Only the hub owner or admins can view join requests")
            c.execute("""
                SELECT m.user_id, u.name, m.role
                FROM workspace_members m
                LEFT JOIN users u ON u.id = m.user_id
                WHERE m.workspace_id = %s AND m.status = 'pending'
            """, (workspace_id,))
            rows = c.fetchall()
            return [{"user_id": r[0], "user_name": r[1] or "Unknown", "role": r[2]} for r in rows]


@router.post("/{workspace_id}/requests/{requester_id}/approve")
async def approve_join_request(
    workspace_id: str,
    requester_id: str,
    tx_hash: str = Body(..., embed=True, description="Tx hash of the requester's 6000 CLOSE payment to the treasury address"),
    user=Depends(get_current_user)
):
    """
    Approves a pending join request. The 6000 CLOSE payment must already be
    on-chain (paid by the REQUESTER, from their own wallet, to the treasury)
    before this is called - the admin submits the requester's tx_hash here
    to confirm and finalize. If payment verification fails (wrong sender,
    wrong amount, already used), approval is blocked with a 402.
    """
    if not user:
        raise HTTPException(401, "Authentication required")

    with get_db() as conn:
        with conn.cursor() as c:
            if not _is_admin(c, workspace_id, user["id"]):
                raise HTTPException(403, "Only the hub owner or admins can approve join requests")

            c.execute(
                "SELECT status FROM workspace_members WHERE workspace_id = %s AND user_id = %s",
                (workspace_id, requester_id)
            )
            row = c.fetchone()
            if not row:
                raise HTTPException(404, "No join request found for this user")
            if row[0] == "approved":
                return {"message": "Already approved"}
            if row[0] != "pending":
                raise HTTPException(400, f"Request is not pending (status: {row[0]})")

    try:
        verify_workspace_payment(requester_id, tx_hash, WORKSPACE_JOIN_COST, "join", workspace_id)
    except ValueError as e:
        raise HTTPException(402, str(e))

    with get_db() as conn:
        with conn.cursor() as c:
            c.execute(
                "UPDATE workspace_members SET status = 'approved' WHERE workspace_id = %s AND user_id = %s",
                (workspace_id, requester_id)
            )
            conn.commit()

    return {"message": "Request approved"}


@router.post("/{workspace_id}/requests/{requester_id}/reject")
async def reject_join_request(workspace_id: str, requester_id: str, user=Depends(get_current_user)):
    if not user:
        raise HTTPException(401, "Authentication required")
    with get_db() as conn:
        with conn.cursor() as c:
            if not _is_admin(c, workspace_id, user["id"]):
                raise HTTPException(403, "Only the hub owner or admins can reject join requests")
            c.execute(
                "DELETE FROM workspace_members WHERE workspace_id = %s AND user_id = %s AND status = 'pending'",
                (workspace_id, requester_id)
            )
            if c.rowcount == 0:
                raise HTTPException(404, "No pending join request found for this user")
            conn.commit()
    return {"message": "Request rejected"}


@router.get("/{workspace_id}/messages")
async def get_workspace_messages(
    workspace_id: str,
    limit: int = Query(50, ge=1, le=200),
    user=Depends(get_current_user)
):
    if not user:
        raise HTTPException(401, "Authentication required")
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute(
                "SELECT user_id FROM workspace_members WHERE workspace_id = %s AND user_id = %s AND status = 'approved'",
                (workspace_id, user["id"])
            )
            if not c.fetchone():
                raise HTTPException(403, "Not an approved member of this workspace")
            c.execute("""
                SELECT id, user_id, content, is_ai, created_at
                FROM workspace_messages
                WHERE workspace_id = %s
                ORDER BY created_at ASC
                LIMIT %s
            """, (workspace_id, limit))
            rows = c.fetchall()
            messages = []
            for row in rows:
                c.execute("SELECT name FROM users WHERE id = %s", (row[1],))
                user_row = c.fetchone()
                messages.append({
                    "id": row[0], "user_id": row[1],
                    "user_name": user_row[0] if user_row else "Unknown",
                    "content": row[2], "is_ai": row[3],
                    "created_at": row[4].isoformat() if row[4] else None
                })
            return messages


@router.post("/{workspace_id}/message")
async def send_workspace_message(
    workspace_id: str,
    content: str = Body(..., embed=True),
    user=Depends(get_current_user)
):
    if not user:
        raise HTTPException(401, "Authentication required")
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute(
                "SELECT user_id FROM workspace_members WHERE workspace_id = %s AND user_id = %s AND status = 'approved'",
                (workspace_id, user["id"])
            )
            if not c.fetchone():
                raise HTTPException(403, "Not an approved member of this workspace")
            msg_id = str(uuid.uuid4())
            c.execute("""
                INSERT INTO workspace_messages (id, workspace_id, user_id, content)
                VALUES (%s, %s, %s, %s)
            """, (msg_id, workspace_id, user["id"], content))
            conn.commit()
    return {"id": msg_id, "content": content, "user_id": user["id"], "user_name": user.get("name", "User")}
