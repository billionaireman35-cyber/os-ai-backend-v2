from fastapi import APIRouter, Depends, HTTPException, Body, Query
from app.core.security import get_current_user
from app.core.database import get_db
import uuid, logging
from datetime import datetime, timezone

router = APIRouter()
logger = logging.getLogger(__name__)

@router.post("/create")
async def create_workspace(
    name: str = Body(...),
    description: str = Body(""),
    is_public: bool = Body(True),
    user=Depends(get_current_user)
):
    if not user:
        raise HTTPException(401, "Authentication required")
    room_code = ''.join(uuid.uuid4().hex[:8].upper())
    workspace_id = str(uuid.uuid4())
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute("""
                INSERT INTO workspaces (id, name, description, room_code, owner_id, is_public)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (workspace_id, name, description, room_code, user["id"], is_public))
            # Add owner as member with role 'admin'
            c.execute("""
                INSERT INTO workspace_members (workspace_id, user_id, role)
                VALUES (%s, %s, %s)
            """, (workspace_id, user["id"], "admin"))
            conn.commit()
    return {
        "id": workspace_id,
        "name": name,
        "description": description,
        "room_code": room_code,
        "owner_id": user["id"],
        "is_public": is_public,
        "members": [{"user_id": user["id"], "role": "admin"}]
    }

@router.get("/list")
async def list_workspaces(user=Depends(get_current_user)):
    if not user:
        raise HTTPException(401, "Authentication required")
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute("""
                SELECT w.id, w.name, w.description, w.room_code, w.owner_id, w.is_public, w.created_at,
                       (SELECT COUNT(*) FROM workspace_members WHERE workspace_id = w.id) as member_count
                FROM workspaces w
                LEFT JOIN workspace_members m ON w.id = m.workspace_id
                WHERE w.is_public = TRUE OR m.user_id = %s
                GROUP BY w.id
                ORDER BY w.created_at DESC
            """, (user["id"],))
            rows = c.fetchall()
            return [
                {
                    "id": row[0],
                    "name": row[1],
                    "description": row[2],
                    "room_code": row[3],
                    "owner_id": row[4],
                    "is_public": row[5],
                    "created_at": row[6].isoformat() if row[6] else None,
                    "member_count": row[7],
                    "is_member": True  # since we filtered
                }
                for row in rows
            ]

@router.post("/join")
async def join_workspace(
    room_code: str = Body(...),
    user=Depends(get_current_user)
):
    if not user:
        raise HTTPException(401, "Authentication required")
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute("SELECT id FROM workspaces WHERE room_code = %s", (room_code.upper(),))
            row = c.fetchone()
            if not row:
                raise HTTPException(404, "Workspace not found")
            workspace_id = row[0]
            # Check if already member
            c.execute("SELECT user_id FROM workspace_members WHERE workspace_id = %s AND user_id = %s", (workspace_id, user["id"]))
            if c.fetchone():
                return {"message": "Already a member"}
            c.execute("INSERT INTO workspace_members (workspace_id, user_id, role) VALUES (%s, %s, %s)", (workspace_id, user["id"], "member"))
            conn.commit()
    return {"message": "Joined workspace"}

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
            # Check membership
            c.execute("SELECT user_id FROM workspace_members WHERE workspace_id = %s AND user_id = %s", (workspace_id, user["id"]))
            if not c.fetchone():
                raise HTTPException(403, "Not a member of this workspace")
            c.execute("""
                SELECT id, user_id, content, is_ai, created_at
                FROM workspace_messages
                WHERE workspace_id = %s
                ORDER BY created_at ASC
                LIMIT %s
            """, (workspace_id, limit))
            rows = c.fetchall()
            # Also fetch user names
            messages = []
            for row in rows:
                c.execute("SELECT name FROM users WHERE id = %s", (row[1],))
                user_row = c.fetchone()
                messages.append({
                    "id": row[0],
                    "user_id": row[1],
                    "user_name": user_row[0] if user_row else "Unknown",
                    "content": row[2],
                    "is_ai": row[3],
                    "created_at": row[4].isoformat() if row[4] else None
                })
            return messages

@router.post("/{workspace_id}/message")
async def send_workspace_message(
    workspace_id: str,
    content: str = Body(...),
    user=Depends(get_current_user)
):
    if not user:
        raise HTTPException(401, "Authentication required")
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute("SELECT user_id FROM workspace_members WHERE workspace_id = %s AND user_id = %s", (workspace_id, user["id"]))
            if not c.fetchone():
                raise HTTPException(403, "Not a member of this workspace")
            msg_id = str(uuid.uuid4())
            c.execute("""
                INSERT INTO workspace_messages (id, workspace_id, user_id, content)
                VALUES (%s, %s, %s, %s)
            """, (msg_id, workspace_id, user["id"], content))
            conn.commit()
    return {"id": msg_id, "content": content, "user_id": user["id"], "user_name": user.get("name", "User")}
