from fastapi import APIRouter, Depends, HTTPException, Body
from app.core.security import get_current_user
from app.core.config import settings
from app.core.database import get_db
import logging

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/")
async def founder_login(
    code: str = Body(..., embed=True),
    user=Depends(get_current_user)
):
    """
    Elevates the CURRENTLY LOGGED-IN user to founder status, if the correct
    founder key is provided. Requires an existing valid session - this is
    not a separate login, it's a privilege upgrade for the account you're
    already authenticated as.
    """
    if not user:
        raise HTTPException(401, "You must be logged in to your account before entering the founder key")

    if code != settings.FOUNDER_KEY:
        raise HTTPException(401, "Invalid founder key")

    with get_db() as conn:
        with conn.cursor() as c:
            c.execute("UPDATE users SET is_founder = TRUE WHERE id = %s", (user["id"],))
            conn.commit()

    logger.info(f"User {user['id']} elevated to founder status")
    return {"message": "Founder status granted", "user": {**user, "is_founder": True}}


@router.get("/_debug_key_shape")
async def _debug_key_shape():
    """
    TEMPORARY diagnostic - reveals only the length and first/last character
    of settings.FOUNDER_KEY, never the full value. Remove this endpoint
    once the founder-key mismatch investigation is resolved.
    """
    key = settings.FOUNDER_KEY or ""
    if not key:
        return {"error": "FOUNDER_KEY is empty or not set on the server"}
    return {
        "length": len(key),
        "first_char": key[0],
        "last_char": key[-1],
        "has_leading_whitespace": key != key.lstrip(),
        "has_trailing_whitespace": key != key.rstrip(),
    }


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
