from fastapi import APIRouter, Depends, HTTPException, Query, Body
from app.core.security import get_current_user
from app.services.wallet_service import (
    get_user_balance,
    get_user_transactions,
    create_wallet_for_user,
    send_transaction,
    get_user_private_key
)
from app.services.blockchain import burn_close
from app.services.deposit_service import verify_and_credit_deposit
import uuid as uuid_lib
from app.core.database import get_db
import uuid
import logging

router = APIRouter()
logger = logging.getLogger(__name__)

wallet_router = router

@router.get("/balance")
async def get_balance(currency: str = Query(None), user=Depends(get_current_user)):
    if not user:
        raise HTTPException(401, "Authentication required")
    try:
        # Explicit ?currency= wins; otherwise fall back to the user's
        # saved preference, defaulting to USD if neither is set.
        target_currency = currency or user.get("preferred_currency") or "USD"
        balances = get_user_balance(user["id"], currency=target_currency)
        return {"balances": balances}
    except Exception as e:
        logger.error(f"Balance fetch failed: {e}")
        raise HTTPException(500, "Failed to fetch balances")


@router.put("/preferred-currency")
async def set_preferred_currency(
    currency: str = Body(..., embed=True),
    user=Depends(get_current_user)
):
    if not user:
        raise HTTPException(401, "Authentication required")
    from app.services.fx_service import get_supported_fx_currencies
    currency = currency.upper().strip()
    if currency != "USD" and currency not in get_supported_fx_currencies():
        raise HTTPException(400, f"Unsupported currency: {currency}")
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute("UPDATE users SET preferred_currency = %s WHERE id = %s", (currency, user["id"]))
            conn.commit()
    return {"preferred_currency": currency}

@router.get("/transactions")
async def get_transactions(
    limit: int = Query(20, ge=1, le=100),
    user=Depends(get_current_user)
):
    if not user:
        raise HTTPException(401, "Authentication required")
    try:
        txs = get_user_transactions(user["id"], limit)
        return {"transactions": txs}
    except Exception as e:
        logger.error(f"Transactions fetch failed: {e}")
        raise HTTPException(500, "Failed to fetch transactions")

@router.post("/create")
async def create_wallet(
    password: str = Body(..., embed=True),
    user=Depends(get_current_user)
):
    if not user:
        raise HTTPException(401, "Authentication required")
    if len(password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters")
    try:
        result = create_wallet_for_user(user["id"], password)
        return {"wallet": result}
    except Exception as e:
        logger.error(f"Wallet creation failed: {e}")
        raise HTTPException(500, "Failed to create wallet")

# NOTE: create_wallet_for_user() no longer raises on an existing wallet -
# it returns the existing address instead (see wallet_service.py). This
# except block now only fires on genuine failures (DB/chain errors).

@router.post("/export-private-key")
async def export_private_key(
    password: str = Body(..., embed=True),
    user=Depends(get_current_user)
):
    """
    Reveals the user's own private key in raw hex, after re-verifying their
    wallet password. This wallet is non-custodial - the user should always
    be able to export their own key (e.g. to import into MetaMask, or as a
    backup independent of this app). Added 2026-08-20: this capability was
    missing entirely - the key existed encrypted in the DB and could be
    used internally to sign transactions, but was never exposable to its
    own owner.
    """
    if not user:
        raise HTTPException(401, "Authentication required")
    try:
        private_key_hex = get_user_private_key(user["id"], password)
    except Exception:
        raise HTTPException(400, "Incorrect password")

    logger.info(f"User {user['id']} exported their private key")
    return {"private_key": private_key_hex}


@router.post("/burn")
async def burn(
    amount: int = Body(..., embed=True),
    password: str = Body(..., embed=True),
    user=Depends(get_current_user)
):
    if not user:
        raise HTTPException(401, "Authentication required")
    if amount <= 0:
        raise HTTPException(400, "Amount must be greater than 0")

    # Verify the wallet password by attempting the same decrypt used for
    # /send and /sign - a wrong password fails here, same as those routes.
    try:
        get_user_private_key(user["id"], password)
    except Exception:
        raise HTTPException(400, "Incorrect password")

    user_id = user["id"]
    burn_tx_id = str(uuid.uuid4())

    with get_db() as conn:
        with conn.cursor() as c:
            c.execute(
                "UPDATE users SET close_balance = close_balance - %s WHERE id = %s AND close_balance >= %s",
                (amount, user_id, amount)
            )
            if c.rowcount == 0:
                raise HTTPException(402, "Insufficient CLOSE balance")
            c.execute("""
                INSERT INTO close_transactions (id, user_id, type, amount, status)
                VALUES (%s, %s, %s, %s, %s)
            """, (burn_tx_id, user_id, "burn", amount, "pending"))
            conn.commit()

    try:
        tx_hash = burn_close(amount)
        with get_db() as conn:
            with conn.cursor() as c:
                c.execute(
                    "UPDATE close_transactions SET status = %s, tx_hash = %s WHERE id = %s",
                    ("confirmed", tx_hash, burn_tx_id)
                )
                conn.commit()
        return {"tx_hash": tx_hash}
    except Exception as e:
        logger.error(f"Manual burn failed: {e}")
        with get_db() as conn:
            with conn.cursor() as c:
                c.execute(
                    "UPDATE users SET close_balance = close_balance + %s WHERE id = %s",
                    (amount, user_id)
                )
                c.execute(
                    "UPDATE close_transactions SET status = %s WHERE id = %s",
                    ("failed", burn_tx_id)
                )
                conn.commit()
        raise HTTPException(500, "Burn failed on-chain - your balance has been refunded")

@router.post("/deposit/verify")
async def verify_deposit(
    chain: str = Body(..., embed=True),
    tx_hash: str = Body(..., embed=True),
    user=Depends(get_current_user)
):
    if not user:
        raise HTTPException(401, "Authentication required")
    try:
        result = verify_and_credit_deposit(user["id"], chain, tx_hash)
        return {"success": True, **result}
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        logger.error(f"Deposit verification failed: {e}")
        raise HTTPException(500, "Failed to verify deposit")

@router.get("/deposit/info")
async def deposit_info():
    return {
        "address": settings.DEPOSIT_ADDRESS,
        "minimums": {
            "polygon": settings.DEPOSIT_MIN_USD_POLYGON,
            "bsc": settings.DEPOSIT_MIN_USD_BSC,
            "ethereum": settings.DEPOSIT_MIN_USD_ETHEREUM,
        },
        "close_per_usd": settings.CLOSE_PER_USD
    }

@router.post("/withdraw/request")
async def request_withdrawal(
    chain: str = Body(..., embed=True),
    token_symbol: str = Body(..., embed=True),
    amount: float = Body(..., embed=True),
    destination_address: str = Body(..., embed=True),
    password: str = Body(..., embed=True),
    user=Depends(get_current_user)
):
    if not user:
        raise HTTPException(401, "Authentication required")
    if amount <= 0:
        raise HTTPException(400, "Amount must be greater than 0")

    try:
        get_user_private_key(user["id"], password)
    except Exception:
        raise HTTPException(400, "Incorrect password")

    user_id = user["id"]
    request_id = str(uuid_lib.uuid4())

    if token_symbol.upper() == "CLOSE":
        with get_db() as conn:
            with conn.cursor() as c:
                amount_int = int(amount)
                c.execute(
                    "UPDATE users SET close_balance = close_balance - %s WHERE id = %s AND close_balance >= %s",
                    (amount_int, user_id, amount_int)
                )
                if c.rowcount == 0:
                    raise HTTPException(402, "Insufficient CLOSE balance")
                c.execute("""
                    INSERT INTO withdrawal_requests (id, user_id, chain, token_symbol, amount, destination_address)
                    VALUES (%s, %s, %s, %s, %s, %s)
                """, (request_id, user_id, chain, token_symbol, amount_int, destination_address))
                conn.commit()
    else:
        # Non-CLOSE tokens: request is logged but balance isn't held in our
        # DB (it lives on-chain in the user's own wallet), so nothing to
        # debit here - this just creates a trackable withdrawal record.
        with get_db() as conn:
            with conn.cursor() as c:
                c.execute("""
                    INSERT INTO withdrawal_requests (id, user_id, chain, token_symbol, amount, destination_address)
                    VALUES (%s, %s, %s, %s, %s, %s)
                """, (request_id, user_id, chain, token_symbol, amount, destination_address))
                conn.commit()

    return {"success": True, "request_id": request_id, "status": "pending"}


@router.get("/transactions/history")
async def transaction_history(user=Depends(get_current_user)):
    if not user:
        raise HTTPException(401, "Authentication required")
    user_id = user["id"]
    history = []

    with get_db() as conn:
        with conn.cursor() as c:
            c.execute("""
                SELECT id, type, amount, status, tx_hash, created
                FROM close_transactions WHERE user_id = %s ORDER BY created DESC LIMIT 50
            """, (user_id,))
            for row in c.fetchall():
                history.append({
                    "kind": row[1], "amount": float(row[2]), "status": row[3],
                    "tx_hash": row[4], "created": row[5].isoformat() if row[5] else None
                })

            c.execute("""
                SELECT chain, token_symbol, amount, usd_value, close_credited, tx_hash, created
                FROM crypto_deposits WHERE user_id = %s ORDER BY created DESC LIMIT 50
            """, (user_id,))
            for row in c.fetchall():
                history.append({
                    "kind": "deposit", "chain": row[0], "token_symbol": row[1],
                    "amount": float(row[2]), "usd_value": float(row[3]),
                    "close_credited": row[4], "tx_hash": row[5],
                    "created": row[6].isoformat() if row[6] else None
                })

            c.execute("""
                SELECT chain, token_symbol, amount, destination_address, status, tx_hash, created
                FROM withdrawal_requests WHERE user_id = %s ORDER BY created DESC LIMIT 50
            """, (user_id,))
            for row in c.fetchall():
                history.append({
                    "kind": "withdrawal", "chain": row[0], "token_symbol": row[1],
                    "amount": float(row[2]), "destination_address": row[3],
                    "status": row[4], "tx_hash": row[5],
                    "created": row[6].isoformat() if row[6] else None
                })

    history.sort(key=lambda x: x["created"] or "", reverse=True)
    return {"history": history}


@router.post("/withdraw/fulfill")
async def fulfill_withdrawal(
    request_id: str = Body(..., embed=True),
    tx_hash: str = Body(..., embed=True),
    admin_key: str = Body(..., embed=True)
):
    if admin_key != settings.FOUNDER_KEY:
        raise HTTPException(403, "Invalid admin key")
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute(
                "UPDATE withdrawal_requests SET status = 'fulfilled', tx_hash = %s, fulfilled_at = NOW() WHERE id = %s",
                (tx_hash, request_id)
            )
            if c.rowcount == 0:
                raise HTTPException(404, "Withdrawal request not found")
            conn.commit()
    return {"success": True}


@router.get("/withdraw/pending")
async def list_pending_withdrawals(admin_key: str):
    if admin_key != settings.FOUNDER_KEY:
        raise HTTPException(403, "Invalid admin key")
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute("""
                SELECT id, user_id, chain, token_symbol, amount, destination_address, created
                FROM withdrawal_requests WHERE status = 'pending' ORDER BY created ASC
            """)
            rows = c.fetchall()
    return {"pending": [
        {"id": r[0], "user_id": str(r[1]), "chain": r[2], "token_symbol": r[3],
         "amount": float(r[4]), "destination_address": r[5], "created": r[6].isoformat() if r[6] else None}
        for r in rows
    ]}

@router.post("/send")
async def send(
    chain: str = Body(...),
    to_address: str = Body(...),
    amount_wei: int = Body(...),
    password: str = Body(...),
    token_address: str = Body(None),
    data: str = Body("0x"),
    user=Depends(get_current_user)
):
    if not user:
        raise HTTPException(401, "Authentication required")
    if len(password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters")
    try:
        tx_hash = send_transaction(
            user_id=user["id"],
            password=password,
            chain=chain,
            to_address=to_address,
            amount_wei=amount_wei,
            token_address=token_address,
            data=data
        )
        return {"tx_hash": tx_hash}
    except Exception as e:
        logger.error(f"Send failed: {e}")
        raise HTTPException(500, f"Send failed: {str(e)}")

@router.post("/sign")
async def sign(
    chain: str = Body(...),
    transaction: dict = Body(...),  # expects {to, value, data?, gas?, gasPrice?, nonce?}
    password: str = Body(...),
    user=Depends(get_current_user)
):
    if not user:
        raise HTTPException(401, "Authentication required")
    if len(password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters")
    try:
        private_key_hex = get_user_private_key(user["id"], password)
        from app.services.transaction import sign_transaction
        # We need from_address; get it from user's wallet
        from app.core.database import get_db
        with get_db() as conn:
            with conn.cursor() as c:
                c.execute("SELECT wallet_address FROM users WHERE id = %s", (user["id"],))
                row = c.fetchone()
                if not row or not row[0]:
                    raise HTTPException(400, "No wallet address found")
                from_address = row[0]
        signed_hex = sign_transaction(
            chain=chain,
            from_address=from_address,
            to_address=transaction["to"],
            value_wei=transaction.get("value", 0),
            private_key_hex=private_key_hex,
            data=transaction.get("data", "0x"),
            gas_limit=transaction.get("gas"),
            gas_price=transaction.get("gasPrice"),
            nonce=transaction.get("nonce")
        )
        return {"signed_tx": signed_hex}
    except Exception as e:
        logger.error(f"Sign failed: {e}")
        raise HTTPException(500, f"Sign failed: {str(e)}")
