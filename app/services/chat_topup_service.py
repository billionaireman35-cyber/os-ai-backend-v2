"""
Chat CLOSE top-up verification. A user sends any amount of CLOSE (ERC-20,
Polygon) from their own OS AI wallet to the chat treasury address, then
submits the resulting tx_hash. This module verifies on-chain that the
transfer really happened, then credits close_balance 1:1 - the same
internal allowance chat messages already debit from (see chat.py's
per-message burn logic), now honestly backed by real token movement
instead of an ungrounded counter.

Same verification approach as workspace_payment_service.py, but no fixed
expected_amount - top-ups can be any amount the user chooses to send.
"""
import logging
import uuid
from eth_utils import to_checksum_address
from app.services.blockchain import get_web3
from app.core.database import get_db

logger = logging.getLogger(__name__)

CLOSE_TOKEN_ADDRESS = "0x3c6833cFDdED80fE76474a3Cb2Cc050Daec91fe8"
CLOSE_DECIMALS = 18
CHAT_TREASURY_ADDRESS = "0x109464E84bDD6552d76bcBbaEf03bDe8069C0698"
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

CHAIN = "polygon"


def verify_and_credit_chat_topup(user_id: str, tx_hash: str) -> dict:
    """
    Verifies a CLOSE payment to the chat treasury and credits close_balance
    1:1 with the amount sent. Raises ValueError with a clear message on any
    failure. Returns the credited amount on success.
    """
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute("SELECT id FROM chat_topups WHERE tx_hash = %s", (tx_hash,))
            if c.fetchone():
                raise ValueError("This transaction has already been credited")

            c.execute("SELECT wallet_address FROM users WHERE id = %s", (user_id,))
            row = c.fetchone()
            if not row or not row[0]:
                raise ValueError("No wallet found for this account")
            user_wallet = to_checksum_address(row[0])

    web3 = get_web3(CHAIN)
    treasury = to_checksum_address(CHAT_TREASURY_ADDRESS)
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
        raise ValueError("No qualifying CLOSE transfer to the chat treasury address found in this transaction")

    if matched_amount <= 0:
        raise ValueError("Transferred amount must be greater than zero")

    credited = int(matched_amount)
    topup_id = str(uuid.uuid4())

    with get_db() as conn:
        with conn.cursor() as c:
            c.execute("""
                INSERT INTO chat_topups (id, user_id, tx_hash, amount, status)
                VALUES (%s, %s, %s, %s, %s)
            """, (topup_id, user_id, tx_hash, credited, "confirmed"))
            c.execute("UPDATE users SET close_balance = close_balance + %s WHERE id = %s", (credited, user_id))
            conn.commit()

    logger.info(f"Chat top-up credited: user={user_id} amount={credited} tx={tx_hash}")
    return {"id": topup_id, "amount": credited, "tx_hash": tx_hash}
