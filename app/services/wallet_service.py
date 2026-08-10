import uuid
import base64
import hashlib
import json
import logging
from Crypto.Cipher import AES
from Crypto.Protocol.KDF import PBKDF2
from Crypto.Random import get_random_bytes
from ecdsa import SigningKey, SECP256k1
from app.core.database import get_db
from app.core.config import settings
from app.services.blockchain import get_all_balances
from app.services.coingecko_service import get_token_price, get_top_tokens

logger = logging.getLogger(__name__)

SECP256K1_N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141

def _derive_key(password: str, salt: bytes) -> bytes:
    return PBKDF2(password, salt, dkLen=32, count=100000)

def _encrypt_private_key(private_key_hex: str, password: str) -> str:
    salt = get_random_bytes(16)
    key = _derive_key(password, salt)
    nonce = get_random_bytes(12)
    cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
    ciphertext, tag = cipher.encrypt_and_digest(private_key_hex.encode())
    encrypted_payload = salt + nonce + tag + ciphertext
    return base64.b64encode(encrypted_payload).decode()

def _decrypt_private_key(encrypted_b64: str, password: str) -> str:
    raw = base64.b64decode(encrypted_b64)
    salt = raw[:16]
    nonce = raw[16:28]
    tag = raw[28:44]
    ciphertext = raw[44:]
    key = _derive_key(password, salt)
    cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
    plaintext = cipher.decrypt_and_verify(ciphertext, tag)
    private_key_hex = plaintext.decode()
    if len(private_key_hex) != 64:
        raise ValueError(f"Invalid private key length: {len(private_key_hex)} (expected 64)")
    key_int = int(private_key_hex, 16)
    if not (0 < key_int < SECP256K1_N):
        raise ValueError("Private key out of range")
    return private_key_hex

def create_wallet_for_user(user_id: str, password: str) -> dict:
    sk = SigningKey.generate(curve=SECP256k1)
    private_key_hex = sk.to_string().hex()
    public_key = sk.get_verifying_key()
    public_key_bytes = public_key.to_string()
    import hashlib
    address = "0x" + hashlib.sha256(public_key_bytes).hexdigest()[:40]
    encrypted_key = _encrypt_private_key(private_key_hex, password)

    with get_db() as conn:
        with conn.cursor() as c:
            c.execute("""
                INSERT INTO os_wallets (id, user_id, chain, address, encrypted_key, label)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (str(uuid.uuid4()), user_id, 'polygon', address, encrypted_key, 'Primary'))
            c.execute("""
                UPDATE users
                SET wallet_address = %s, wallet_encrypted_seed = %s,
                    close_balance = close_balance + %s
                WHERE id = %s
            """, (address, encrypted_key, settings.FREE_CLOSE_AMOUNT, user_id))
            conn.commit()
    return {
        "address": address,
        "encrypted_private_key": encrypted_key,
    }

def get_user_balance(user_id: str) -> dict:
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute("SELECT wallet_address FROM users WHERE id = %s", (user_id,))
            row = c.fetchone()
            if not row or not row[0]:
                return {"error": "No wallet address found. Please create a wallet first."}
            address = row[0]
            # Get raw balances from blockchain
            raw_balances = get_all_balances(address)
            # Enrich with USD prices
            enriched = {}
            total_usd = 0
            for chain, data in raw_balances.items():
                native_symbol = data.get("native", {}).get("symbol", chain.upper())
                native_balance = data.get("native", {}).get("balance", 0)
                # Get price for native token (use Coingecko ID)
                price = 0
                try:
                    # Map chain to Coingecko ID
                    cg_id_map = {
                        "polygon": "matic-network",
                        "ethereum": "ethereum",
                        "bsc": "binancecoin",
                        "arbitrum": "arbitrum",
                        "base": "ethereum",  # base uses ETH
                    }
                    cg_id = cg_id_map.get(chain, "ethereum")
                    price_data = get_token_price(cg_id, "usd")
                    price = price_data.get(cg_id, {}).get("usd", 0)
                except:
                    pass
                usd_value = native_balance * price
                total_usd += usd_value
                enriched[chain] = {
                    "native": {
                        "symbol": native_symbol,
                        "balance": native_balance,
                        "usd": usd_value,
                    },
                    "tokens": {}
                }
                # For tokens, we'll need to get prices per token – for now we'll set to 0
                for token_symbol, token_data in data.get("tokens", {}).items():
                    # Try to get price from Coingecko if we have mapping
                    token_price = 0
                    # Hardcode some mappings for common tokens
                    token_cg_map = {
                        "CLOSE": "close-token",  # placeholder
                        "OSINA": "osina",        # placeholder
                        "USDC": "usd-coin",
                        "WETH": "ethereum",
                        "DAI": "dai",
                    }
                    if token_symbol in token_cg_map:
                        try:
                            price_data = get_token_price(token_cg_map[token_symbol], "usd")
                            token_price = price_data.get(token_cg_map[token_symbol], {}).get("usd", 0)
                        except:
                            pass
                    usd_token = token_data.get("balance", 0) * token_price
                    total_usd += usd_token
                    enriched[chain]["tokens"][token_symbol] = {
                        "address": token_data.get("address", ""),
                        "balance": token_data.get("balance", 0),
                        "usd": usd_token,
                    }
            # Also return internal CLOSE balance (which is stored in DB)
            c.execute("SELECT close_balance FROM users WHERE id = %s", (user_id,))
            close_row = c.fetchone()
            close_balance = close_row[0] if close_row else 0
            # Get CLOSE price (using placeholder, but we can use a real ID later)
            close_price = 0
            try:
                price_data = get_token_price("close-token", "usd")
                close_price = price_data.get("close-token", {}).get("usd", 0)
            except:
                pass
            enriched["close"] = {
                "balance": close_balance,
                "usd": close_balance * close_price,
            }
            total_usd += enriched["close"]["usd"]
            enriched["total_usd"] = total_usd
            return enriched

def get_user_transactions(user_id: str, limit: int = 20) -> list:
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute("""
                SELECT id, type, amount, tx_hash, chain, status, created
                FROM close_transactions
                WHERE user_id = %s
                ORDER BY created DESC
                LIMIT %s
            """, (user_id, limit))
            rows = c.fetchall()
            return [
                {
                    "id": row[0],
                    "type": row[1],
                    "amount": row[2],
                    "tx_hash": row[3],
                    "chain": row[4],
                    "status": row[5],
                    "time": row[6].isoformat() if row[6] else None
                }
                for row in rows
            ]

def get_user_private_key(user_id: str, password: str) -> str:
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute("SELECT encrypted_key FROM os_wallets WHERE user_id = %s LIMIT 1", (user_id,))
            row = c.fetchone()
            if not row or not row[0]:
                raise ValueError("No wallet found for user")
            encrypted_key = row[0]
            return _decrypt_private_key(encrypted_key, password)

def send_transaction(
    user_id: str,
    password: str,
    chain: str,
    to_address: str,
    amount_wei: int,
    token_address: str = None,
    data: str = "0x"
) -> str:
    private_key_hex = get_user_private_key(user_id, password)

    with get_db() as conn:
        with conn.cursor() as c:
            c.execute("SELECT wallet_address FROM users WHERE id = %s", (user_id,))
            row = c.fetchone()
            if not row or not row[0]:
                raise ValueError("No wallet address found")
            from_address = row[0]

    if token_address:
        def pad_hex(value, length=64):
            return hex(value)[2:].zfill(length)
        to_padded = to_address[2:].zfill(64)
        amount_padded = pad_hex(amount_wei)
        data = "0xa9059cbb" + to_padded + amount_padded
        to_address = token_address

    from app.services.transaction import sign_transaction, broadcast_transaction
    signed_hex = sign_transaction(
        chain=chain,
        from_address=from_address,
        to_address=to_address,
        value_wei=amount_wei if not token_address else 0,
        private_key_hex=private_key_hex,
        data=data
    )
    tx_hash = broadcast_transaction(chain, signed_hex)

    with get_db() as conn:
        with conn.cursor() as c:
            c.execute("""
                INSERT INTO close_transactions (id, user_id, type, amount, tx_hash, chain, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (str(uuid.uuid4()), user_id, "send", amount_wei, tx_hash, chain, "completed"))
            conn.commit()
    return tx_hash
