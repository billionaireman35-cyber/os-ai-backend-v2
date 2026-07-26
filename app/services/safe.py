from fastapi import APIRouter, Depends, HTTPException
from app.core.security import get_current_user
from app.core.database import get_db
from app.services.blockchain import broadcast_signed_transaction
from app.core.config import settings, get_safe_singleton, get_safe_proxy_factory
import uuid
import json
import logging
from datetime import datetime
from safe_eth.eth import EthereumClient
from safe_eth.safe import Safe
from safe_eth.safe.safe_tx import SafeTx
from safe_eth.safe.signatures import signatures_to_bytes
from web3 import Web3
import eth_account

router = APIRouter(prefix="/safe", tags=["Gnosis Safe"])
logger = logging.getLogger(__name__)

def get_web3(chain: str):
    rpc = settings.get_rpc_url(chain)
    return Web3(Web3.HTTPProvider(rpc))

def get_ethereum_client(chain: str) -> EthereumClient:
    return EthereumClient(settings.get_rpc_url(chain))

def get_safe_contract(safe_address: str, chain: str) -> Safe:
    """Load an existing deployed Safe as a Safe object."""
    ethereum_client = get_ethereum_client(chain)
    return Safe(Web3.to_checksum_address(safe_address), ethereum_client)

def create_safe(owners: list, threshold: int, chain: str = "polygon") -> str:
    """
    Deploy a new Gnosis Safe on-chain with the given owners/threshold.
    Uses the platform's distribution wallet to pay deployment gas.
    Returns the deployed Safe's address.
    """
    ethereum_client = get_ethereum_client(chain)
    deployer_account = eth_account.Account.from_key(settings.DISTRIBUTION_WALLET_PRIVATE_KEY)
    master_copy_address = Web3.to_checksum_address(get_safe_singleton(chain))
    proxy_factory_address = Web3.to_checksum_address(get_safe_proxy_factory(chain))
    checksum_owners = [Web3.to_checksum_address(o) for o in owners]

    tx_sent = Safe.create(
        ethereum_client=ethereum_client,
        deployer_account=deployer_account,
        master_copy_address=master_copy_address,
        owners=checksum_owners,
        threshold=threshold,
        proxy_factory_address=proxy_factory_address,
    )
    return tx_sent.contract_address

def propose_safe_transaction(safe_address: str, to: str, value: int, data: str, chain: str = "polygon") -> dict:
    """
    Build (but do not execute) a Safe multisig transaction, returning its safe_tx_hash
    so owners can sign it.
    """
    safe = get_safe_contract(safe_address, chain)
    data_bytes = bytes.fromhex(data[2:]) if isinstance(data, str) and data.startswith("0x") else b""

    safe_tx = safe.build_multisig_tx(
        to=Web3.to_checksum_address(to),
        value=int(value),
        data=data_bytes,
    )
    safe_tx_hash = safe_tx.safe_tx_hash.hex()

    return {
        "safe_tx_hash": safe_tx_hash if safe_tx_hash.startswith("0x") else f"0x{safe_tx_hash}",
        "to": to,
        "value": str(value),
        "data": data,
    }

def list_safes_for_user(user_id: str) -> list:
    """Return all Safes owned/tracked for a given user."""
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute("""
                SELECT safe_address, chain, owners, threshold
                FROM user_safes
                WHERE user_id = %s
            """, (user_id,))
            rows = c.fetchall()
    return [
        {
            "safe_address": r[0],
            "chain": r[1],
            "owners": r[2] if isinstance(r[2], list) else json.loads(r[2]),
            "threshold": r[3],
        }
        for r in rows
    ]

@router.post("/propose")
async def propose(req: dict, user=Depends(get_current_user)):
    """
    Propose a new Safe transaction (multisig).
    """
    if not user:
        raise HTTPException(401, "Authentication required")

    safe_address = req.get("safe_address")
    to = req.get("to")
    value = req.get("value", 0)          # in wei
    data = req.get("data", "0x")
    chain = req.get("chain", "polygon")

    # Validate ownership
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute("""
                SELECT threshold, owners FROM user_safes
                WHERE safe_address = %s AND user_id = %s
            """, (safe_address, user["id"]))
            row = c.fetchone()
            if not row:
                raise HTTPException(403, "You do not own this Safe")
            threshold = row[0]
            owners = row[1]  # JSONB list

    # Build SafeTx using safe-eth-py
    safe = get_safe_contract(safe_address, chain)
    safe_tx_obj = propose_safe_transaction(safe_address, to, value, data, chain)
    safe_tx_hash = safe_tx_obj["safe_tx_hash"]  # hex string

    # Store in DB
    tx_id = str(uuid.uuid4())
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute("""
                INSERT INTO safe_transactions (
                    id, safe_address, chain, to_address, value, data,
                    safe_tx_hash, status, threshold, signers, signatures,
                    user_id, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                tx_id,
                safe_address,
                chain,
                to,
                str(value),
                data,
                safe_tx_hash,
                "pending",
                threshold,
                json.dumps([]),   # signers (addresses)
                json.dumps([]),   # signatures (objects)
                user["id"],
                datetime.utcnow().isoformat()
            ))
            conn.commit()

    return {
        "id": tx_id,
        "safe_tx_hash": safe_tx_hash,
        "threshold": threshold,
        "message": "Proposal created"
    }

@router.post("/sign")
async def sign(req: dict, user=Depends(get_current_user)):
    """
    Sign a pending Safe transaction.
    The frontend provides a signature of the safe_tx_hash.
    """
    if not user:
        raise HTTPException(401, "Authentication required")

    tx_id = req.get("tx_id")
    signature = req.get("signature")   # hex signature (65 bytes)

    with get_db() as conn:
        with conn.cursor() as c:
            c.execute("""
                SELECT safe_tx_hash, signers, signatures, threshold, status
                FROM safe_transactions
                WHERE id = %s AND status = 'pending'
            """, (tx_id,))
            row = c.fetchone()
            if not row:
                raise HTTPException(404, "Proposal not found or already executed")

            safe_tx_hash, signers_json, signatures_json, threshold, status = row
            signers = json.loads(signers_json) if signers_json else []
            signatures = json.loads(signatures_json) if signatures_json else []

            if user["wallet_address"] in signers:
                raise HTTPException(400, "Already signed")

            # Append signature
            signers.append(user["wallet_address"])
            signatures.append({
                "signer": user["wallet_address"],
                "signature": signature
            })

            c.execute("""
                UPDATE safe_transactions
                SET signers = %s, signatures = %s
                WHERE id = %s
            """, (json.dumps(signers), json.dumps(signatures), tx_id))
            conn.commit()

    return {
        "message": "Signed",
        "signers": signers,
        "threshold": threshold,
        "signature_count": len(signers)
    }

@router.post("/execute")
async def execute(req: dict, user=Depends(get_current_user)):
    """
    Execute a Safe transaction once threshold is met.
    Combines signatures and broadcasts the execution transaction.
    """
    if not user:
        raise HTTPException(401, "Authentication required")

    tx_id = req.get("tx_id")

    with get_db() as conn:
        with conn.cursor() as c:
            c.execute("""
                SELECT safe_address, chain, to_address, value, data, safe_tx_hash,
                       signers, signatures, threshold, status
                FROM safe_transactions
                WHERE id = %s AND status = 'pending'
            """, (tx_id,))
            row = c.fetchone()
            if not row:
                raise HTTPException(404, "Proposal not found")

            (safe_address, chain, to, value, data, safe_tx_hash,
             signers_json, signatures_json, threshold, status) = row

            signers = json.loads(signers_json) if signers_json else []
            signatures = json.loads(signatures_json) if signatures_json else []

            if len(signers) < threshold:
                raise HTTPException(400, f"Not enough signatures: {len(signers)}/{threshold}")

            # Build the SafeTx object
            safe = get_safe_contract(safe_address, chain)
            safe_tx_obj = SafeTx(
                safe_address=safe_address,
                to=to,
                value=int(value),
                data=data,
                operation=0,  # 0 = call
            )
            # Set the safe_tx_hash (to verify we have the right hash)
            safe_tx_obj.safe_tx_hash = bytes.fromhex(safe_tx_hash.replace("0x", ""))

            # Convert signatures to safe-eth-py format: list of (v, r, s) tuples,
            # sorted by signer address ascending (required by the Safe contract).
            sig_tuples = []
            for sig_obj in sorted(signatures, key=lambda s: s["signer"].lower()):
                sig_hex = sig_obj["signature"]  # hex string, with or without 0x
                if sig_hex.startswith("0x"):
                    sig_hex = sig_hex[2:]
                sig_bytes = bytes.fromhex(sig_hex)
                if len(sig_bytes) != 65:
                    raise HTTPException(400, "Invalid signature length")
                r = int.from_bytes(sig_bytes[:32], "big")
                s = int.from_bytes(sig_bytes[32:64], "big")
                v = sig_bytes[64]
                sig_tuples.append((v, r, s))

            # Combine signatures into the packed bytes format the Safe contract expects
            safe_tx_obj.signatures = signatures_to_bytes(sig_tuples)

            # Execute the transaction
            eth_client = EthereumClient(settings.get_rpc_url(chain))
            exec_tx = safe_tx_obj.get_execution_transaction(eth_client)
            safe_contract = get_safe_contract(safe_address, chain)
            web3 = get_web3(chain)
            exec_tx_data = safe_tx_obj.get_execution_transaction_data()
            tx = {
                "to": safe_address,
                "data": exec_tx_data,
                "gas": 500000,
                "gasPrice": web3.eth.gas_price,
                "nonce": web3.eth.get_transaction_count(safe_address, 'pending'),
                "chainId": settings.ONEINCH_CHAIN_IDS.get(chain, 137),
            }
            # Execution is deferred to the frontend, which signs and broadcasts exec_tx_data.

            c.execute("UPDATE safe_transactions SET status = 'executing' WHERE id = %s", (tx_id,))
            conn.commit()

    return {
        "message": "Execution data ready",
        "exec_tx_data": exec_tx_data,
        "safe_address": safe_address,
        "chain": chain,
        "tx_id": tx_id,
    }
