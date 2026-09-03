from fastapi import APIRouter, Depends, HTTPException, Body, Query
from app.core.security import get_current_user
from app.core.database import get_db
from app.core.config import settings
from app.services.workspace_payment_service import verify_workspace_payment, get_unresolved_payment, link_payment_to_workspace
from app.services.ai import call_ai_model, build_system_prompt, search_web
from app.services.memory import get_memories
from app.services.blockchain import get_effective_burn_amount
import uuid, logging, re, traceback

router = APIRouter()
logger = logging.getLogger(__name__)

WORKSPACE_CREATE_COST = 5000
WORKSPACE_JOIN_COST = 6000

# Hustle Hub is a Gold+ feature - matches Foundry's gate. Existing approved
# members keep access to hubs they've already paid into even if their tier
# later drops (permanent-access principle, see WORKSPACE_CREATE_COST
# comment history) - this check only gates NEW entry points: creating a
# hub, requesting to join, paying to join, and browsing public hubs.
GATEWAY_MIN_TIERS = {"gold", "platinum"}


def _require_tier_access(user: dict):
    tier = (user or {}).get("stake_tier", "bronze")
    if tier not in GATEWAY_MIN_TIERS and not (user or {}).get("is_founder"):
        raise HTTPException(403, "Hustle Hub requires Gold tier or higher (10,000+ CLOSE staked).")


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
    _require_tier_access(user)

    user_id = user["id"]

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

    link_payment_to_workspace(payment_id, workspace_id)

    return {
        "id": workspace_id, "name": name, "description": description,
        "room_code": room_code, "owner_id": user_id, "is_public": is_public,
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


@router.get("/discover")
async def discover_public_workspaces(
    query: str = Query("", description="Optional name search"),
    limit: int = Query(30, ge=1, le=100),
    user=Depends(get_current_user)
):
    """Browse public hubs the user isn't already in - lets someone find a
    room_code to request-to-join instead of needing it shared out-of-band."""
    if not user:
        raise HTTPException(401, "Authentication required")
    _require_tier_access(user)

    with get_db() as conn:
        with conn.cursor() as c:
            c.execute("""
                SELECT w.id, w.name, w.description, w.room_code, w.owner_id, w.created_at,
                       (SELECT COUNT(*) FROM workspace_members WHERE workspace_id = w.id AND status = 'approved') as member_count
                FROM workspaces w
                WHERE w.is_public = TRUE
                AND w.id NOT IN (
                    SELECT workspace_id FROM workspace_members WHERE user_id = %s
                )
                AND (%s = '' OR w.name ILIKE %s)
                ORDER BY member_count DESC
                LIMIT %s
            """, (user["id"], query, f"%{query}%", limit))
            rows = c.fetchall()
            return [
                {
                    "id": row[0], "name": row[1], "description": row[2], "room_code": row[3],
                    "owner_id": row[4], "created_at": row[5].isoformat() if row[5] else None,
                    "member_count": row[6],
                }
                for row in rows
            ]


@router.post("/join")
async def join_workspace(
    room_code: str = Body(...),
    user=Depends(get_current_user)
):
    """Free join REQUEST - no payment yet. Payment happens via
    /requests/submit-payment (self-service) after this."""
    if not user:
        raise HTTPException(401, "Authentication required")
    _require_tier_access(user)

    user_id = user["id"]

    with get_db() as conn:
        with conn.cursor() as c:
            c.execute("SELECT id, name FROM workspaces WHERE room_code = %s", (room_code.upper(),))
            row = c.fetchone()
            if not row:
                raise HTTPException(404, "Workspace not found")
            workspace_id, workspace_name = row

            c.execute(
                "SELECT status FROM workspace_members WHERE workspace_id = %s AND user_id = %s",
                (workspace_id, user_id)
            )
            existing = c.fetchone()
            if existing:
                if existing[0] == "approved":
                    return {"message": "Already a member", "workspace_id": workspace_id, "workspace_name": workspace_name, "status": "approved"}
                return {"message": "Join request already pending payment", "workspace_id": workspace_id, "workspace_name": workspace_name, "status": "pending"}

            c.execute(
                "INSERT INTO workspace_members (workspace_id, user_id, role, status) VALUES (%s, %s, %s, %s)",
                (workspace_id, user_id, "member", "pending")
            )
            conn.commit()

    return {
        "message": "Join request created — pay 6000 CLOSE to activate membership",
        "workspace_id": workspace_id, "workspace_name": workspace_name, "status": "pending"
    }


@router.post("/{workspace_id}/requests/submit-payment")
async def submit_join_payment(
    workspace_id: str,
    tx_hash: str = Body(..., embed=True),
    user=Depends(get_current_user)
):
    """
    Self-service: the requester (not an admin) submits their own 6000 CLOSE
    payment tx_hash directly, right after paying. verify_workspace_payment
    already checks the tx was sent from THIS user's own registered wallet,
    so there's no trust gap in skipping the admin relay step - this removes
    the old "message the admin your hash" friction entirely. The old
    admin-approve-by-hash endpoint below is kept as a manual fallback.

    Resume-safe: if this exact tx_hash was already verified for this
    workspace/user (e.g. the membership UPDATE failed after a prior
    successful verify), skip re-verification instead of rejecting it as
    "already used" and leaving the user stuck paid-but-not-approved.
    """
    if not user:
        raise HTTPException(401, "Authentication required")
    _require_tier_access(user)
    user_id = user["id"]

    with get_db() as conn:
        with conn.cursor() as c:
            c.execute(
                "SELECT status FROM workspace_members WHERE workspace_id = %s AND user_id = %s",
                (workspace_id, user_id)
            )
            row = c.fetchone()
            if not row:
                raise HTTPException(404, "No join request found for this workspace")
            if row[0] == "approved":
                return {"message": "Already approved"}
            if row[0] != "pending":
                raise HTTPException(400, f"Request is not pending (status: {row[0]})")

            c.execute("""
                SELECT id FROM workspace_payments
                WHERE user_id = %s AND tx_hash = %s AND purpose = 'join' AND workspace_id = %s
            """, (user_id, tx_hash, workspace_id))
            already_verified = c.fetchone()

    if not already_verified:
        try:
            verify_workspace_payment(user_id, tx_hash, WORKSPACE_JOIN_COST, "join", workspace_id)
        except ValueError as e:
            raise HTTPException(402, str(e))

    with get_db() as conn:
        with conn.cursor() as c:
            c.execute(
                "UPDATE workspace_members SET status = 'approved' WHERE workspace_id = %s AND user_id = %s",
                (workspace_id, user_id)
            )
            conn.commit()

    return {"message": "Payment verified — you're in"}


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
    """Manual fallback - admin pastes the requester's hash. Prefer
    /requests/submit-payment (self-service) going forward."""
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


@router.get("/{workspace_id}/members")
async def list_members(workspace_id: str, user=Depends(get_current_user)):
    if not user:
        raise HTTPException(401, "Authentication required")
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute(
                "SELECT id FROM workspace_members WHERE workspace_id = %s AND user_id = %s AND status = 'approved'",
                (workspace_id, user["id"])
            )
            if not c.fetchone():
                raise HTTPException(403, "Not an approved member of this workspace")
            c.execute("""
                SELECT m.user_id, u.name, m.role, m.joined_at
                FROM workspace_members m
                LEFT JOIN users u ON u.id = m.user_id
                WHERE m.workspace_id = %s AND m.status = 'approved'
                ORDER BY m.joined_at ASC
            """, (workspace_id,))
            rows = c.fetchall()
            return [
                {"user_id": r[0], "user_name": r[1] or "Unknown", "role": r[2],
                 "joined_at": r[3].isoformat() if r[3] else None}
                for r in rows
            ]


@router.post("/{workspace_id}/members/{member_user_id}/remove")
async def remove_member(workspace_id: str, member_user_id: str, user=Depends(get_current_user)):
    if not user:
        raise HTTPException(401, "Authentication required")
    with get_db() as conn:
        with conn.cursor() as c:
            if not _is_admin(c, workspace_id, user["id"]):
                raise HTTPException(403, "Only the hub owner or admins can remove members")
            c.execute("SELECT owner_id FROM workspaces WHERE id = %s", (workspace_id,))
            row = c.fetchone()
            if row and row[0] == member_user_id:
                raise HTTPException(400, "Cannot remove the hub owner")
            c.execute(
                "DELETE FROM workspace_members WHERE workspace_id = %s AND user_id = %s AND status = 'approved'",
                (workspace_id, member_user_id)
            )
            if c.rowcount == 0:
                raise HTTPException(404, "Member not found")
            conn.commit()
    return {"message": "Member removed"}


@router.post("/{workspace_id}/leave")
async def leave_workspace(workspace_id: str, user=Depends(get_current_user)):
    if not user:
        raise HTTPException(401, "Authentication required")
    user_id = user["id"]
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute("SELECT owner_id FROM workspaces WHERE id = %s", (workspace_id,))
            row = c.fetchone()
            if not row:
                raise HTTPException(404, "Workspace not found")
            if row[0] == user_id:
                raise HTTPException(400, "The hub owner can't leave. Remove the hub or transfer ownership instead.")
            c.execute(
                "DELETE FROM workspace_members WHERE workspace_id = %s AND user_id = %s",
                (workspace_id, user_id)
            )
            if c.rowcount == 0:
                raise HTTPException(404, "You're not a member of this workspace")
            conn.commit()
    return {"message": "Left workspace"}


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
                SELECT id, user_id, content, is_ai, created_at, edited_at
                FROM workspace_messages
                WHERE workspace_id = %s AND deleted_at IS NULL
                ORDER BY created_at ASC
                LIMIT %s
            """, (workspace_id, limit))
            rows = c.fetchall()

            # Batched name lookup instead of one query per message - also
            # naturally excludes NULL (AI-authored) user_ids from the IN
            # clause, so those fall through to the is_ai branch below
            # rather than a failed per-row lookup that used to read as
            # "Unknown".
            user_ids = {row[1] for row in rows if row[1] is not None}
            names_by_id = {}
            if user_ids:
                c.execute("SELECT id, name FROM users WHERE id = ANY(%s)", (list(user_ids),))
                names_by_id = {r[0]: r[1] for r in c.fetchall()}

            messages = []
            for row in rows:
                is_ai_msg = row[3]
                if is_ai_msg:
                    display_name = "OS AI"
                else:
                    display_name = names_by_id.get(row[1], "Unknown")
                messages.append({
                    "id": row[0], "user_id": row[1],
                    "user_name": display_name,
                    "content": row[2], "is_ai": row[3],
                    "created_at": row[4].isoformat() if row[4] else None,
                    "edited_at": row[5].isoformat() if row[5] else None,
                })
            return messages


OSAI_MENTION_RE = re.compile(r"@osai\b", re.IGNORECASE)


def _reply_as_osai(workspace_id: str, user: dict, question: str) -> dict | None:
    """
    Mirrors chat.py's non-streaming request handler exactly: atomic
    balance check + deduct, call_ai_model, then burn-or-refund on
    completion - same cost (BURN_PER_MESSAGE, tier-adjusted), same
    memory/web-search context, charged to the asker. Posts the reply as
    a real workspace_messages row (is_ai=TRUE, user_id=NULL - the column
    allows NULL) visible to the whole hub, not just the asker.

    Returns the AI message dict to include in the response, or None if
    the asker had insufficient balance (silently skipped rather than
    blocking their own message from sending - see call site).
    """
    from app.api.v1.chat import burn_with_staking_split

    user_id = user["id"]
    wallet_address = user.get("wallet_address")
    effective_burn = get_effective_burn_amount(wallet_address, settings.BURN_PER_MESSAGE)
    burn_tx_id = str(uuid.uuid4())

    with get_db() as conn:
        with conn.cursor() as c:
            c.execute(
                "UPDATE users SET close_balance = close_balance - %s WHERE id = %s AND close_balance >= %s",
                (effective_burn, user_id, effective_burn)
            )
            if c.rowcount == 0:
                return None
            c.execute("""
                INSERT INTO close_transactions (id, user_id, type, amount, status)
                VALUES (%s, %s, %s, %s, %s)
            """, (burn_tx_id, user_id, "burn", effective_burn, "pending"))
            conn.commit()

    memory_context = ""
    try:
        memory_context = get_memories(user_id, question, settings.MEMORY_RETRIEVAL_LIMIT)
    except Exception as e:
        logger.error(f"Memory retrieval failed (hub @osai): {e}")

    web_results = ""
    if any(kw in question.lower() for kw in [
        "latest", "today", "news", "current", "recent", "now", "who is",
        "who's", "president", "prime minister", "ceo", "governor", "election",
        "price of", "exchange rate", "stock price", "score", "won", "winner",
        "this year", "this week", "right now", "still", "update", "happened"
    ]):
        try:
            web_results = search_web(question)
        except Exception as e:
            logger.error(f"Web search failed (hub @osai): {e}")

    system_prompt = build_system_prompt(question, user, memory_context, web_results)
    messages_for_ai = [{"role": "system", "content": system_prompt}, {"role": "user", "content": question}]

    ai_success = False
    response_text = ""
    try:
        response_text, model_used = call_ai_model(messages_for_ai, user_id, None, tier=user.get("stake_tier", "guest"))
        ai_success = True
    except Exception as e:
        logger.error(f"AI call failed (hub @osai): {e}\n{traceback.format_exc()}")
        response_text = "I'm sorry, I encountered an error. Please try again later."

    msg_id = str(uuid.uuid4())
    with get_db() as conn:
        with conn.cursor() as c:
            if ai_success:
                c.execute("""
                    INSERT INTO workspace_messages (id, workspace_id, user_id, content, is_ai)
                    VALUES (%s, %s, %s, %s, TRUE)
                """, (msg_id, workspace_id, None, response_text))
                try:
                    tx_hash = burn_with_staking_split(effective_burn, msg_id, c)
                    c.execute(
                        "UPDATE close_transactions SET status = 'completed', tx_hash = %s WHERE id = %s",
                        (tx_hash, burn_tx_id)
                    )
                except Exception as e:
                    logger.error(f"On-chain burn failed (hub @osai): {e}")
                    c.execute("UPDATE users SET close_balance = close_balance + %s WHERE id = %s", (effective_burn, user_id))
                    c.execute(
                        "UPDATE close_transactions SET status = 'failed', tx_hash = 'burn_error' WHERE id = %s",
                        (burn_tx_id,)
                    )
            else:
                c.execute("UPDATE users SET close_balance = close_balance + %s WHERE id = %s", (effective_burn, user_id))
                c.execute("UPDATE close_transactions SET status = 'failed' WHERE id = %s", (burn_tx_id,))
            conn.commit()

    return {"id": msg_id, "content": response_text, "user_id": None, "user_name": "OS AI", "is_ai": True}


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

    result = {"id": msg_id, "content": content, "user_id": user["id"], "user_name": user.get("name", "User")}

    if OSAI_MENTION_RE.search(content):
        ai_message = _reply_as_osai(workspace_id, user, content)
        if ai_message:
            result = {"message": result, "ai_message": ai_message}
        else:
            result = {"message": result, "ai_message": None, "ai_error": "Insufficient CLOSE balance to ask OS AI."}

    return result


@router.put("/{workspace_id}/message/{message_id}")
async def edit_workspace_message(
    workspace_id: str,
    message_id: str,
    content: str = Body(..., embed=True),
    user=Depends(get_current_user)
):
    if not user:
        raise HTTPException(401, "Authentication required")
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute(
                "SELECT user_id FROM workspace_messages WHERE id = %s AND workspace_id = %s AND deleted_at IS NULL",
                (message_id, workspace_id)
            )
            row = c.fetchone()
            if not row:
                raise HTTPException(404, "Message not found")
            if row[0] != user["id"]:
                raise HTTPException(403, "You can only edit your own messages")
            c.execute(
                "UPDATE workspace_messages SET content = %s, edited_at = NOW() WHERE id = %s",
                (content, message_id)
            )
            conn.commit()
    return {"message": "Edited"}


@router.delete("/{workspace_id}/message/{message_id}")
async def delete_workspace_message(workspace_id: str, message_id: str, user=Depends(get_current_user)):
    if not user:
        raise HTTPException(401, "Authentication required")
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute(
                "SELECT user_id FROM workspace_messages WHERE id = %s AND workspace_id = %s AND deleted_at IS NULL",
                (message_id, workspace_id)
            )
            row = c.fetchone()
            if not row:
                raise HTTPException(404, "Message not found")
            is_owner_of_msg = row[0] == user["id"]
            is_ws_admin = _is_admin(c, workspace_id, user["id"])
            if not is_owner_of_msg and not is_ws_admin:
                raise HTTPException(403, "You can only delete your own messages")
            c.execute("UPDATE workspace_messages SET deleted_at = NOW() WHERE id = %s", (message_id,))
            conn.commit()
    return {"message": "Deleted"}
