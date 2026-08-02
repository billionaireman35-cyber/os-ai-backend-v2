from fastapi import APIRouter, Depends, HTTPException, Request, BackgroundTasks
from app.models.schemas import SendCodeRequest, VerifyCodeRequest, RegisterRequest, LoginRequest
from app.core.database import get_db
from app.core.security import create_token, verify_password, hash_password, now_utc, get_current_user
from app.services.email import send_verification_email
import re, uuid, hmac, secrets, string, asyncio, logging
from datetime import timedelta

router = APIRouter()
logger = logging.getLogger(__name__)

@router.post("/send-code")
async def send_verification_code(req: SendCodeRequest, background_tasks: BackgroundTasks):
    email = req.email.strip()
    if not re.match(r'^[^@]+@[^@]+\.[^@]+$', email):
        raise HTTPException(400, "Valid email required")
    alphabet = string.ascii_uppercase + string.digits
    code = ''.join(secrets.choice(alphabet) for _ in range(6))
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute("""
                INSERT INTO verification_codes (email, code, purpose, expires_at)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (email, purpose) DO UPDATE
                SET code = EXCLUDED.code, expires_at = EXCLUDED.expires_at, attempts = 0
            """, (email, code, req.purpose, now_utc() + timedelta(minutes=15)))
            conn.commit()
    background_tasks.add_task(send_verification_email, email, code, req.purpose)
    return {"sent": True, "message": "Verification code sent", "expires_in": 900}

@router.post("/verify-code")
async def verify_code(req: VerifyCodeRequest):
    email = req.email.strip()
    code = req.code.strip().upper()
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute("SELECT code, attempts, expires_at FROM verification_codes WHERE email = %s AND purpose = %s", (email, req.purpose))
            row = c.fetchone()
            if not row:
                raise HTTPException(400, "Invalid or expired code")
            stored_code, attempts, expires_at = row
            if expires_at < now_utc():
                c.execute("DELETE FROM verification_codes WHERE email = %s AND purpose = %s", (email, req.purpose))
                conn.commit()
                raise HTTPException(400, "Code expired. Request a new one.")
            if attempts >= 5:
                c.execute("DELETE FROM verification_codes WHERE email = %s AND purpose = %s", (email, req.purpose))
                conn.commit()
                raise HTTPException(400, "Too many failed attempts. Request a new code.")
            if not hmac.compare_digest(stored_code, code):
                c.execute("UPDATE verification_codes SET attempts = attempts + 1 WHERE email = %s AND purpose = %s", (email, req.purpose))
                conn.commit()
                await asyncio.sleep(min(2 ** (attempts + 1), 10))
                raise HTTPException(400, "Invalid verification code")
            c.execute("DELETE FROM verification_codes WHERE email = %s AND purpose = %s", (email, req.purpose))
            conn.commit()
            return {"verified": True}

@router.post("/register")
async def register(req: RegisterRequest):
    if len(req.password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters")
    with get_db() as conn:
        with conn.cursor() as c:
            # Proper verification code validation
            c.execute(
                "SELECT code, expires_at FROM verification_codes WHERE email = %s AND purpose = 'verification'",
                (req.email,)
            )
            row = c.fetchone()
            if not row:
                raise HTTPException(400, "Invalid or expired verification code")
            stored_code, expires_at = row
            if expires_at < now_utc():
                raise HTTPException(400, "Verification code expired. Request a new one.")
            if not hmac.compare_digest(stored_code, req.verification_code):
                raise HTTPException(400, "Invalid verification code")
            # Delete the code after successful use
            c.execute("DELETE FROM verification_codes WHERE email = %s AND purpose = 'verification'", (req.email,))
            conn.commit()

            c.execute("SELECT id FROM users WHERE email = %s", (req.email,))
            if c.fetchone():
                raise HTTPException(400, "Email already registered")
            user_id = str(uuid.uuid4())
            name = req.name or req.email.split('@')[0]
            c.execute("""
                INSERT INTO users (id, email, password_hash, name, device_fingerprint)
                VALUES (%s, %s, %s, %s, %s)
            """, (user_id, req.email, hash_password(req.password), name, req.fingerprint))
            token = create_token(user_id)
            c.execute("INSERT INTO user_sessions (user_id, token, expires_at) VALUES (%s, %s, %s)",
                      (user_id, token, now_utc() + timedelta(days=30)))
            conn.commit()
            return {
                "token": token,
                "user": {
                    "id": user_id,
                    "email": req.email,
                    "name": name,
                    "close_balance": 0,
                    "close_staked": 0,
                    "stake_tier": "none",
                    "is_founder": False
                }
            }

@router.post("/login")
async def login(req: LoginRequest):
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute("SELECT id, email, password_hash, name, close_balance, close_staked, stake_tier, is_founder FROM users WHERE email = %s", (req.email,))
            user = c.fetchone()
            if not user or not verify_password(req.password, user[2]):
                raise HTTPException(401, "Invalid credentials")
            user_id, email, _, name, close_balance, close_staked, stake_tier, is_founder = user
            if is_founder:
                raise HTTPException(403, "Founder account must use the founder login portal")
            if req.fingerprint:
                c.execute("UPDATE users SET device_fingerprint = %s, fingerprint_verified = TRUE WHERE id = %s", (req.fingerprint, user_id))
            token = create_token(user_id)
            c.execute("INSERT INTO user_sessions (user_id, token, expires_at) VALUES (%s, %s, %s)",
                      (user_id, token, now_utc() + timedelta(days=30)))
            conn.commit()
            return {
                "token": token,
                "user": {
                    "id": user_id,
                    "email": email,
                    "name": name or email.split('@')[0],
                    "close_balance": close_balance or 0,
                    "close_staked": close_staked or 0,
                    "stake_tier": stake_tier or "none",
                    "is_founder": is_founder
                }
            }

@router.post("/logout")
async def logout(request: Request):
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        with get_db() as conn:
            with conn.cursor() as c:
                c.execute("DELETE FROM user_sessions WHERE token = %s", (auth[7:],))
                conn.commit()
    return {"message": "Logged out"}

@router.get("/me")
async def get_me(user=Depends(get_current_user)):
    if not user:
        raise HTTPException(401, "Not authenticated")
    return user

@router.post("/forgot-password")
async def forgot_password(req: dict, background_tasks: BackgroundTasks):
    email = req.get("email", "").strip()
    if not email:
        raise HTTPException(400, "Email required")
    alphabet = string.ascii_uppercase + string.digits
    code = ''.join(secrets.choice(alphabet) for _ in range(6))
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute("SELECT id FROM users WHERE email = %s", (email,))
            if c.fetchone():
                c.execute("""
                    INSERT INTO verification_codes (email, code, purpose, expires_at)
                    VALUES (%s, %s, 'password_reset', %s)
                    ON CONFLICT (email, purpose) DO UPDATE
                    SET code = EXCLUDED.code, expires_at = EXCLUDED.expires_at, attempts = 0
                """, (email, code, now_utc() + timedelta(minutes=15)))
                conn.commit()
                background_tasks.add_task(send_verification_email, email, code, "password_reset")
    return {"message": "If the account exists, a reset code has been sent."}

@router.post("/reset-password")
async def reset_password(req: dict):
    email = req.get("email", "").strip()
    code = req.get("code", "").strip().upper()
    new_password = req.get("new_password", "")
    if len(new_password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters")
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute("SELECT code, expires_at FROM verification_codes WHERE email = %s AND purpose='password_reset' AND expires_at > NOW()", (email,))
            row = c.fetchone()
            if not row or not hmac.compare_digest(row[0], code):
                raise HTTPException(400, "Invalid or expired reset code")
            c.execute("UPDATE users SET password_hash = %s WHERE email = %s", (hash_password(new_password), email))
            c.execute("DELETE FROM verification_codes WHERE email = %s AND purpose='password_reset'", (email,))
            c.execute("DELETE FROM user_sessions WHERE user_id IN (SELECT id FROM users WHERE email = %s)", (email,))
            conn.commit()
    return {"message": "Password reset successfully"}

@router.put("/update-profile")
async def update_profile(req: dict, user=Depends(get_current_user)):
    if not user:
        raise HTTPException(401, "Not authenticated")
    name = req.get("name")
    if not name:
        raise HTTPException(400, "Name required")
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute("UPDATE users SET name = %s, updated_at = NOW() WHERE id = %s", (name, user["id"]))
            conn.commit()
            c.execute("SELECT id, email, name, close_balance, close_staked, stake_tier, wallet_address, is_founder FROM users WHERE id = %s", (user["id"],))
            row = c.fetchone()
            updated_user = {
                "id": row[0],
                "email": row[1],
                "name": row[2] or row[1].split('@')[0],
                "close_balance": row[3] or 0,
                "close_staked": row[4] or 0,
                "stake_tier": row[5] or "none",
                "wallet_address": row[6] or "",
                "is_founder": row[7] or False,
            }
            return {"user": updated_user}
