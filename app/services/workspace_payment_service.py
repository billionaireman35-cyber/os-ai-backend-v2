"""
Hustle Hub payment verification. A user sends CLOSE (ERC-20, Polygon) from
their own OS AI wallet to the platform treasury address, then submits the
resulting tx_hash. This module verifies on-chain that the transfer really
happened, for the exact expected amount, from that user's own wallet,
before the caller (workspace.py) grants access.

Unlike deposit_service.verify_and_credit_deposit (which credits a variable
internal balance for any qualifying amount), this requires an EXACT amount
match and an exact sender match - this is an access payment, not a top-up.
"""
import logging
from eth_utils import to_checksum_address
from app.services.blockchain import get_web3
from app.core.config import settings
from app.core.database import get_db

logger = logging.getLogger(__name__)

CLOSE_TOKEN_ADDRESS = "0x3c6833cFDdED80fE76474a3Cb2Cc050Daec91fe8"
CLOSE_DECIMALS = 18
TREASURY_ADDRESS = "0x5bD39AD3e8B1CB01e7385958160FD9b2675D02d1"
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

CHAIN = "polygon"


def get_unresolved_payment(user_id: str, tx_hash: str, purpose: str):
    """
    Checks whether this tx_hash was already verified for this user/purpose
    but never got linked to a completed workspace (workspace_id IS NULL).
    Used to let a failed create_workspace be safely retried with the same
    tx_hash, instead of verify_workspace_payment rejecting it as a
    duplicate. Returns the payment row id if an unresolved payment exists,
    else None.
    """
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute("""
                SELECT id FROM workspace_payments
                WHERE user_id = %s AND tx_hash = %s AND purpose = %s AND workspace_id IS NULL
            """, (user_id, tx_hash, purpose))
            row = c.fetchone()
            return row[0] if row else None


def link_payment_to_workspace(payment_id: str, workspace_id: str):
    """Marks a previously-verified payment as resolved by linking it to the
    workspace it paid for. Called once workspace creation actually succeeds."""
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute(
                "UPDATE workspace_payments SET workspace_id = %s WHERE id = %s",
                (workspace_id, payment_id)
            )
            conn.commit()


def verify_workspace_payment(user_id: str, tx_hash: str, expected_amount: int, purpose: str, workspace_id: str = None) -> dict:
    """
    Verifies a CLOSE token payment to the treasury address for Hustle Hub
    access (create or join-approval). Raises ValueError with a clear message
    on any failure. Returns a dict with the recorded payment id on success.

    purpose: "create" or "join" - stored for audit, not itself validated
    against expected_amount here (the caller passes the right amount for
    whichever action this is).
    """
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute("SELECT id FROM workspace_payments WHERE tx_hash = %s", (tx_hash,))
            if c.fetchone():
                raise ValueError("This transaction has already been used for a Hustle Hub payment")

            c.execute("SELECT wallet_address FROM users WHERE id = %s", (user_id,))
            row = c.fetchone()
            if not row or not row[0]:
                raise ValueError("No wallet found for this account")
            user_wallet = to_checksum_address(row[0])

    web3 = get_web3(CHAIN)
    treasury = to_checksum_address(TREASURY_ADDRESS)
    close_contract = to_checksum_address(CLOSE_TOKEN_ADDRESS)

    receipt = web3.eth.get_transaction_receipt(tx_hash)
    if receipt.status != 1:
        raise ValueError("Transaction failed on-chain")

    tx = web3.eth.get_transaction(tx_hash)
    tx_from = to_checksum_address(tx["from"])
    if tx_from != user_wallet:
        raise ValueError("This transaction was not sent from your own wallet")

    # Scan logs for a CLOSE Transfer event: from = user's wallet, to = treasury
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
        raise ValueError("No qualifying CLOSE transfer to the treasury address found in this transaction")

    # Exact match required - access payments aren't proportional like deposits.
    # Small tolerance for float rounding on the division above.
    if abs(matched_amount - expected_amount) > 0.0001:
        raise ValueError(f"Expected a payment of exactly {expected_amount} CLOSE, but this transaction sent {matched_amount}")

    import uuid
    payment_id = str(uuid.uuid4())
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute("""
                INSERT INTO workspace_payments (id, user_id, workspace_id, tx_hash, purpose, amount, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (payment_id, user_id, workspace_id, tx_hash, purpose, expected_amount, "confirmed"))
            conn.commit()

    logger.info(f"Workspace payment verified: user={user_id} purpose={purpose} amount={expected_amount} tx={tx_hash}")
    return {"id": payment_id, "amount": expected_amount, "tx_hash": tx_hash}
