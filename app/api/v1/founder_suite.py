"""
Founder oversight endpoints - platform-wide views across all users, not
scoped to the requesting user. Every endpoint here requires is_founder=TRUE.
Never returns password_hash or wallet_encrypted_seed under any circumstance.
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from app.core.security import get_current_user
from app.core.database import get_db
import logging

router = APIRouter()
logger = logging.getLogger(__name__)


def _require_founder(user):
    if not user or not user.get("is_founder"):
        raise HTTPException(403, "Founder access required")


@router.get("/users")
async def list_all_users(
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    user=Depends(get_current_user)
):
    _require_founder(user)
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute("""
                SELECT id, email, name, close_balance, close_staked, stake_tier,
                       wallet_address, is_founder, fingerprint_verified,
                       last_active, created_at
                FROM users
                ORDER BY created_at DESC
                LIMIT %s OFFSET %s
            """, (limit, offset))
            rows = c.fetchall()
            c.execute("SELECT COUNT(*) FROM users")
            total = c.fetchone()[0]
            return {
                "total": total,
                "users": [
                    {
                        "id": r[0], "email": r[1], "name": r[2],
                        "close_balance": r[3], "close_staked": r[4],
                        "stake_tier": r[5], "wallet_address": r[6],
                        "is_founder": r[7], "fingerprint_verified": r[8],
                        "last_active": r[9].isoformat() if r[9] else None,
                        "created_at": r[10].isoformat() if r[10] else None,
                    }
                    for r in rows
                ]
            }


@router.get("/workspaces")
async def list_all_workspaces(
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    user=Depends(get_current_user)
):
    _require_founder(user)
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute("""
                SELECT w.id, w.name, w.description, w.room_code, w.owner_id,
                       u.email, w.is_public, w.created_at,
                       (SELECT COUNT(*) FROM workspace_members WHERE workspace_id = w.id AND status = 'approved') as approved_count,
                       (SELECT COUNT(*) FROM workspace_members WHERE workspace_id = w.id AND status = 'pending') as pending_count
                FROM workspaces w
                LEFT JOIN users u ON u.id = w.owner_id
                ORDER BY w.created_at DESC
                LIMIT %s OFFSET %s
            """, (limit, offset))
            rows = c.fetchall()
            c.execute("SELECT COUNT(*) FROM workspaces")
            total = c.fetchone()[0]
            return {
                "total": total,
                "workspaces": [
                    {
                        "id": r[0], "name": r[1], "description": r[2],
                        "room_code": r[3], "owner_id": r[4], "owner_email": r[5],
                        "is_public": r[6],
                        "created_at": r[7].isoformat() if r[7] else None,
                        "approved_members": r[8], "pending_requests": r[9],
                    }
                    for r in rows
                ]
            }


@router.get("/transactions")
async def list_all_transactions(
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    user=Depends(get_current_user)
):
    """
    Unified platform-wide transaction feed across close_transactions,
    crypto_deposits, workspace_payments, and chat_topups - the sources of
    real CLOSE movement built tonight. Sorted by most recent first.
    """
    _require_founder(user)
    with get_db() as conn:
        with conn.cursor() as c:
            history = []

            c.execute("""
                SELECT ct.id, ct.user_id, u.email, ct.type, ct.amount, ct.status, ct.tx_hash, ct.created
                FROM close_transactions ct
                LEFT JOIN users u ON u.id = ct.user_id
                ORDER BY ct.created DESC
                LIMIT %s
            """, (limit,))
            for r in c.fetchall():
                history.append({
                    "source": "close_transactions", "id": r[0], "user_id": r[1],
                    "user_email": r[2], "kind": r[3], "amount": float(r[4]) if r[4] is not None else None,
                    "status": r[5], "tx_hash": r[6],
                    "created": r[7].isoformat() if r[7] else None,
                })

            c.execute("""
                SELECT cd.id, cd.user_id, u.email, cd.chain, cd.token_symbol, cd.amount,
                       cd.close_credited, cd.tx_hash, cd.created
                FROM crypto_deposits cd
                LEFT JOIN users u ON u.id = cd.user_id
                ORDER BY cd.created DESC
                LIMIT %s
            """, (limit,))
            for r in c.fetchall():
                history.append({
                    "source": "crypto_deposits", "id": r[0], "user_id": r[1],
                    "user_email": r[2], "kind": "deposit", "chain": r[3],
                    "token_symbol": r[4], "amount": float(r[5]) if r[5] is not None else None,
                    "close_credited": r[6], "tx_hash": r[7],
                    "created": r[8].isoformat() if r[8] else None,
                })

            c.execute("""
                SELECT wp.id, wp.user_id, u.email, wp.workspace_id, wp.tx_hash,
                       wp.purpose, wp.amount, wp.status, wp.created
                FROM workspace_payments wp
                LEFT JOIN users u ON u.id = wp.user_id
                ORDER BY wp.created DESC
                LIMIT %s
            """, (limit,))
            for r in c.fetchall():
                history.append({
                    "source": "workspace_payments", "id": r[0], "user_id": r[1],
                    "user_email": r[2], "workspace_id": r[3], "tx_hash": r[4],
                    "kind": f"workspace_{r[5]}", "amount": float(r[6]) if r[6] is not None else None,
                    "status": r[7], "created": r[8].isoformat() if r[8] else None,
                })

            c.execute("""
                SELECT ctu.id, ctu.user_id, u.email, ctu.tx_hash, ctu.amount, ctu.status, ctu.created
                FROM chat_topups ctu
                LEFT JOIN users u ON u.id = ctu.user_id
                ORDER BY ctu.created DESC
                LIMIT %s
            """, (limit,))
            for r in c.fetchall():
                history.append({
                    "source": "chat_topups", "id": r[0], "user_id": r[1],
                    "user_email": r[2], "kind": "chat_topup", "tx_hash": r[3],
                    "amount": float(r[4]) if r[4] is not None else None,
                    "status": r[5], "created": r[6].isoformat() if r[6] else None,
                })

    history.sort(key=lambda x: x["created"] or "", reverse=True)
    return {"total": len(history), "transactions": history[offset:offset + limit]}


@router.get("/staking-treasury")
async def staking_treasury_overview(
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    user=Depends(get_current_user)
):
    """
    Founder solvency check for the staking system: how much CLOSE actually
    sits in the treasury wallet on-chain, vs. how much the treasury owes
    (total staked principal + total accrued-but-unclaimed yield across
    every active position). If the on-chain balance is less than what's
    owed, the treasury cannot currently cover a full claim/unstake wave -
    that's surfaced explicitly as `solvent: false` rather than left for
    the founder to calculate by eye.
    """
    _require_founder(user)

    from app.services.blockchain import get_token_balance
    from app.services.staking_service import (
        STAKING_TREASURY_ADDRESS, CLOSE_TOKEN_ADDRESS, CHAIN, _calculate_yield
    )

    try:
        treasury_balance = get_token_balance(CHAIN, CLOSE_TOKEN_ADDRESS, STAKING_TREASURY_ADDRESS)
    except Exception as e:
        logger.error(f"Failed to fetch staking treasury on-chain balance: {e}")
        treasury_balance = None

    with get_db() as conn:
        with conn.cursor() as c:
            c.execute("""
                SELECT sp.id, sp.user_id, u.email, sp.amount, sp.term, sp.apy,
                       sp.staked_at, sp.unlock_at, sp.status, sp.unstaked_at, sp.yield_claimed
                FROM stake_positions sp
                LEFT JOIN users u ON u.id = sp.user_id
                WHERE sp.status = \'active\'
                ORDER BY sp.staked_at DESC
            """)
            rows = c.fetchall()

    total_staked = 0
    total_pending_yield = 0.0
    positions = []
    for r in rows:
        (pos_id, uid, email, amount, term, apy, staked_at, unlock_at,
         status, unstaked_at, yield_claimed) = r
        pending = _calculate_yield(
            (pos_id, amount, term, apy, staked_at, unlock_at, status, unstaked_at, yield_claimed)
        )
        total_staked += amount
        total_pending_yield += pending
        positions.append({
            "id": pos_id, "user_id": uid, "user_email": email,
            "amount": amount, "term": term, "apy": float(apy),
            "staked_at": staked_at.isoformat() if staked_at else None,
            "unlock_at": unlock_at.isoformat() if unlock_at else None,
            "pending_yield": round(pending, 4),
        })

    total_owed = total_staked + total_pending_yield
    solvent = treasury_balance is not None and treasury_balance >= total_owed

    return {
        "treasury_address": STAKING_TREASURY_ADDRESS,
        "treasury_balance": treasury_balance,
        "total_staked": total_staked,
        "total_pending_yield": round(total_pending_yield, 4),
        "total_owed": round(total_owed, 4),
        "solvent": solvent,
        "active_position_count": len(positions),
        "positions": positions[offset:offset + limit],
    }
