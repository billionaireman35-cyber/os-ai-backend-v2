import uuid
import json
from eth_account import Account
from app.core.database import get_db
from app.core.config import settings
import logging

logger = logging.getLogger(__name__)

def create_wallet_for_user(user_id: str, password: str) -> dict:
    Account.enable_unaudited_hdwallet_features()
    account = Account.create()
    encrypted = Account.encrypt(account.key.hex(), password)
    encrypted_json = json.dumps(encrypted)
    
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute("""
                INSERT INTO os_wallets (id, user_id, chain, address, encrypted_key, label)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (str(uuid.uuid4()), user_id, 'polygon', account.address, encrypted_json, 'Primary'))
            c.execute("""
                UPDATE users
                SET wallet_address = %s, wallet_encrypted_seed = %s,
                    close_balance = close_balance + %s
                WHERE id = %s
            """, (account.address, encrypted_json, settings.FREE_CLOSE_AMOUNT, user_id))
            conn.commit()
    
    return {
        "address": account.address,
        "seed_phrase": account.mnemonic,
        "encrypted_seed": encrypted_json
    }
