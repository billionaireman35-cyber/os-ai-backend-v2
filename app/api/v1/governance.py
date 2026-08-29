from fastapi import APIRouter, Depends, HTTPException, Body
from app.core.security import get_current_user
from app.services.governance_service import (
    create_proposal, vote, get_proposal, list_proposals, get_my_voting_power,
    MIN_STAKED_TO_PROPOSE, VOTING_PERIOD_DAYS, QUORUM_PERCENT
)
import logging

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/params")
async def get_params():
    """Public info: the thresholds governing proposals, so the frontend
    can explain requirements before a user tries to create one."""
    return {
        "min_staked_to_propose": MIN_STAKED_TO_PROPOSE,
        "voting_period_days": VOTING_PERIOD_DAYS,
        "quorum_percent": QUORUM_PERCENT,
    }


@router.get("/proposals")
async def get_proposals(user=Depends(get_current_user)):
    if not user:
        raise HTTPException(401, "Authentication required")
    return {"proposals": list_proposals()}


@router.get("/proposals/{proposal_id}")
async def get_single_proposal(proposal_id: str, user=Depends(get_current_user)):
    if not user:
        raise HTTPException(401, "Authentication required")
    try:
        return get_proposal(proposal_id)
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.get("/proposals/{proposal_id}/my-power")
async def get_voting_power(proposal_id: str, user=Depends(get_current_user)):
    if not user:
        raise HTTPException(401, "Authentication required")
    return get_my_voting_power(user["id"], proposal_id)


@router.post("/proposals")
async def create(
    title: str = Body(...),
    description: str = Body(...),
    user=Depends(get_current_user)
):
    """
    Creates a new governance proposal. Requires at least
    MIN_STAKED_TO_PROPOSE CLOSE actively staked - checked against
    current stake_positions at call time.
    """
    if not user:
        raise HTTPException(401, "Authentication required")
    try:
        return create_proposal(user["id"], title, description)
    except ValueError as e:
        raise HTTPException(402, str(e))


@router.post("/proposals/{proposal_id}/vote")
async def cast_vote(
    proposal_id: str,
    support: str = Body(..., embed=True),
    user=Depends(get_current_user)
):
    if not user:
        raise HTTPException(401, "Authentication required")
    try:
        return vote(user["id"], proposal_id, support)
    except ValueError as e:
        raise HTTPException(400, str(e))
