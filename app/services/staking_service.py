"""
CLOSE staking. Database-backed (not yet a smart contract - see
CloseStaking ABI already referenced in blockchain.py for the eventual
on-chain version). Users pay real CLOSE to a treasury address to open a
stake position; yield accrues at a locked-in APY and is paid out from that
same treasury on claim.

TEMPORARY: currently reusing the Hustle Hub treasury address for testing,
per 2026-08-21 decision - NOT yet a dedicated staking wallet. Swap
STAKING_TREASURY_ADDRESS below once the dedicated wallet exists.
"""
import logging
import uuid
from datetime import timedelta
from eth_utils import to_checksum_address
from app.services.blockchain import get_web3, send_close_from_wallet
from app.core.database import get_db
from app.core.config import settings
from app.services.wallet_service import _decrypt_private_key

logger = logging.getLogger(__name__)

CLOSE_TOKEN_ADDRESS = "0x3c6833cFDdED80fE76474a3Cb2Cc050Daec91fe8"
CLOSE_DECIMALS = 18
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
CHAIN = "polygon"

# Dedicated staking treasury wallet, generated 2026-08-21. Address is
# public/non-secret; the encrypted private key and password live only in
# Render's environment (STAKING_TREASURY_ENCRYPTED_KEY, STAKING_TREASURY_PASSWORD),
# never in source control.
STAKING_TREASURY_ADDRESS = "0x4CfBFeD3Dd360664d6e24eC5511C87498248CC0A"


def _get_treasury_private_key() -> str:
    """Decrypts the treasury's private key on demand, never cached or
    logged. Both STAKING_TREASURY_ENCRYPTED_KEY and STAKING_TREASURY_PASSWORD
    live only in Render's environment."""
    return _decrypt_private_key(settings.STAKING_TREASURY_ENCRYPTED_KEY, settings.STAKING_TREASURY_PASSWORD)

TERMS = {
    "flexible": {"apy": 3, "lock_days": 0},
    "30d": {"apy": 6, "lock_days": 30},
    "90d": {"apy": 10, "lock_days": 90},
    "180d": {"apy": 16, "lock_days": 180},
}

TIER_THRESHOLDS = [
    # (min_amount, min_lock_days, tier) - checked in order, first match wins.
    # Founder tier is granted separately via is_founder, not stake-driven.
    (100000, 180, "platinum"),
    (10000, 90, "gold"),
    (0, 0, "bronze"),
]


def verify_stake_payment(user_id: str, tx_hash: str, expected_amount: int) -> dict:
    """Same verification pattern as workspace_payment_service.py: confirms
    a real CLOSE transfer from the user's own wallet to the treasury, for
    exactly the expected amount, and that this tx_hash hasn't been used
    before (stake_positions.stake_tx_hash is UNIQUE)."""
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute("SELECT id FROM stake_positions WHERE stake_tx_hash = %s", (tx_hash,))
            if c.fetchone():
                raise ValueError("This transaction has already been used to open a stake")

            c.execute("SELECT wallet_address FROM users WHERE id = %s", (user_id,))
            row = c.fetchone()
            if not row or not row[0]:
                raise ValueError("No wallet found for this account")
            user_wallet = to_checksum_address(row[0])

    web3 = get_web3(CHAIN)
    treasury = to_checksum_address(STAKING_TREASURY_ADDRESS)
    close_contract = to_checksum_address(CLOSE_TOKEN_ADDRESS)

    receipt = web3.eth.get_transaction_receipt(tx_hash)
    if receipt.status != 1:
        raise ValueError("Transaction failed on-chain")

    tx = web3.eth.get_transaction(tx_hash)
    tx_from = to_checksum_address(tx["from"])
    if tx_from != user_wallet:
        raise ValueError("This transaction was not sent from your own wallet")

    matched_amount = None
    for log in receipt.logs:
        if to_checksum_address(log["address"]) != close_contract:
            continue
        if len(log["topics"]) < 3:
            continue
        if log["topics"][0].hex() != TRANSFER_TOPIC:
            continue
        from_addr = to_checksum_address("0x" + log["topics"][1].hex()[-40:])
        to_addr = to_checksum_address("0x" + log["topics"][2].hex()[-40:])
        if from_addr != user_wallet or to_addr != treasury:
            continue
        raw_amount = int(log["data"], 16) if isinstance(log["data"], str) else int.from_bytes(log["data"], "big")
        matched_amount = raw_amount / (10 ** CLOSE_DECIMALS)
        break

    if matched_amount is None:
        raise ValueError("No qualifying CLOSE transfer to the staking treasury found in this transaction")

    if abs(matched_amount - expected_amount) > 0.0001:
        raise ValueError(f"Expected exactly {expected_amount} CLOSE, but this transaction sent {matched_amount}")

    return {"verified_amount": expected_amount}


def create_stake(user_id: str, amount: int, term: str, tx_hash: str) -> dict:
    if term not in TERMS:
        raise ValueError(f"Invalid term: {term}")
    if amount <= 0:
        raise ValueError("Amount must be greater than 0")

    verify_stake_payment(user_id, tx_hash, amount)

    term_info = TERMS[term]
    stake_id = str(uuid.uuid4())
    unlock_at = None

    with get_db() as conn:
        with conn.cursor() as c:
            if term_info["lock_days"] > 0:
                c.execute(
                    "SELECT NOW() + INTERVAL '%s days'" % term_info["lock_days"]
                )
                unlock_at = c.fetchone()[0]

            c.execute("""
                INSERT INTO stake_positions (id, user_id, amount, term, apy, unlock_at, stake_tx_hash)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (stake_id, user_id, amount, term, term_info["apy"], unlock_at, tx_hash))

            c.execute(
                "UPDATE users SET close_staked = close_staked + %s WHERE id = %s",
                (amount, user_id)
            )
            conn.commit()

    _recalculate_stake_tier(user_id)

    logger.info(f"Stake created: user={user_id} amount={amount} term={term} tx={tx_hash}")
    return {"id": stake_id, "amount": amount, "term": term, "apy": term_info["apy"], "unlock_at": unlock_at.isoformat() if unlock_at else None}


def _calculate_yield(position) -> float:
    """position is a tuple: (id, amount, term, apy, staked_at, unlock_at, status, unstaked_at, yield_claimed)"""
    _, amount, term, apy, staked_at, unlock_at, status, unstaked_at, yield_claimed = position
    if status != "active":
        return 0.0

    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    staked_at = staked_at.replace(tzinfo=timezone.utc) if staked_at.tzinfo is None else staked_at

    end_time = now
    if unlock_at:
        unlock_at_aware = unlock_at.replace(tzinfo=timezone.utc) if unlock_at.tzinfo is None else unlock_at
        end_time = min(now, unlock_at_aware)

    days_elapsed = max(0, (end_time - staked_at).total_seconds() / 86400)
    total_yield = float(amount) * (float(apy) / 100) * (days_elapsed / 365)
    return max(0, total_yield - (yield_claimed or 0))


def list_stakes(user_id: str) -> list:
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute("""
                SELECT id, amount, term, apy, staked_at, unlock_at, status, unstaked_at, yield_claimed
                FROM stake_positions WHERE user_id = %s ORDER BY staked_at DESC
            """, (user_id,))
            rows = c.fetchall()
            return [
                {
                    "id": r[0], "amount": r[1], "term": r[2], "apy": float(r[3]),
                    "staked_at": r[4].isoformat() if r[4] else None,
                    "unlock_at": r[5].isoformat() if r[5] else None,
                    "status": r[6],
                    "unstaked_at": r[7].isoformat() if r[7] else None,
                    "yield_claimed": r[8] or 0,
                    "pending_yield": round(_calculate_yield(r), 4),
                }
                for r in rows
            ]


def claim_yield(user_id: str, stake_id: str) -> dict:
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute("""
                SELECT id, amount, term, apy, staked_at, unlock_at, status, unstaked_at, yield_claimed
                FROM stake_positions WHERE id = %s AND user_id = %s
            """, (stake_id, user_id))
            row = c.fetchone()
            if not row:
                raise ValueError("Stake position not found")

            pending = _calculate_yield(row)
            if pending <= 0:
                raise ValueError("No yield available to claim yet")
            pending_int = int(pending)
            if pending_int <= 0:
                raise ValueError("Accrued yield is less than 1 CLOSE - keep waiting")

            c.execute("SELECT wallet_address FROM users WHERE id = %s", (user_id,))
            wallet_row = c.fetchone()
            if not wallet_row or not wallet_row[0]:
                raise ValueError("No wallet found for this account")
            user_wallet = wallet_row[0]

    # Real on-chain payout: send pending_int CLOSE from the treasury
    # wallet to the user's own wallet. Done outside the DB transaction
    # above (connection already closed) so a slow/failed broadcast can't
    # hold a DB lock open.
    treasury_key = _get_treasury_private_key()
    tx_hash = send_close_from_wallet(
        from_address=STAKING_TREASURY_ADDRESS,
        from_private_key=treasury_key,
        to_address=user_wallet,
        amount=pending_int,
    )

    with get_db() as conn:
        with conn.cursor() as c:
            c.execute(
                "UPDATE stake_positions SET yield_claimed = yield_claimed + %s WHERE id = %s",
                (pending_int, stake_id)
            )
            conn.commit()

    logger.info(f"Yield claimed: user={user_id} stake={stake_id} amount={pending_int} tx={tx_hash}")
    return {"claimed": pending_int, "tx_hash": tx_hash}


def unstake(user_id: str, stake_id: str) -> dict:
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute("""
                SELECT id, amount, term, apy, staked_at, unlock_at, status, unstaked_at, yield_claimed
                FROM stake_positions WHERE id = %s AND user_id = %s
            """, (stake_id, user_id))
            row = c.fetchone()
            if not row:
                raise ValueError("Stake position not found")
            if row[6] != "active":
                raise ValueError("This stake is not active")

            amount = row[1]
            unlock_at = row[5]

            from datetime import datetime, timezone
            now = datetime.now(timezone.utc)
            is_early = False
            if unlock_at:
                unlock_at_aware = unlock_at.replace(tzinfo=timezone.utc) if unlock_at.tzinfo is None else unlock_at
                is_early = now < unlock_at_aware

            forfeited_yield = 0
            if is_early:
                # Early unstake from a fixed term forfeits unclaimed yield -
                # only principal is returned. Prevents gaming flexible-rate
                # arbitrage against fixed terms.
                forfeited_yield = _calculate_yield(row)

            c.execute("SELECT wallet_address FROM users WHERE id = %s", (user_id,))
            wallet_row = c.fetchone()
            if not wallet_row or not wallet_row[0]:
                raise ValueError("No wallet found for this account")
            user_wallet = wallet_row[0]

    # Real on-chain payout: return the principal from the treasury to the
    # user's own wallet, same pattern as claim_yield above.
    treasury_key = _get_treasury_private_key()
    tx_hash = send_close_from_wallet(
        from_address=STAKING_TREASURY_ADDRESS,
        from_private_key=treasury_key,
        to_address=user_wallet,
        amount=amount,
    )

    with get_db() as conn:
        with conn.cursor() as c:
            c.execute(
                "UPDATE users SET close_staked = close_staked - %s WHERE id = %s",
                (amount, user_id)
            )
            c.execute(
                "UPDATE stake_positions SET status = 'unstaked', unstaked_at = NOW() WHERE id = %s",
                (stake_id,)
            )
            conn.commit()

    _recalculate_stake_tier(user_id)

    logger.info(f"Unstaked: user={user_id} stake={stake_id} amount={amount} early={is_early} forfeited_yield={forfeited_yield:.2f} tx={tx_hash}")
    return {"returned": amount, "early": is_early, "forfeited_yield": round(forfeited_yield, 4), "tx_hash": tx_hash}


def _recalculate_stake_tier(user_id: str):
    """Recomputes stake_tier from all currently-active stake positions.
    Never downgrades a founder - is_founder governs that tier separately."""
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute("SELECT is_founder FROM users WHERE id = %s", (user_id,))
            row = c.fetchone()
            if row and row[0]:
                return  # founders keep their tier regardless of staking

            c.execute("""
                SELECT COALESCE(SUM(amount), 0),
                       COALESCE(MAX(CASE
                           WHEN term = 'flexible' THEN 0
                           WHEN term = '30d' THEN 30
                           WHEN term = '90d' THEN 90
                           WHEN term = '180d' THEN 180
                           ELSE 0 END), 0)
                FROM stake_positions WHERE user_id = %s AND status = 'active'
            """, (user_id,))
            total_staked, max_lock_days = c.fetchone()

            new_tier = "builder"
            for min_amount, min_lock, tier in TIER_THRESHOLDS:
                if total_staked >= min_amount and max_lock_days >= min_lock:
                    new_tier = tier
                    break

            c.execute("UPDATE users SET stake_tier = %s WHERE id = %s", (new_tier, user_id))
            conn.commit()
