from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from app.core.security import get_current_user, hash_password, verify_password
from app.core.database import get_db
from app.services.blockchain import burn_close_onchain, broadcast_signed_transaction
from app.services.ai import call_ai_model
from app.core.config import settings
import uuid
import json
import secrets
import string
import logging
from datetime import datetime, timedelta

router = APIRouter(prefix="/workspace", tags=["Hustle Hub"])
logger = logging.getLogger(__name__)

HUB_FEE = 1000  # CLOSE tokens

def generate_room_code():
    return ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(6))

@router.post("/create")
async def create_workspace(req: dict, user=Depends(get_current_user)):
    if not user:
        raise HTTPException(401, "Authentication required")
    
    name = req.get("name", "My Hustle Hub")
    description = req.get("description", "")
    password = req.get("password", "")
    is_public = req.get("is_public", True)
    
    # Check balance
    close_balance = user.get("close_balance", 0)
    if close_balance < HUB_FEE:
        raise HTTPException(400, f"Insufficient CLOSE balance. Need {HUB_FEE} CLOSE to create a Hub.")
    
    room_code = generate_room_code()
    workspace_id = str(uuid.uuid4())
    
    # Deduct CLOSE optimistically
    with get_db() as conn:
        with conn.cursor() as c:
            # Check if user already owns a workspace with this name
            c.execute("SELECT id FROM workspaces WHERE owner_id = %s AND name = %s AND status != 'deleted'", (user["id"], name))
            if c.fetchone():
                raise HTTPException(400, "Hub with this name already exists")
            
            # Create workspace with pending status
            c.execute("""
                INSERT INTO workspaces (id, name, description, room_code, password_hash, owner_id, is_public, status, fee_paid)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (workspace_id, name, description, room_code, hash_password(password) if password else None, user["id"], is_public, "pending", False))
            
            # Add owner as member with pending status
            c.execute("""
                INSERT INTO workspace_members (workspace_id, user_id, role, status)
                VALUES (%s, %s, %s, %s)
            """, (workspace_id, user["id"], "owner", "active"))  # owner doesn't pay
            
            # Deduct CLOSE balance
            c.execute("UPDATE users SET close_balance = close_balance - %s WHERE id = %s", (HUB_FEE, user["id"]))
            
            # Insert transaction
            c.execute("""
                INSERT INTO close_transactions (id, user_id, type, amount, status, reference_id)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (str(uuid.uuid4()), user["id"], "hub_create", HUB_FEE, "pending", workspace_id))
            
            conn.commit()
    
    # Return burn payload for frontend
    return {
        "workspace_id": workspace_id,
        "name": name,
        "room_code": room_code,
        "message": "Hub created. Please sign the burn transaction to activate it.",
        "burn_payload": {
            "contract": settings.CLOSE_CONTRACT_ADDRESS,
            "amount": HUB_FEE,
            "chain": "polygon",
            "reference_id": workspace_id,
            "action": "hub_create"
        }
    }

@router.post("/join")
async def join_workspace(req: dict, user=Depends(get_current_user)):
    if not user:
        raise HTTPException(401, "Authentication required")
    
    room_code = req.get("room_code", "").upper().strip()
    password = req.get("password", "")
    
    # Check balance
    close_balance = user.get("close_balance", 0)
    if close_balance < HUB_FEE:
        raise HTTPException(400, f"Insufficient CLOSE balance. Need {HUB_FEE} CLOSE to join a Hub.")
    
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute("""
                SELECT id, name, password_hash, owner_id, is_public, status
                FROM workspaces
                WHERE room_code = %s AND status != 'deleted'
            """, (room_code,))
            row = c.fetchone()
            if not row:
                raise HTTPException(404, "Hub not found")
            
            workspace_id, workspace_name, password_hash, owner_id, is_public, workspace_status = row
            
            if workspace_status != "active":
                raise HTTPException(400, "Hub is not active yet")
            
            # Verify password if set
            if password_hash and not verify_password(password, password_hash):
                raise HTTPException(403, "Incorrect password")
            
            # Check if already member
            c.execute("SELECT 1 FROM workspace_members WHERE workspace_id = %s AND user_id = %s", (workspace_id, user["id"]))
            if c.fetchone():
                raise HTTPException(400, "Already a member")
            
            # Deduct CLOSE
            c.execute("UPDATE users SET close_balance = close_balance - %s WHERE id = %s", (HUB_FEE, user["id"]))
            
            # Add member with pending status
            c.execute("""
                INSERT INTO workspace_members (workspace_id, user_id, role, status)
                VALUES (%s, %s, %s, %s)
            """, (workspace_id, user["id"], "member", "pending"))
            
            # Insert transaction
            membership_id = str(uuid.uuid4())
            c.execute("""
                INSERT INTO close_transactions (id, user_id, type, amount, status, reference_id)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (membership_id, user["id"], "hub_join", HUB_FEE, "pending", workspace_id))
            
            conn.commit()
    
    return {
        "workspace_id": workspace_id,
        "workspace_name": workspace_name,
        "message": "Joined Hub. Please sign the burn transaction to activate membership.",
        "burn_payload": {
            "contract": settings.CLOSE_CONTRACT_ADDRESS,
            "amount": HUB_FEE,
            "chain": "polygon",
            "reference_id": workspace_id,
            "action": "hub_join",
            "user_id": user["id"]
        }
    }