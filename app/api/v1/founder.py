from fastapi import APIRouter, Depends, HTTPException, Body
from app.core.security import get_current_user, create_token
from app.core.config import settings
import logging

router = APIRouter()
logger = logging.getLogger(__name__)

@router.post("/")
async def founder_login(
    code: dict = Body(...),
    user=Depends(get_current_user)
):
    # If user is already authenticated, just return them
    if user:
        return {"message": "Already authenticated", "user": user}
    # Otherwise, validate founder key
    if code.get("code") == settings.FOUNDER_KEY:
        # In practice, we might want to create a special founder session
        # For now, we'll return a token for the founder user if they exist,
        # or we can create a temporary founder session.
        # But since we don't have a founder user ID here, we need to fetch or create one.
        # We'll use the founder@closeai.io user.
        from app.core.database import get_db
        from app.core.security import hash_password, now_utc
        import uuid
        from datetime import timedelta

        email = "founder@closeai.io"
        with get_db() as conn:
            with conn.cursor() as c:
                c.execute("SELECT id FROM users WHERE email = %s", (email,))
                row = c.fetchone()
                if not row:
                    # Create founder user if not exists
                    user_id = str(uuid.uuid4())
                    hashed = hash_password("Founder@123")  # default password
                    c.execute("""
                        INSERT INTO users (id, email, password_hash, name, is_founder)
                        VALUES (%s, %s, %s, %s, %s)
                    """, (user_id, email, hashed, "Founder", True))
                    conn.commit()
                    user_id = user_id
                else:
                    user_id = row[0]
                token = create_token(user_id)
                # Store session
                c.execute("""
                    INSERT INTO user_sessions (user_id, token, expires_at)
                    VALUES (%s, %s, %s)
                """, (user_id, token, now_utc() + timedelta(days=30)))
                conn.commit()
        return {"token": token, "user": {"id": user_id, "email": email, "is_founder": True}}
    else:
        raise HTTPException(401, "Invalid founder key")

@router.post("/add-close")
async def add_close(
    amount: int = Body(..., embed=True),
    user=Depends(get_current_user)
):
    if not user or not user.get("is_founder"):
        raise HTTPException(403, "Only founders can add CLOSE")
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute("UPDATE users SET close_balance = close_balance + %s WHERE id = %s", (amount, user["id"]))
            conn.commit()
    return {"message": f"Added {amount} CLOSE"}
