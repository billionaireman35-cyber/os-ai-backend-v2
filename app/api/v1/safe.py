from fastapi import APIRouter, Depends, HTTPException, Body
from app.core.security import get_current_user
from app.services.safe_service import create_safe, list_safes, get_safe_balance, propose_safe_transaction, sign_safe_transaction, execute_safe_transaction, list_pending_transactions
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


@router.post("/{safe_id}/propose")
async def propose_safe_transaction_endpoint(
    safe_id: str,
    to_address: str = Body(...),
    value_wei: str = Body("0"),
    data: str = Body("0x"),
    password: str = Body(..., embed=True),
    user=Depends(get_current_user),
):
    if not user:
        raise HTTPException(401, "Authentication required")
    if len(password) < 8:
        raise HTTPException(400, "Invalid password")
    if not to_address:
        raise HTTPException(400, "to_address is required")

    try:
        return propose_safe_transaction(
            safe_id=safe_id,
            user_id=user["id"],
            password=password,
            to_address=to_address,
            value_wei=int(value_wei),
            data=data,
        )
    except ValueError as e:
        msg = str(e)
        if "password" in msg.lower() or "decrypt" in msg.lower():
            raise HTTPException(400, "Incorrect password")
        raise HTTPException(400, msg)
    except Exception as e:
        logger.error(f"Safe propose error: {e}")
        raise HTTPException(500, "Failed to propose transaction")


@router.post("/transactions/{tx_id}/sign")
async def sign_safe_transaction_endpoint(
    tx_id: str,
    password: str = Body(..., embed=True),
    user=Depends(get_current_user),
):
    if not user:
        raise HTTPException(401, "Authentication required")
    if len(password) < 8:
        raise HTTPException(400, "Invalid password")

    try:
        return sign_safe_transaction(tx_id, user["id"], password)
    except ValueError as e:
        msg = str(e)
        if "password" in msg.lower() or "decrypt" in msg.lower():
            raise HTTPException(400, "Incorrect password")
        raise HTTPException(400, msg)
    except Exception as e:
        logger.error(f"Safe sign error: {e}")
        raise HTTPException(500, "Failed to sign transaction")


@router.post("/transactions/{tx_id}/execute")
async def execute_safe_transaction_endpoint(
    tx_id: str,
    password: str = Body(..., embed=True),
    user=Depends(get_current_user),
):
    if not user:
        raise HTTPException(401, "Authentication required")
    if len(password) < 8:
        raise HTTPException(400, "Invalid password")

    try:
        return execute_safe_transaction(tx_id, user["id"], password)
    except ValueError as e:
        msg = str(e)
        if "password" in msg.lower() or "decrypt" in msg.lower():
            raise HTTPException(400, "Incorrect password")
        raise HTTPException(400, msg)
    except Exception as e:
        logger.error(f"Safe execute error: {e}")
        raise HTTPException(500, "Failed to execute transaction")


@router.get("/{safe_id}/transactions")
async def list_pending_transactions_endpoint(safe_id: str, user=Depends(get_current_user)):
    if not user:
        raise HTTPException(401, "Authentication required")
    try:
        return list_pending_transactions(safe_id, user["id"])
    except ValueError as e:
        raise HTTPException(404, str(e))
    except Exception as e:
        logger.error(f"Safe list transactions error: {e}")
        raise HTTPException(500, "Failed to list transactions")
