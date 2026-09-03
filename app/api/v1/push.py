from fastapi import APIRouter, Depends, HTTPException, Body
from app.core.security import get_current_user
from app.core.database import get_db
from app.core.config import settings
import uuid

router = APIRouter()

@router.post("/subscribe")
async def subscribe_push(
    subscription: dict = Body(...),
    user=Depends(get_current_user)
):
    if not user:
        raise HTTPException(401, "Authentication required")
    endpoint = subscription.get("endpoint")
    auth_key = subscription.get("keys", {}).get("auth")
    p256dh_key = subscription.get("keys", {}).get("p256dh")
    if not endpoint or not auth_key or not p256dh_key:
        raise HTTPException(400, "Invalid subscription")
    with get_db() as conn:
        with conn.cursor() as c:
            # Remove old subscription for this endpoint if exists
            c.execute("DELETE FROM push_subscriptions WHERE endpoint = %s", (endpoint,))
            c.execute("""
                INSERT INTO push_subscriptions (id, user_id, endpoint, auth_key, p256dh_key)
                VALUES (%s, %s, %s, %s, %s)
            """, (str(uuid.uuid4()), user["id"], endpoint, auth_key, p256dh_key))
            conn.commit()
    return {"message": "Subscribed"}

@router.delete("/unsubscribe")
async def unsubscribe_push(
    endpoint: str = Body(...),
    user=Depends(get_current_user)
):
    if not user:
        raise HTTPException(401, "Authentication required")
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute("DELETE FROM push_subscriptions WHERE endpoint = %s AND user_id = %s", (endpoint, user["id"]))
            conn.commit()
    return {"message": "Unsubscribed"}

@router.get("/vapid-public-key")
async def get_vapid_public_key():
    if not settings.VAPID_PUBLIC_KEY:
        raise HTTPException(503, "Push notifications are not configured")
    return {"publicKey": settings.VAPID_PUBLIC_KEY}
