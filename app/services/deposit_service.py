"""Crypto deposit verification. Users submit a tx_hash claiming they paid
settings.DEPOSIT_ADDRESS; this module verifies that on-chain before crediting
CLOSE. Prices fetched directly from CoinGecko's public API (no API key
needed for this endpoint, rate-limited but fine at low volume).
"""
import requests
import logging
from eth_utils import to_checksum_address
from app.services.blockchain import get_web3
from app.core.config import settings

logger = logging.getLogger(__name__)

NATIVE_SYMBOL = {"polygon": "matic-network", "ethereum": "ethereum", "bsc": "binancecoin"}
NATIVE_TICKER = {"polygon": "POL", "ethereum": "ETH", "bsc": "BNB"}
MIN_USD = {
    "polygon": settings.DEPOSIT_MIN_USD_POLYGON,
    "ethereum": settings.DEPOSIT_MIN_USD_ETHEREUM,
    "bsc": settings.DEPOSIT_MIN_USD_BSC,
}

# Known stablecoins per chain, treated as fixed $1 - avoids needing a
# universal token-price oracle for arbitrary ERC-20s.
STABLECOINS = {
    "polygon": {"0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174": ("USDC", 6)},
    "ethereum": {"0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48": ("USDC", 6)},
    "bsc": {"0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d": ("USDC", 18)},
}

TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"


def get_native_price_usd(chain: str) -> float:
    coingecko_id = NATIVE_SYMBOL[chain]
    resp = requests.get(
        "https://api.coingecko.com/api/v3/simple/price",
        params={"ids": coingecko_id, "vs_currencies": "usd"},
        timeout=10
    )
    resp.raise_for_status()
    return resp.json()[coingecko_id]["usd"]


def verify_and_credit_deposit(user_id: str, chain: str, tx_hash: str) -> dict:
    from app.core.database import get_db
    import uuid

    if chain not in ("polygon", "ethereum", "bsc"):
        raise ValueError(f"Unsupported chain: {chain}")

    with get_db() as conn:
        with conn.cursor() as c:
            c.execute("SELECT id FROM crypto_deposits WHERE tx_hash = %s", (tx_hash,))
            if c.fetchone():
                raise ValueError("This transaction has already been credited")

    web3 = get_web3(chain)
    deposit_address = to_checksum_address(settings.DEPOSIT_ADDRESS)

    receipt = web3.eth.get_transaction_receipt(tx_hash)
    if receipt.status != 1:
        raise ValueError("Transaction failed on-chain")

    tx = web3.eth.get_transaction(tx_hash)

    token_symbol = None
    amount = 0.0
    usd_value = 0.0

    # Case 1: native currency transfer directly to the deposit address
    if tx["to"] and to_checksum_address(tx["to"]) == deposit_address and tx["value"] > 0:
        amount = float(web3.fromWei(tx["value"], "ether"))
        price = get_native_price_usd(chain)
        usd_value = amount * price
        token_symbol = NATIVE_TICKER[chain]

    # Case 2: ERC-20 token transfer (check logs for a Transfer event to deposit_address)
    else:
        stablecoins = STABLECOINS.get(chain, {})
        for log in receipt.logs:
            if len(log["topics"]) < 3:
                continue
            if log["topics"][0].hex() != TRANSFER_TOPIC:
                continue
            to_addr = to_checksum_address("0x" + log["topics"][2].hex()[-40:])
            if to_addr != deposit_address:
                continue
            token_address = to_checksum_address(log["address"])
            if token_address in stablecoins:
                symbol, decimals = stablecoins[token_address]
                raw_amount = int(log["data"], 16) if isinstance(log["data"], str) else int.from_bytes(log["data"], "big")
                amount = raw_amount / (10 ** decimals)
                usd_value = amount  # stablecoin, 1:1
                token_symbol = symbol
                break

    if token_symbol is None:
        raise ValueError("No qualifying deposit to the deposit address found in this transaction")

    min_usd = MIN_USD[chain]
    if usd_value < min_usd:
        raise ValueError(f"Deposit of ${usd_value:.2f} is below the ${min_usd:.2f} minimum for {chain}")

    close_credited = int(usd_value * settings.CLOSE_PER_USD)

    with get_db() as conn:
        with conn.cursor() as c:
            deposit_id = str(uuid.uuid4())
            c.execute("""
                INSERT INTO crypto_deposits (id, user_id, chain, tx_hash, token_symbol, amount, usd_value, close_credited)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """, (deposit_id, user_id, chain, tx_hash, token_symbol, amount, usd_value, close_credited))
            c.execute("UPDATE users SET close_balance = close_balance + %s WHERE id = %s", (close_credited, user_id))
            conn.commit()

    logger.info(f"Deposit credited: user={user_id} chain={chain} {amount} {token_symbol} (${usd_value:.2f}) -> {close_credited} CLOSE")
    return {
        "token_symbol": token_symbol,
        "amount": amount,
        "usd_value": round(usd_value, 2),
        "close_credited": close_credited
    }
