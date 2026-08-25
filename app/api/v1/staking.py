from fastapi import APIRouter, Depends, HTTPException, Body
from app.core.security import get_current_user
from app.services.staking_service import create_stake, list_stakes, claim_yield, unstake, TERMS, STAKING_TREASURY_ADDRESS
import logging

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/terms")
async def get_terms():
    """Public info: available lock terms, their APY, and the treasury
    address to send CLOSE to when opening a stake."""
    return {
        "terms": TERMS,
        "treasury_address": STAKING_TREASURY_ADDRESS,
    }


@router.get("/positions")
async def get_positions(user=Depends(get_current_user)):
    if not user:
        raise HTTPException(401, "Authentication required")
    return {"positions": list_stakes(user["id"])}


@router.post("/stake")
async def stake(
    amount: int = Body(...),
    term: str = Body(...),
    tx_hash: str = Body(...),
    user=Depends(get_current_user)
):
    """
    Opens a new stake position. The user must have already sent `amount`
    CLOSE to the treasury address (from GET /terms) from their own wallet,
    and submits the resulting tx_hash here for verification - same
    pay-then-verify pattern as Hustle Hub and chat top-ups.
    """
    if not user:
        raise HTTPException(401, "Authentication required")
    try:
        result = create_stake(user["id"], amount, term, tx_hash)
        return result
    except ValueError as e:
        raise HTTPException(402, str(e))


@router.post("/claim")
async def claim(
    stake_id: str = Body(..., embed=True),
    user=Depends(get_current_user)
):
    if not user:
        raise HTTPException(401, "Authentication required")
    try:
        return claim_yield(user["id"], stake_id)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/unstake")
async def unstake_position(
    stake_id: str = Body(..., embed=True),
    user=Depends(get_current_user)
):
    if not user:
        raise HTTPException(401, "Authentication required")
    try:
        return unstake(user["id"], stake_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
