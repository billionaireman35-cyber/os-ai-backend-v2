import uuid
import base64
import hashlib
from eth_utils import keccak, to_checksum_address
import json
import logging
from Crypto.Cipher import AES
from Crypto.Protocol.KDF import PBKDF2
from Crypto.Random import get_random_bytes
from ecdsa import SigningKey, SECP256k1
from app.core.database import get_db
from app.core.config import settings
from app.services.blockchain import get_all_balances, get_token_balance, send_close_from_distribution, get_web3, ERC20_ABI
from app.services.coingecko_service import get_token_price, get_market_data_for_ids

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
    # SECURITY: prevent creating multiple wallets / re-claiming the free
    # CLOSE bonus by calling this endpoint repeatedly.
    with get_db() as _check_conn:
        with _check_conn.cursor() as _c:
            _c.execute("SELECT wallet_address FROM users WHERE id = %s", (user_id,))
            _row = _c.fetchone()
            if _row and _row[0]:
                raise ValueError("User already has a wallet")

    sk = SigningKey.generate(curve=SECP256k1)
    private_key_hex = sk.to_string().hex()
    public_key = sk.get_verifying_key()
    public_key_bytes = public_key.to_string()
    import hashlib
    address = to_checksum_address("0x" + keccak(public_key_bytes).hex()[-40:])
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

    # 🔥 Send 500 CLOSE on‑chain from distribution wallet
    try:
        tx_hash = send_close_from_distribution(address, settings.FREE_CLOSE_AMOUNT)
        logger.info(f"Sent {settings.FREE_CLOSE_AMOUNT} CLOSE to {address} tx: {tx_hash}")
    except Exception as e:
        logger.error(f"Failed to send on‑chain CLOSE: {e}")

    return {
        "address": address,
        "encrypted_private_key": encrypted_key,
    }

def get_user_balance(user_id: str) -> dict:
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute("SELECT wallet_address, close_balance FROM users WHERE id = %s", (user_id,))
            row = c.fetchone()
            if not row or not row[0]:
                return {"error": "No wallet address found. Please create a wallet first."}
            address = row[0]
            internal_close_balance = row[1] or 0

            # Fetch on-chain balances (native + tokens)
            raw_balances = get_all_balances(address)
            enriched = {}
            total_usd = 0

            cg_id_map = {
                "polygon": "matic-network",
                "ethereum": "ethereum",
                "bsc": "binancecoin",
                "arbitrum": "arbitrum",
                "base": "ethereum",
            }
            token_cg_map = {
                "CLOSE": "close-token",
                "OSINA": "osina",
                "USDC": "usd-coin",
                "WETH": "ethereum",
                "DAI": "dai",
            }

            # Collect every CoinGecko id this wallet actually needs, then
            # fetch price + 24h change + 7d sparkline in ONE batched call
            # (was previously one get_token_price() call per native/token
            # entry - N sequential requests). See get_market_data_for_ids
            # in coingecko_service.py, added 2026-08-20 for Vault sparklines.
            needed_ids = set()
            for chain, data in raw_balances.items():
                needed_ids.add(cg_id_map.get(chain, "ethereum"))
                for token_symbol in data.get("tokens", {}):
                    if token_symbol in token_cg_map:
                        needed_ids.add(token_cg_map[token_symbol])
            needed_ids.add("close-token")

            market_data = get_market_data_for_ids(list(needed_ids))

            def _market_fields(cg_id):
                m = market_data.get(cg_id, {})
                return {
                    "price": m.get("current_price", 0) or 0,
                    "change_24h": m.get("price_change_percentage_24h"),
                    "sparkline_7d": (m.get("sparkline_in_7d") or {}).get("price"),
                }

            for chain, data in raw_balances.items():
                native_symbol = data.get("native", {}).get("symbol", chain.upper())
                native_balance = data.get("native", {}).get("balance", 0)
                native_market = _market_fields(cg_id_map.get(chain, "ethereum"))
                usd_value = native_balance * native_market["price"]
                total_usd += usd_value

                enriched[chain] = {
                    "native": {
                        "symbol": native_symbol,
                        "balance": native_balance,
                        "usd": usd_value,
                        "change_24h": native_market["change_24h"],
                        "sparkline_7d": native_market["sparkline_7d"],
                    },
                    "tokens": {}
                }

                for token_symbol, token_data in data.get("tokens", {}).items():
                    token_market = _market_fields(token_cg_map.get(token_symbol)) if token_symbol in token_cg_map else {"price": 0, "change_24h": None, "sparkline_7d": None}
                    usd_token = token_data.get("balance", 0) * token_market["price"]
                    total_usd += usd_token
                    enriched[chain]["tokens"][token_symbol] = {
                        "address": token_data.get("address", ""),
                        "balance": token_data.get("balance", 0),
                        "usd": usd_token,
                        "change_24h": token_market["change_24h"],
                        "sparkline_7d": token_market["sparkline_7d"],
                    }

            # Internal CLOSE balance (legacy ledger field, kept for backward
            # compatibility - Vault UI now sources real CLOSE from the
            # on-chain token entry above, not this field)
            close_market = _market_fields("close-token")
            close_usd = internal_close_balance * close_market["price"]
            total_usd += close_usd

            enriched["close"] = {
                "balance": internal_close_balance,
                "usd": close_usd,
            }
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

    # Convert amount_wei to a human-readable token amount before storing -
    # close_transactions.amount is BIGINT and everywhere else in this table
    # stores plain amounts (e.g. 5000, 6000, 200), not 18-decimal wei, which
    # both overflows BIGINT for any real amount and would make this row
    # inconsistent with every other row in the table. Use the token's own
    # decimals() when sending an ERC-20 (CLOSE, USDC, etc. differ - USDC is
    # 6, not 18), falling back to 18 (native chain currency, or if the
    # decimals() call itself fails) matching the existing pattern used for
    # balance display elsewhere in this file.
    if token_address:
        try:
            web3 = get_web3(chain)
            contract = web3.eth.contract(address=to_checksum_address(token_address), abi=ERC20_ABI)
            decimals = contract.functions.decimals().call()
        except Exception:
            decimals = 18
    else:
        decimals = 18
    human_amount = int(amount_wei / (10 ** decimals))

    with get_db() as conn:
        with conn.cursor() as c:
            c.execute("""
                INSERT INTO close_transactions (id, user_id, type, amount, tx_hash, chain, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (str(uuid.uuid4()), user_id, "send", human_amount, tx_hash, chain, "completed"))
            conn.commit()
    return tx_hash

def sign_and_broadcast_swap(
    user_id: str,
    password: str,
    chain: str,
    to_address: str,
    data: str,
    value_wei: int = 0,
) -> str:
    """
    Signs and broadcasts arbitrary contract calldata (e.g. a KyberSwap
    router swap) using the user's own decrypted private key - same
    non-custodial pattern as send_transaction, but for a contract call
    rather than a simple transfer. to_address is the router contract,
    data is the encoded swap calldata from POST /swap (route/build).
    """
    private_key_hex = get_user_private_key(user_id, password)

    with get_db() as conn:
        with conn.cursor() as c:
            c.execute("SELECT wallet_address FROM users WHERE id = %s", (user_id,))
            row = c.fetchone()
            if not row or not row[0]:
                raise ValueError("No wallet address found")
            from_address = row[0]

    from app.services.transaction import sign_transaction, broadcast_transaction
    signed_hex = sign_transaction(
        chain=chain,
        from_address=from_address,
        to_address=to_address,
        value_wei=value_wei,
        private_key_hex=private_key_hex,
        data=data,
    )
    tx_hash = broadcast_transaction(chain, signed_hex)

    with get_db() as conn:
        with conn.cursor() as c:
            c.execute("""
                INSERT INTO close_transactions (id, user_id, type, amount, tx_hash, chain, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (str(uuid.uuid4()), user_id, "swap", 0, tx_hash, chain, "completed"))
            conn.commit()
    return tx_hash


def decrypt_private_key(encrypted_key: str, password: str) -> str:
    """Decrypt a private key using the same scheme as _encrypt_private_key."""
    from Crypto.Cipher import AES
    from Crypto.Protocol.KDF import PBKDF2
    import base64, json
    data = json.loads(base64.b64decode(encrypted_key).decode('utf-8'))
    salt = bytes.fromhex(data['salt'])
    iv = bytes.fromhex(data['iv'])
    ciphertext = bytes.fromhex(data['ciphertext'])
    key = PBKDF2(password, salt, dkLen=32, count=100000)
    cipher = AES.new(key, AES.MODE_GCM, nonce=iv)
    plaintext = cipher.decrypt(ciphertext)
    return plaintext.decode('utf-8')
