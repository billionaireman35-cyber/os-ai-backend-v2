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
from app.services.transaction import sign_transaction, broadcast_transaction, sign_safe_hash
from app.services.wallet_service import get_user_private_key
from app.services.wallet_identity import require_signing_wallet, resolve_wallet_identity

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

# Minimal ABI for interacting with a deployed Safe itself (distinct from
# the factory/singleton ABIs above, which only cover deployment).
# getTransactionHash matches Safe's own on-chain hash computation exactly
# - calling it (a view function, no gas/signing needed) rather than
# reimplementing Safe's EIP-712 hashing ourselves avoids any risk of a
# hash mismatch between what we sign and what the contract verifies.
SAFE_CONTRACT_ABI = [
    {
        "inputs": [
            {"name": "to", "type": "address"},
            {"name": "value", "type": "uint256"},
            {"name": "data", "type": "bytes"},
            {"name": "operation", "type": "uint8"},
            {"name": "safeTxGas", "type": "uint256"},
            {"name": "baseGas", "type": "uint256"},
            {"name": "gasPrice", "type": "uint256"},
            {"name": "gasToken", "type": "address"},
            {"name": "refundReceiver", "type": "address"},
            {"name": "_nonce", "type": "uint256"}
        ],
        "name": "getTransactionHash",
        "outputs": [{"name": "", "type": "bytes32"}],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [],
        "name": "nonce",
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [
            {"name": "to", "type": "address"},
            {"name": "value", "type": "uint256"},
            {"name": "data", "type": "bytes"},
            {"name": "operation", "type": "uint8"},
            {"name": "safeTxGas", "type": "uint256"},
            {"name": "baseGas", "type": "uint256"},
            {"name": "gasPrice", "type": "uint256"},
            {"name": "gasToken", "type": "address"},
            {"name": "refundReceiver", "type": "address"},
            {"name": "signatures", "type": "bytes"}
        ],
        "name": "execTransaction",
        "outputs": [{"name": "success", "type": "bool"}],
        "stateMutability": "payable",
        "type": "function"
    },
    {
        "inputs": [],
        "name": "getOwners",
        "outputs": [{"name": "", "type": "address[]"}],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [],
        "name": "getThreshold",
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function"
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
    wallet_id: str = None,
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
    print(f"SAFE_CREATE_ENTERED user_id={user_id} chain={chain} wallet_address_param={wallet_address}", flush=True)
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

    wallet = require_signing_wallet(
        user_id=user_id,
        wallet_id=wallet_id,
        wallet_address=wallet_address,
    )
    private_key_hex = get_user_private_key(
        user_id=user_id,
        password=password,
        wallet_id=wallet["id"],
    )
    from_address = wallet["address"]

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
    print(f"SAFE_DEPLOY_DEBUG from_address={from_address} chain={chain} balance_wei={debug_balance}", flush=True)

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
    events = factory_contract.events.ProxyCreation().processReceipt(receipt)
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


def find_owner_wallet(owner_address: str) -> dict | None:
    """Looks up which OS AI user (if any) controls a given address, and
    how it can be signed. Checks os_wallets, which is the source of truth
    for every wallet in this app - including primary wallets, which also
    have a row there (see get_user_private_key's primary-wallet query).

    Returns None if the address matches no OS AI user at all (a genuinely
    external owner - out of scope for in-app signing in this version).
    Otherwise returns {"user_id", "wallet_type"} where wallet_type is
    "custodial" (backend can sign with the owner's password) or
    "connected" (only the owner's own external wallet, e.g. MetaMask via
    WalletConnect, can sign - this app never held that key)."""
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute(
                "SELECT user_id, wallet_type FROM os_wallets WHERE LOWER(address) = LOWER(%s)",
                (owner_address,)
            )
            row = c.fetchone()
            if not row:
                return None
            return {"user_id": row[0], "wallet_type": row[1] or "custodial"}


def propose_safe_transaction(
    safe_id: str,
    user_id: str,
    password: str,
    to_address: str,
    value_wei: int,
    data: str = "0x",
    wallet_id: str = None,
) -> dict:
    """Proposes a withdrawal/transfer from a Safe: computes the exact
    on-chain transaction hash via the Safe's own getTransactionHash (so
    our hash always matches what execTransaction will later verify),
    signs it with the proposer's own key, and stores it as the first
    signature. Only works if the proposer is both an owner of this Safe
    AND an OS AI user with a custodial (password-signable) wallet -
    connected-wallet owners must sign via their own external wallet
    (not yet implemented) and non-owners are rejected outright."""
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute("SELECT chain, address, owners, threshold FROM safes WHERE id = %s", (safe_id,))
            row = c.fetchone()
            if not row:
                raise ValueError("Safe not found")
            chain, safe_address, owners, threshold = row[0], row[1], row[2], row[3]

    proposer_wallet = require_signing_wallet(
        user_id=user_id,
        wallet_id=wallet_id,
    )
    proposer_address = proposer_wallet["address"]

    if proposer_address.lower() not in [o.lower() for o in owners]:
        raise ValueError("Only an owner of this Safe can propose a transaction")

    private_key_hex = get_user_private_key(
        user_id=user_id,
        password=password,
        wallet_id=proposer_wallet["id"],
    )

    web3 = get_web3(chain)
    safe_contract = web3.eth.contract(address=to_checksum_address(safe_address), abi=SAFE_CONTRACT_ABI)

    safe_nonce = safe_contract.functions.nonce().call()

    ZERO = "0x0000000000000000000000000000000000000000"
    data_bytes = bytes.fromhex(data[2:]) if data.startswith("0x") else bytes.fromhex(data)

    safe_tx_hash = safe_contract.functions.getTransactionHash(
        to_checksum_address(to_address),
        value_wei,
        data_bytes,
        0,  # operation: 0 = Call (the only kind this app initiates)
        0,  # safeTxGas: 0 = no manual gas limit override, let execTransaction estimate
        0,  # baseGas
        0,  # gasPrice: 0 = no refund/relayer mechanism - proposer/executor pays their own gas
        ZERO,  # gasToken
        ZERO,  # refundReceiver
        safe_nonce,
    ).call()

    safe_tx_hash_hex = "0x" + safe_tx_hash.hex()
    signature_hex = sign_safe_hash(safe_tx_hash_hex, private_key_hex, proposer_address)

    tx_id = str(uuid.uuid4())
    import json
    signatures = [{"owner": proposer_address, "signature": signature_hex}]
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute("""
                INSERT INTO safe_transactions
                    (id, safe_id, proposer_user_id, proposer_wallet_id, to_address, value_wei, data, safe_nonce, safe_tx_hash, signatures, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'pending')
            """, (tx_id, safe_id, user_id, proposer_wallet["id"], to_address, str(value_wei), data, safe_nonce, safe_tx_hash_hex, json.dumps(signatures)))
            conn.commit()

    return {
        "id": tx_id,
        "safe_id": safe_id,
        "to_address": to_address,
        "value_wei": str(value_wei),
        "safe_nonce": safe_nonce,
        "safe_tx_hash": safe_tx_hash_hex,
        "signatures_collected": 1,
        "threshold": threshold,
        "status": "pending",
    }


def sign_safe_transaction(
    tx_id: str,
    user_id: str,
    password: str,
    wallet_id: str = None,
) -> dict:
    """Adds one more owner's signature to an existing pending proposal.
    Recomputes the same safe_tx_hash from the stored proposal (rather than
    trusting a client-supplied hash) and verifies the caller is genuinely
    an owner of the Safe before signing - a user can only sign with their
    own key regardless, but this also gives a clear error rather than a
    wasted signature from a non-owner."""
    import json

    with get_db() as conn:
        with conn.cursor() as c:
            c.execute("""
                SELECT st.safe_id, st.safe_tx_hash, st.signatures, st.status,
                       s.owners, s.threshold, s.chain, s.address
                FROM safe_transactions st
                JOIN safes s ON s.id = st.safe_id
                WHERE st.id = %s
            """, (tx_id,))
            row = c.fetchone()
            if not row:
                raise ValueError("Proposal not found")
            safe_id, safe_tx_hash_hex, signatures, status, owners, threshold, chain, safe_address = row

    if status != "pending":
        raise ValueError(f"This proposal is already {status}")

    signer_wallet = require_signing_wallet(
        user_id=user_id,
        wallet_id=wallet_id,
    )
    signer_address = signer_wallet["address"]

    if signer_address.lower() not in [o.lower() for o in owners]:
        raise ValueError("Only an owner of this Safe can sign this proposal")

    if any(sig["owner"].lower() == signer_address.lower() for sig in signatures):
        raise ValueError("You have already signed this proposal")

    private_key_hex = get_user_private_key(
        user_id=user_id,
        password=password,
        wallet_id=signer_wallet["id"],
    )
    signature_hex = sign_safe_hash(safe_tx_hash_hex, private_key_hex, signer_address)

    signatures.append({"owner": signer_address, "signature": signature_hex})

    with get_db() as conn:
        with conn.cursor() as c:
            c.execute(
                "UPDATE safe_transactions SET signatures = %s WHERE id = %s",
                (json.dumps(signatures), tx_id)
            )
            conn.commit()

    return {
        "id": tx_id,
        "safe_id": safe_id,
        "signatures_collected": len(signatures),
        "threshold": threshold,
        "ready_to_execute": len(signatures) >= threshold,
        "status": "pending",
    }


def execute_safe_transaction(
    tx_id: str,
    user_id: str,
    password: str,
    wallet_id: str = None,
) -> dict:
    """Executes a Safe transaction once enough signatures are collected.
    Any owner can trigger execution (they pay the gas for this call) -
    Safe's contract itself verifies the collected signatures meet
    threshold, so this app's own count check is a fast-fail convenience,
    not the actual security boundary.

    Signatures must be concatenated in ascending order by signer address -
    this is a real Safe contract requirement (its signature-checking loop
    assumes ascending order to detect duplicates/enforce distinctness),
    not just a formatting preference."""
    import json

    with get_db() as conn:
        with conn.cursor() as c:
            c.execute("""
                SELECT st.to_address, st.value_wei, st.data, st.safe_nonce,
                       st.signatures, st.status,
                       s.chain, s.address, s.threshold, s.owners
                FROM safe_transactions st
                JOIN safes s ON s.id = st.safe_id
                WHERE st.id = %s
            """, (tx_id,))
            row = c.fetchone()
            if not row:
                raise ValueError("Proposal not found")
            (to_address, value_wei_str, data, safe_nonce, signatures, status,
             chain, safe_address, threshold, owners) = row

    if status != "pending":
        raise ValueError(f"This proposal is already {status}")
    if len(signatures) < threshold:
        raise ValueError(f"Not enough signatures yet ({len(signatures)}/{threshold})")

    executor_wallet = require_signing_wallet(
        user_id=user_id,
        wallet_id=wallet_id,
    )
    executor_address = executor_wallet["address"]

    if executor_address.lower() not in [o.lower() for o in owners]:
        raise ValueError("Only an owner of this Safe can execute this proposal")

    private_key_hex = get_user_private_key(
        user_id=user_id,
        password=password,
        wallet_id=executor_wallet["id"],
    )

    # Concatenate signatures sorted ascending by owner address (required by
    # the Safe contract's own verification order, not a stylistic choice).
    sorted_sigs = sorted(signatures, key=lambda s: s["owner"].lower())
    packed_signatures = "0x" + "".join(sig["signature"][2:] for sig in sorted_sigs)

    web3 = get_web3(chain)
    safe_contract = web3.eth.contract(address=to_checksum_address(safe_address), abi=SAFE_CONTRACT_ABI)

    ZERO = "0x0000000000000000000000000000000000000000"
    data_bytes = bytes.fromhex(data[2:]) if data.startswith("0x") else bytes.fromhex(data)
    value_wei = int(value_wei_str)

    exec_calldata = safe_contract.encodeABI(fn_name="execTransaction", args=[
        to_checksum_address(to_address),
        value_wei,
        data_bytes,
        0,
        0,
        0,
        0,
        ZERO,
        ZERO,
        bytes.fromhex(packed_signatures[2:]),
    ])

    signed_hex = sign_transaction(
        chain=chain,
        from_address=executor_address,
        to_address=safe_address,
        value_wei=0,
        private_key_hex=private_key_hex,
        data=exec_calldata,
    )
    exec_tx_hash = broadcast_transaction(chain, signed_hex)

    with get_db() as conn:
        with conn.cursor() as c:
            c.execute("""
                UPDATE safe_transactions
                SET status = 'executed', exec_tx_hash = %s, executor_wallet_id = %s, executed_at = NOW()
                WHERE id = %s
            """, (exec_tx_hash, executor_wallet["id"], tx_id))
            conn.commit()

    return {
        "id": tx_id,
        "status": "executed",
        "exec_tx_hash": exec_tx_hash,
    }


def list_pending_transactions(
    safe_id: str,
    user_id: str,
    wallet_id: str = None,
) -> list:
    """Lists pending proposals for a Safe, for display to any of its
    owners. Scoped by checking the requester is genuinely an owner
    (not just any OS AI user), since proposal details (destination,
    amount) shouldn't be visible to non-owners."""
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute("SELECT owners, threshold FROM safes WHERE id = %s", (safe_id,))
            row = c.fetchone()
            if not row:
                raise ValueError("Safe not found")
            owners, threshold = row[0], row[1]

    requester_wallet = resolve_wallet_identity(
        user_id=user_id,
        wallet_id=wallet_id,
        require_signing=False,
    )
    requester_address = requester_wallet["address"]

    if requester_address.lower() not in [o.lower() for o in owners]:
        raise ValueError("Only an owner of this Safe can view its proposals")

    with get_db() as conn:
        with conn.cursor() as c:
            c.execute("""
                SELECT id, to_address, value_wei, data, safe_nonce, signatures, status, exec_tx_hash, created_at
                FROM safe_transactions
                WHERE safe_id = %s
                ORDER BY created_at DESC
            """, (safe_id,))
            rows = c.fetchall()
            return [
                {
                    "id": r[0], "to_address": r[1], "value_wei": r[2], "data": r[3],
                    "safe_nonce": r[4], "signatures_collected": len(r[5]),
                    "signers": [s["owner"] for s in r[5]], "threshold": threshold,
                    "status": r[6], "exec_tx_hash": r[7],
                    "created_at": r[8].isoformat() if r[8] else None,
                }
                for r in rows
            ]


def get_safe_balance(safe_id: str, user_id: str) -> dict:
    """Native + tracked-token balance for one Safe, on the single chain it
    was actually deployed on (unlike get_all_balances, which checks every
    supported chain - a Safe's address is meaningless on chains it was
    never deployed to)."""
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute("""
                SELECT chain, address FROM safes WHERE id = %s AND user_id = %s
            """, (safe_id, user_id))
            row = c.fetchone()
            if not row:
                raise ValueError("Safe not found")
            chain, address = row[0], row[1]

    from app.services.blockchain import get_balance, get_token_balance

    checksummed = to_checksum_address(address)
    try:
        native_bal = float(get_balance(chain, checksummed))
    except Exception as e:
        logger.error(f"get_balance failed for Safe {safe_id} ({chain}/{address}): {e}")
        native_bal = 0.0

    result = {
        "chain": chain,
        "address": address,
        "native": {"symbol": "POL" if chain == "polygon" else chain.upper(), "balance": native_bal},
        "tokens": {},
    }

    if chain == "polygon":
        token_list = [
            {"symbol": "CLOSE", "address": settings.CLOSE_CONTRACT_ADDRESS},
            {"symbol": "USDC", "address": "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"},
        ]
        for token in token_list:
            try:
                bal = get_token_balance(chain, token["address"], checksummed)
                if bal > 0:
                    result["tokens"][token["symbol"]] = {"address": token["address"], "balance": bal}
            except Exception:
                pass

    return result
