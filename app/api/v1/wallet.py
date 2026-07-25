from fastapi import APIRouter, Depends, HTTPException
from app.core.security import get_current_user
from app.services.safe import create_safe, propose_safe_transaction, list_safes_for_user
from app.core.database import get_db
import uuid
import logging

router = APIRouter(prefix="/safe", tags=["Safe"])

@router.post("/create")
async def deploy_safe(req: dict, user=Depends(get_current_user)):
    if not user:
        raise HTTPException(401, "Authentication required")
    owners = req.get("owners", [])
    threshold = req.get("threshold", 1)
    chain = req.get("chain", "polygon")
    if not owners or threshold < 1 or threshold > len(owners):
        raise HTTPException(400, "Invalid owners or threshold")
    # Deploy Safe
    try:
        safe_address = create_safe(owners, threshold, chain)
        with get_db() as conn:
            with conn.cursor() as c:
                c.execute("""
                    INSERT INTO user_safes (id, user_id, safe_address, chain, owners, threshold)
                    VALUES (%s, %s, %s, %s, %s, %s)
                """, (str(uuid.uuid4()), user["id"], safe_address, chain, json.dumps(owners), threshold))
                conn.commit()
        return {"safe_address": safe_address, "chain": chain}
    except Exception as e:
        logger.error(f"Safe creation failed: {e}")
        raise HTTPException(500, "Safe creation failed")

@router.get("/list")
async def list_safes(user=Depends(get_current_user)):
    if not user:
        raise HTTPException(401, "Authentication required")
    safes = list_safes_for_user(user["id"])
    return {"safes": safes}

@router.post("/propose")
async def propose(req: dict, user=Depends(get_current_user)):
    if not user:
        raise HTTPException(401, "Authentication required")
    safe_address = req.get("safe_address")
    to = req.get("to")
    value = req.get("value", 0)
    data = req.get("data", "0x")
    chain = req.get("chain", "polygon")
    # Verify user owns this Safe
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute("SELECT 1 FROM user_safes WHERE safe_address = %s AND user_id = %s", (safe_address, user["id"]))
            if not c.fetchone():
                raise HTTPException(403, "You do not own this Safe")
    # Create proposal (we'll store in pending_intents table later)
    proposal = propose_safe_transaction(safe_address, to, value, data, chain)
    return proposal