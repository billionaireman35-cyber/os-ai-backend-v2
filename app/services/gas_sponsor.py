# app/services/gas_sponsor.py
#
# Lets a wallet with zero POL still send CLOSE. Relayer wallet pays gas;
# relayer recoups cost by pulling a flat CLOSE fee from the sender.
# Relayer's own POL balance is topped up manually/operationally - no
# auto-swap-back logic. Simple on purpose: fewer moving parts to get wrong.

from eth_utils import to_checksum_address
from datetime import datetime, timezone
import logging

from app.core.database import get_db
from app.core.config import settings
from app.services.blockchain import get_web3, get_wallet_lock, send_raw_tx, ERC20_ABI

logger = logging.getLogger(__name__)

DAILY_SPONSORED_TX_CAP = 3
SPONSOR_FEE_CLOSE = 0.5
BOOTSTRAP_DRIP_POL_WEI = 3_000_000_000_000_000  # 0.003 POL, one approve() worth
LARGE_ALLOWANCE_WEI = 2**200

SPONSOR_ERC20_ABI = ERC20_ABI + [
    {"constant": False, "inputs": [{"name": "spender", "type": "address"}, {"name": "amount", "type": "uint256"}],
     "name": "approve", "outputs": [{"name": "", "type": "bool"}], "type": "function"},
    {"constant": False, "inputs": [{"name": "sender", "type": "address"}, {"name": "recipient", "type": "address"}, {"name": "amount", "type": "uint256"}],
     "name": "transferFrom", "outputs": [{"name": "", "type": "bool"}], "type": "function"},
    {"constant": True, "inputs": [{"name": "owner", "type": "address"}, {"name": "spender", "type": "address"}],
     "name": "allowance", "outputs": [{"name": "", "type": "uint256"}], "type": "function"},
]


class SponsorshipError(Exception):
    pass


def _today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _get_sponsored_count_today(user_id: str) -> int:
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute(
                "SELECT count FROM wallet_sponsorship_log WHERE user_id = %s AND log_date = %s",
                (user_id, _today_utc())
            )
            row = c.fetchone()
            return row[0] if row else 0


def _increment_sponsored_count(user_id: str):
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute("""
                INSERT INTO wallet_sponsorship_log (user_id, log_date, count)
                VALUES (%s, %s, 1)
                ON CONFLICT (user_id, log_date) DO UPDATE SET count = wallet_sponsorship_log.count + 1
            """, (user_id, _today_utc()))
            conn.commit()


def _is_bootstrapped(user_id: str) -> bool:
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute("SELECT sponsor_bootstrapped FROM users WHERE id = %s", (user_id,))
            row = c.fetchone()
            return bool(row and row[0])


def _mark_bootstrapped(user_id: str):
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute("UPDATE users SET sponsor_bootstrapped = TRUE WHERE id = %s", (user_id,))
            conn.commit()


def ensure_bootstrapped(user_id: str, user_address: str, user_private_key: str):
    """Runs once per wallet: relayer drips a little POL, then the user's
    own wallet approves the relayer to move CLOSE on its behalf."""
    if _is_bootstrapped(user_id):
        return

    from app.services.treasury_ops import ensure_gas_funded
    ensure_gas_funded(settings.RELAYER_WALLET_ADDRESS, settings.RELAYER_WALLET_PRIVATE_KEY, label="relayer")

    web3 = get_web3("polygon")
    user_address = to_checksum_address(user_address)
    relayer_address = to_checksum_address(settings.RELAYER_WALLET_ADDRESS)

    # Drip POL
    lock = get_wallet_lock(relayer_address)
    with lock:
        nonce = web3.eth.get_transaction_count(relayer_address, 'pending')
        drip_tx = {
            'from': relayer_address, 'to': user_address, 'value': BOOTSTRAP_DRIP_POL_WEI,
            'nonce': nonce, 'gas': 21000, 'gasPrice': web3.eth.gas_price,
            'chainId': 137,
        }
        drip_hash = send_raw_tx(web3, settings.RELAYER_WALLET_PRIVATE_KEY, drip_tx)
    logger.info(f"Bootstrap drip sent to {user_address}, tx: {drip_hash}")

    # Wait for the drip to actually land before spending it - avoids the
    # user's approve() failing for insufficient gas because the drip
    # hadn't been mined yet.
    web3.eth.wait_for_transaction_receipt(drip_hash, timeout=60)

    # User approves relayer
    contract_address = to_checksum_address(settings.CLOSE_CONTRACT_ADDRESS)
    contract = web3.eth.contract(address=contract_address, abi=SPONSOR_ERC20_ABI)

    lock = get_wallet_lock(user_address)
    with lock:
        nonce = web3.eth.get_transaction_count(user_address, 'pending')
        gas_estimate = contract.functions.approve(relayer_address, LARGE_ALLOWANCE_WEI).estimate_gas(
            {'from': user_address}
        )
        tx = contract.functions.approve(relayer_address, LARGE_ALLOWANCE_WEI).build_transaction({
            'from': user_address, 'nonce': nonce, 'gas': int(gas_estimate * 1.2), 'gasPrice': web3.eth.gas_price,
            'chainId': 137,
        })
        approve_hash = send_raw_tx(web3, user_private_key, tx)
    logger.info(f"User {user_address} approved relayer, tx: {approve_hash}")

    _mark_bootstrapped(user_id)


def sponsored_close_send(user_id: str, user_address: str, to_address: str, amount: float) -> dict:
    """Sends `amount` CLOSE, gas paid by the relayer. Caller must have
    already run ensure_bootstrapped for this user."""
    if not _is_bootstrapped(user_id):
        raise SponsorshipError("Wallet not yet set up for sponsored sends.")

    from app.services.treasury_ops import ensure_gas_funded
    ensure_gas_funded(settings.RELAYER_WALLET_ADDRESS, settings.RELAYER_WALLET_PRIVATE_KEY, label="relayer")

    if _get_sponsored_count_today(user_id) >= DAILY_SPONSORED_TX_CAP:
        raise SponsorshipError(
            f"Daily sponsored send limit reached ({DAILY_SPONSORED_TX_CAP}/day). "
            "Add a little POL to send without sponsorship, or try again tomorrow."
        )

    web3 = get_web3("polygon")
    user_address = to_checksum_address(user_address)
    to_address = to_checksum_address(to_address)
    relayer_address = to_checksum_address(settings.RELAYER_WALLET_ADDRESS)
    contract = web3.eth.contract(address=to_checksum_address(settings.CLOSE_CONTRACT_ADDRESS), abi=SPONSOR_ERC20_ABI)

    fee_wei = int(SPONSOR_FEE_CLOSE * 10**18)
    amount_wei = int(amount * 10**18)

    allowance = contract.functions.allowance(user_address, relayer_address).call()
    if allowance < (fee_wei + amount_wei):
        raise SponsorshipError("Insufficient CLOSE allowance - wallet may need re-bootstrapping.")

    lock = get_wallet_lock(relayer_address)
    with lock:
        # Pull the fee
        nonce = web3.eth.get_transaction_count(relayer_address, 'pending')
        gas_estimate = contract.functions.transferFrom(user_address, relayer_address, fee_wei).estimate_gas(
            {'from': relayer_address}
        )
        fee_tx = contract.functions.transferFrom(user_address, relayer_address, fee_wei).build_transaction({
            'from': relayer_address, 'nonce': nonce, 'gas': int(gas_estimate * 1.2), 'gasPrice': web3.eth.gas_price,
            'chainId': 137,
        })
        fee_hash = send_raw_tx(web3, settings.RELAYER_WALLET_PRIVATE_KEY, fee_tx)

        # Forward the actual send
        nonce = web3.eth.get_transaction_count(relayer_address, 'pending')
        gas_estimate = contract.functions.transferFrom(user_address, to_address, amount_wei).estimate_gas(
            {'from': relayer_address}
        )
        send_tx = contract.functions.transferFrom(user_address, to_address, amount_wei).build_transaction({
            'from': relayer_address, 'nonce': nonce, 'gas': int(gas_estimate * 1.2), 'gasPrice': web3.eth.gas_price,
            'chainId': 137,
        })
        send_hash = send_raw_tx(web3, settings.RELAYER_WALLET_PRIVATE_KEY, send_tx)

    _increment_sponsored_count(user_id)
    logger.info(f"Sponsored send: {amount} CLOSE {user_address} -> {to_address}, tx: {send_hash}")
    return {"fee_tx": fee_hash, "send_tx": send_hash}
