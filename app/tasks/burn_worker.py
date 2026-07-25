import logging
from app.core.database import get_db
from app.services.blockchain import broadcast_signed_transaction

logger = logging.getLogger(__name__)

def process_burn_task(user_id: str, amount: int, signed_tx_hex: str = None):
    if not signed_tx_hex:
        logger.warning(f"No signed tx for user {user_id}, marking completed locally.")
        with get_db() as conn:
            with conn.cursor() as c:
                c.execute("""
                    UPDATE close_transactions
                    SET status = 'completed', tx_hash = 'local'
                    WHERE user_id = %s AND type = 'burn' AND status = 'pending'
                """, (user_id,))
                conn.commit()
        return
    try:
        tx_hash = broadcast_signed_transaction("polygon", signed_tx_hex)
        with get_db() as conn:
            with conn.cursor() as c:
                c.execute("""
                    UPDATE close_transactions
                    SET status = 'completed', tx_hash = %s
                    WHERE user_id = %s AND type = 'burn' AND status = 'pending'
                """, (tx_hash, user_id))
                conn.commit()
        logger.info(f"Burn completed for user {user_id}, tx: {tx_hash}")
    except Exception as e:
        logger.error(f"Burn failed for user {user_id}: {e}")
        with get_db() as conn:
            with conn.cursor() as c:
                c.execute("""
                    UPDATE close_transactions
                    SET status = 'failed'
                    WHERE user_id = %s AND type = 'burn' AND status = 'pending'
                """, (user_id,))
                c.execute("UPDATE users SET close_balance = close_balance + %s WHERE id = %s", (amount, user_id))
                conn.commit()
