from fastapi import APIRouter, Depends, HTTPException, Body
from app.core.security import get_current_user
from app.services.wallet_service import import_wallet_from_private_key, import_wallet_from_mnemonic
import logging
import uuid
from eth_utils import to_checksum_address, is_address

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


@router.post("/connected")
async def import_connected_wallet(
    address: str = Body(...),
    label: str = Body("Connected"),
    chain: str = Body("polygon"),
    user=Depends(get_current_user)
):
    """
    Records a wallet connected via WalletConnect/AppKit - no private key
    is ever sent to or held by this backend. encrypted_key is left NULL
    and wallet_type is 'connected', distinguishing it from custodial
    (password-encrypted) wallets. Signing for a connected wallet happens
    entirely client-side via the wallet's own provider - this endpoint
    only makes the address visible for balance/history lookups, the same
    ownership-checked way custodial imported wallets already are.
    """
    if not user:
        raise HTTPException(401, "Authentication required")
    if not is_address(address):
        raise HTTPException(400, "Invalid wallet address")
    checksummed = to_checksum_address(address)

    from app.core.database import get_db
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute(
                "SELECT id FROM os_wallets WHERE user_id = %s AND address = %s",
                (user["id"], checksummed)
            )
            if c.fetchone():
                raise HTTPException(400, f"This wallet ({checksummed}) is already added to your account.")

            wallet_id = str(uuid.uuid4())
            c.execute("""
                INSERT INTO os_wallets (id, user_id, chain, address, encrypted_key, label, wallet_type)
                VALUES (%s, %s, %s, %s, NULL, %s, 'connected')
            """, (wallet_id, user["id"], chain, checksummed, label))
            conn.commit()

    return {"success": True, "id": wallet_id, "address": checksummed, "label": label, "chain": chain, "wallet_type": "connected"}


@router.get("/list")
async def list_imported_wallets(user=Depends(get_current_user)):
    if not user:
        raise HTTPException(401, "Authentication required")

    from app.core.database import get_db
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute("""
                SELECT id, chain, address, label, is_active, created_at, wallet_type
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
                    "wallet_type": row[6] or "custodial",
                }
                for row in rows
            ]
