from fastapi import APIRouter, Depends, HTTPException, Body
from app.core.security import get_current_user
from app.services.safe_service import create_safe, list_safes, get_safe_balance
import logging

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/create")
async def create_safe_endpoint(
    chain: str = Body(...),
    owners: list = Body(...),
    threshold: int = Body(...),
    password: str = Body(..., embed=True),
    label: str = Body("Safe"),
    wallet_address: str = Body(None),
    user=Depends(get_current_user),
):
    if not user:
        raise HTTPException(401, "Authentication required")
    if len(password) < 8:
        raise HTTPException(400, "Invalid password")
    if not owners:
        raise HTTPException(400, "At least one owner is required")
    if threshold < 1 or threshold > len(owners):
        raise HTTPException(400, f"Threshold must be between 1 and {len(owners)}")

    try:
        result = create_safe(
            user_id=user["id"],
            password=password,
            chain=chain,
            owners=owners,
            threshold=threshold,
            label=label,
            wallet_address=wallet_address,
        )
        return result
    except ValueError as e:
        msg = str(e)
        if "password" in msg.lower() or "decrypt" in msg.lower():
            raise HTTPException(400, "Incorrect password")
        raise HTTPException(400, msg)
    except Exception as e:
        logger.error(f"Safe creation error: {e}")
        raise HTTPException(500, "Safe creation failed")


@router.get("/list")
async def list_safes_endpoint(user=Depends(get_current_user)):
    if not user:
        raise HTTPException(401, "Authentication required")
    try:
        return list_safes(user["id"])
    except Exception as e:
        logger.error(f"Safe list error: {e}")
        raise HTTPException(500, "Failed to list safes")


@router.get("/{safe_id}/balance")
async def get_safe_balance_endpoint(safe_id: str, user=Depends(get_current_user)):
    if not user:
        raise HTTPException(401, "Authentication required")
    try:
        return get_safe_balance(safe_id, user["id"])
    except ValueError as e:
        raise HTTPException(404, str(e))
    except Exception as e:
        logger.error(f"Safe balance error: {e}")
        raise HTTPException(500, "Failed to fetch Safe balance")
