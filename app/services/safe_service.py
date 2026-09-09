"""
Gnosis Safe (Safe{Core}) multisig deployment. Addresses verified
independently against each chain's own explorer (PolygonScan, BscScan,
Arbiscan, BaseScan, Etherscan) - see SAFE_SINGLETON_ADDRESSES /
SAFE_PROXY_FACTORY_ADDRESSES in config.py.

Scope: this module deploys a Safe and lists ones the user owns. It does
NOT implement the multi-owner propose/confirm/execute transaction flow -
that requires Safe's off-chain Transaction Service (which now appears to
use a unified api.safe.global endpoint requiring an API key, a change
from the older free per-chain subdomains) and EIP-712 signature
collection from each owner. That's a separate, later piece of work.
"""
import uuid
import time
import logging
from eth_utils import to_checksum_address
from app.core.database import get_db
from app.core.config import settings, get_safe_singleton, get_safe_proxy_factory
from app.services.blockchain import get_web3
from app.services.transaction import sign_transaction, broadcast_transaction
from app.services.wallet_service import get_user_private_key

logger = logging.getLogger(__name__)

ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"

# Minimal ABI fragments - matches the verified on-chain ABI (confirmed
# against Etherscan/PolygonScan's own published ABI for the real,
# deployed Proxy Factory 1.3.0 contract).
SAFE_SETUP_ABI = [{
    "inputs": [
        {"name": "_owners", "type": "address[]"},
        {"name": "_threshold", "type": "uint256"},
        {"name": "to", "type": "address"},
        {"name": "data", "type": "bytes"},
        {"name": "fallbackHandler", "type": "address"},
        {"name": "paymentToken", "type": "address"},
        {"name": "payment", "type": "uint256"},
        {"name": "paymentReceiver", "type": "address"}
    ],
    "name": "setup",
    "outputs": [],
    "stateMutability": "nonpayable",
    "type": "function"
}]

SAFE_PROXY_FACTORY_ABI = [
    {
        "inputs": [
            {"name": "_singleton", "type": "address"},
            {"name": "initializer", "type": "bytes"},
            {"name": "saltNonce", "type": "uint256"}
        ],
        "name": "createProxyWithNonce",
        "outputs": [{"name": "proxy", "type": "address"}],
        "stateMutability": "nonpayable",
        "type": "function"
    },
    {
        "anonymous": False,
        "inputs": [
            {"indexed": False, "name": "proxy", "type": "address"},
            {"indexed": False, "name": "singleton", "type": "address"}
        ],
        "name": "ProxyCreation",
        "type": "event"
    }
]


def create_safe(
    user_id: str,
    password: str,
    chain: str,
    owners: list,
    threshold: int,
    label: str = "Safe",
    wallet_address: str = None,
) -> dict:
    """Deploys a new Safe on the given chain. The deploying wallet (primary
    or a specific imported wallet via wallet_address) pays gas and signs
    the deployment transaction, but does not need to be one of the Safe's
    owners - owners is an independent list of addresses.

    Blocks until the deployment transaction is mined, since the deployed
    Safe's address comes from the ProxyCreation event in the real receipt
    rather than a locally-predicted CREATE2 address - this is the one
    on-chain action in this app that waits synchronously rather than
    returning a tx_hash immediately."""
    if chain not in settings.SUPPORTED_CHAINS:
        raise ValueError(f"Unsupported chain: {chain}")
    if not owners:
        raise ValueError("At least one owner is required")
    if threshold < 1 or threshold > len(owners):
        raise ValueError(f"Threshold must be between 1 and {len(owners)}")

    owners_checksummed = [to_checksum_address(o) for o in owners]
    singleton_address = get_safe_singleton(chain)
    factory_address = get_safe_proxy_factory(chain)
    if not singleton_address or not factory_address:
        raise ValueError(f"Safe contracts not configured for chain: {chain}")

    private_key_hex = get_user_private_key(user_id, password, wallet_address)

    if wallet_address:
        from_address = wallet_address
    else:
        with get_db() as conn:
            with conn.cursor() as c:
                c.execute("SELECT wallet_address FROM users WHERE id = %s", (user_id,))
                row = c.fetchone()
                if not row or not row[0]:
                    raise ValueError("No wallet address found")
                from_address = row[0]

    web3 = get_web3(chain)
    singleton_contract = web3.eth.contract(address=to_checksum_address(singleton_address), abi=SAFE_SETUP_ABI)
    factory_contract = web3.eth.contract(address=to_checksum_address(factory_address), abi=SAFE_PROXY_FACTORY_ABI)

    setup_calldata_hex = singleton_contract.encodeABI(fn_name="setup", args=[
        owners_checksummed,
        threshold,
        ZERO_ADDRESS,
        b"",
        ZERO_ADDRESS,
        ZERO_ADDRESS,
        0,
        ZERO_ADDRESS,
    ])
    setup_calldata_bytes = bytes.fromhex(setup_calldata_hex[2:])

    salt_nonce = int(time.time() * 1000)
    deploy_calldata = factory_contract.encodeABI(fn_name="createProxyWithNonce", args=[
        to_checksum_address(singleton_address),
        setup_calldata_bytes,
        salt_nonce,
    ])

    debug_balance = web3.eth.get_balance(to_checksum_address(from_address))
    logger.info(f"Safe deploy debug: from_address={from_address}, chain={chain}, balance_wei={debug_balance}")

    signed_hex = sign_transaction(
        chain=chain,
        from_address=from_address,
        to_address=factory_address,
        value_wei=0,
        private_key_hex=private_key_hex,
        data=deploy_calldata,
    )
    tx_hash = broadcast_transaction(chain, signed_hex)

    receipt = web3.eth.wait_for_transaction_receipt(tx_hash, timeout=180)
    events = factory_contract.events.ProxyCreation().process_receipt(receipt)
    if not events:
        raise ValueError(
            f"Safe deployment transaction ({tx_hash}) was mined but no ProxyCreation event was found. "
            f"Check the transaction on-chain before retrying, to avoid deploying a duplicate."
        )
    safe_address = events[0]["args"]["proxy"]

    safe_id = str(uuid.uuid4())
    with get_db() as conn:
        with conn.cursor() as c:
            import json
            c.execute("""
                INSERT INTO safes (id, user_id, chain, address, owners, threshold, label, tx_hash)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """, (safe_id, user_id, chain, safe_address, json.dumps(owners_checksummed), threshold, label, tx_hash))
            conn.commit()

    return {
        "id": safe_id,
        "chain": chain,
        "address": safe_address,
        "owners": owners_checksummed,
        "threshold": threshold,
        "label": label,
        "tx_hash": tx_hash,
    }


def list_safes(user_id: str) -> list:
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute("""
                SELECT id, chain, address, owners, threshold, label, tx_hash, created_at
                FROM safes WHERE user_id = %s ORDER BY created_at DESC
            """, (user_id,))
            rows = c.fetchall()
            return [
                {
                    "id": r[0], "chain": r[1], "address": r[2], "owners": r[3],
                    "threshold": r[4], "label": r[5], "tx_hash": r[6],
                    "created_at": r[7].isoformat() if r[7] else None,
                }
                for r in rows
            ]
