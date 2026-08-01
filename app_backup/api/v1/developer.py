from fastapi import APIRouter, Depends, HTTPException, Request
from app.core.security import get_current_user, hash_password, verify_password
from app.core.database import get_db
from app.core.config import settings
import uuid
import secrets
import string
import bcrypt
import json
import logging
from datetime import datetime, timedelta

router = APIRouter(prefix="/developer", tags=["Developer"])
logger = logging.getLogger(__name__)

def generate_api_key():
    prefix = ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(8))
    secret = ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(32))
    return f"{prefix}_{secret}"

@router.post("/api-key")
async def create_api_key(req: dict, user=Depends(get_current_user)):
    if not user:
        raise HTTPException(401, "Authentication required")
    label = req.get("label", "Unlabelled")
    scopes = req.get("scopes", "chat,research,portfolio")
    api_key = generate_api_key()
    prefix = api_key.split("_")[0]
    key_hash = bcrypt.hashpw(api_key.encode(), bcrypt.gensalt()).decode()
    
    key_id = str(uuid.uuid4())
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute("""
                INSERT INTO api_keys (id, user_id, key_hash, prefix, label, scopes, is_active)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (key_id, user["id"], key_hash, prefix, label, scopes, True))
            conn.commit()
    return {"id": key_id, "api_key": api_key, "label": label, "scopes": scopes}

@router.get("/api-keys")
async def list_api_keys(user=Depends(get_current_user)):
    if not user:
        raise HTTPException(401, "Authentication required")
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute("""
                SELECT id, label, scopes, is_active, last_used, created_at
                FROM api_keys
                WHERE user_id = %s
                ORDER BY created_at DESC
            """, (user["id"],))
            rows = c.fetchall()
            return [
                {
                    "id": row[0],
                    "label": row[1],
                    "scopes": row[2],
                    "is_active": row[3],
                    "last_used": row[4].isoformat() if row[4] else None,
                    "created_at": row[5].isoformat()
                }
                for row in rows
            ]

@router.delete("/api-key/{key_id}")
async def delete_api_key(key_id: str, user=Depends(get_current_user)):
    if not user:
        raise HTTPException(401, "Authentication required")
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute("DELETE FROM api_keys WHERE id = %s AND user_id = %s", (key_id, user["id"]))
            if c.rowcount == 0:
                raise HTTPException(404, "API key not found")
            conn.commit()
    return {"message": "Deleted"}

@router.post("/webhook")
async def create_webhook(req: dict, user=Depends(get_current_user)):
    if not user:
        raise HTTPException(401, "Authentication required")
    url = req.get("url")
    events = req.get("events", "new_message")
    if not url:
        raise HTTPException(400, "URL required")
    hook_id = str(uuid.uuid4())
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute("""
                INSERT INTO webhooks (id, user_id, url, events, is_active)
                VALUES (%s, %s, %s, %s, %s)
            """, (hook_id, user["id"], url, events, True))
            conn.commit()
    return {"id": hook_id, "url": url, "events": events}

@router.get("/webhooks")
async def list_webhooks(user=Depends(get_current_user)):
    if not user:
        raise HTTPException(401, "Authentication required")
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute("""
                SELECT id, url, events, is_active, created_at
                FROM webhooks
                WHERE user_id = %s
                ORDER BY created_at DESC
            """, (user["id"],))
            rows = c.fetchall()
            return [
                {
                    "id": row[0],
                    "url": row[1],
                    "events": row[2],
                    "is_active": row[3],
                    "created_at": row[4].isoformat()
                }
                for row in rows
            ]

@router.delete("/webhook/{hook_id}")
async def delete_webhook(hook_id: str, user=Depends(get_current_user)):
    if not user:
        raise HTTPException(401, "Authentication required")
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute("DELETE FROM webhooks WHERE id = %s AND user_id = %s", (hook_id, user["id"]))
            if c.rowcount == 0:
                raise HTTPException(404, "Webhook not found")
            conn.commit()
    return {"message": "Deleted"}