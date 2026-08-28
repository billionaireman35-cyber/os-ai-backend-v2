import bcrypt
import hmac
import json
import base64
import hashlib
from datetime import datetime, timedelta, timezone
from fastapi import Request
from app.core.config import settings

def now_utc():
    return datetime.now(timezone.utc)

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode()) if hashed else False

def create_token(user_id) -> str:
    # Ensure user_id is a string (in case it's a UUID object)
    user_id = str(user_id)
    header = base64.urlsafe_b64encode(
        json.dumps({"alg": "HS256", "typ": "JWT"}).encode()
    ).decode().rstrip("=")
    payload = base64.urlsafe_b64encode(
        json.dumps({
            "user_id": user_id,
            "type": "user",
            "exp": int((now_utc() + timedelta(days=30)).timestamp())
        }).encode()
    ).decode().rstrip("=")
    sig = base64.urlsafe_b64encode(
        hmac.new(settings.JWT_SECRET.encode(), f"{header}.{payload}".encode(), hashlib.sha256).digest()
    ).decode().rstrip("=")
    return f"{header}.{payload}.{sig}"

def verify_token(token: str):
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        header, payload, signature = parts
        expected = base64.urlsafe_b64encode(
            hmac.new(settings.JWT_SECRET.encode(), f"{header}.{payload}".encode(), hashlib.sha256).digest()
        ).decode().rstrip("=")
        if not hmac.compare_digest(signature, expected):
            return None
        data = json.loads(base64.urlsafe_b64decode(payload + "=="))
        if data.get("exp", 0) < now_utc().timestamp():
            return None
        return data
    except:
        return None

def get_current_user(request: Request):
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    token = auth[7:]
    payload = verify_token(token)
    if not payload or payload.get("type") != "user":
        return None
    user_id = payload.get("user_id")
    if not user_id:
        return None
    from app.core.database import get_db
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute("""
                SELECT id, email, name, close_balance, close_staked, stake_tier,
                       wallet_address, wallet_encrypted_seed, is_founder, device_fingerprint,
                       preferred_currency
                FROM users WHERE id = %s
            """, (user_id,))
            row = c.fetchone()
            if not row:
                return None
            return {
                "id": row[0],
                "email": row[1],
                "name": row[2] or row[1].split('@')[0],
                "close_balance": row[3] or 0,
                "close_staked": row[4] or 0,
                "stake_tier": row[5] or "none",
                "wallet_address": row[6] or "",
                "encrypted_seed": row[7] or "",
                "is_founder": row[8] or False,
                "device_fingerprint": row[9],
                "preferred_currency": row[10] or "USD"
            }
