from fastapi import APIRouter, Depends, HTTPException, Body
from app.core.security import get_current_user
from app.services.wallet_service import import_wallet_from_private_key, import_wallet_from_mnemonic
import logging

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/private-key")
async def import_private_key(
    private_key: str = Body(...),
    password: str = Body(...),
    label: str = Body("Imported"),
    user=Depends(get_current_user)
):
    if not user:
        raise HTTPException(401, "Authentication required")
    if len(password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters")

    try:
        result = import_wallet_from_private_key(
            user_id=user["id"],
            private_key_hex=private_key,
            password=password,
            label=label,
        )
        return {"success": True, **result}
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        logger.error(f"Private key import failed: {e}")
        raise HTTPException(500, "Wallet import failed. Please try again.")


@router.post("/mnemonic")
async def import_mnemonic(
    mnemonic_phrase: str = Body(...),
    password: str = Body(...),
    label: str = Body("Imported"),
    passphrase: str = Body(""),
    user=Depends(get_current_user)
):
    if not user:
        raise HTTPException(401, "Authentication required")
    if len(password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters")

    try:
        result = import_wallet_from_mnemonic(
            user_id=user["id"],
            mnemonic_phrase=mnemonic_phrase,
            password=password,
            label=label,
            passphrase=passphrase,
        )
        return {"success": True, **result}
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        logger.error(f"Mnemonic import failed: {e}")
        raise HTTPException(500, "Wallet import failed. Please try again.")


@router.get("/list")
async def list_imported_wallets(user=Depends(get_current_user)):
    if not user:
        raise HTTPException(401, "Authentication required")

    from app.core.database import get_db
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute("""
                SELECT id, chain, address, label, is_active, created_at
                FROM os_wallets
                WHERE user_id = %s
                ORDER BY created_at ASC
            """, (user["id"],))
            rows = c.fetchall()
            return [
                {
                    "id": row[0],
                    "chain": row[1],
                    "address": row[2],
                    "label": row[3],
                    "is_active": row[4],
                    "created_at": row[5].isoformat() if row[5] else None,
                }
                for row in rows
            ]
