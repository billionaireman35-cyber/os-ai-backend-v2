"""
Gnosis Safe integration using plain web3.py + eth_account, instead of
safe-eth-py (which requires Pydantic v2 and a Rust toolchain that cannot
build on this Android/Termux environment).

Contract addresses verified against the official safe-global/safe-deployments
GitHub registry and cross-checked against live, verified contracts on
Etherscan/Polygonscan/BscScan/Arbiscan/BaseScan (2026-08-16).
"""
import json
import logging
from eth_utils import to_checksum_address
from app.services.blockchain import get_web3, send_raw_tx, get_wallet_lock
from app.core.config import settings, get_safe_singleton, get_safe_proxy_factory

logger = logging.getLogger(__name__)

ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"

PROXY_FACTORY_ABI = json.loads('''[
  {"inputs":[{"internalType":"address","name":"singleton","type":"address"},{"internalType":"bytes","name":"data","type":"bytes"}],"name":"createProxy","outputs":[{"internalType":"contract GnosisSafeProxy","name":"proxy","type":"address"}],"stateMutability":"nonpayable","type":"function"},
  {"anonymous":false,"inputs":[{"indexed":false,"internalType":"contract GnosisSafeProxy","name":"proxy","type":"address"},{"indexed":false,"internalType":"address","name":"singleton","type":"address"}],"name":"ProxyCreation","type":"event"}
]''')

SAFE_SETUP_ABI = json.loads('''[
  {"inputs":[
     {"internalType":"address[]","name":"_owners","type":"address[]"},
     {"internalType":"uint256","name":"_threshold","type":"uint256"},
     {"internalType":"address","name":"to","type":"address"},
     {"internalType":"bytes","name":"data","type":"bytes"},
     {"internalType":"address","name":"fallbackHandler","type":"address"},
     {"internalType":"address","name":"paymentToken","type":"address"},
     {"internalType":"uint256","name":"payment","type":"uint256"},
     {"internalType":"address","name":"paymentReceiver","type":"address"}
   ],"name":"setup","outputs":[],"stateMutability":"nonpayable","type":"function"}
]''')


def create_safe(owners: list, threshold: int, chain: str = "polygon") -> str:
    """Deploy a new Gnosis Safe with the given owners/threshold. Gas is
    paid by the distribution wallet. Returns the deployed Safe's address."""
    if threshold < 1 or threshold > len(owners):
        raise ValueError("threshold must be between 1 and the number of owners")

    web3 = get_web3(chain)
    deployer_pk = settings.DISTRIBUTION_WALLET_PRIVATE_KEY
    deployer_address = to_checksum_address(settings.DISTRIBUTION_WALLET_ADDRESS)

    singleton_address = to_checksum_address(get_safe_singleton(chain))
    factory_address = to_checksum_address(get_safe_proxy_factory(chain))
    checksum_owners = [to_checksum_address(o) for o in owners]

    singleton_contract = web3.eth.contract(address=singleton_address, abi=SAFE_SETUP_ABI)
    setup_data = singleton_contract.encodeABI(fn_name="setup", args=[
        checksum_owners, threshold, ZERO_ADDRESS, b"", ZERO_ADDRESS, ZERO_ADDRESS, 0, ZERO_ADDRESS
    ])

    factory_contract = web3.eth.contract(address=factory_address, abi=PROXY_FACTORY_ABI)

    lock = get_wallet_lock(deployer_address)
    with lock:
        nonce = web3.eth.get_transaction_count(deployer_address, 'pending')
        tx = factory_contract.functions.createProxy(singleton_address, setup_data).build_transaction({
            'from': deployer_address,
            'nonce': nonce,
            'gasPrice': web3.eth.gas_price,
            'chainId': 137,
        })
        gas_estimate = web3.eth.estimate_gas(tx)
        tx['gas'] = int(gas_estimate * 1.3)
        tx_hash = send_raw_tx(web3, deployer_pk, tx)

    logger.info(f"Safe deployment tx sent: {tx_hash}")
    receipt = web3.eth.wait_for_transaction_receipt(tx_hash, timeout=180)
    if receipt.status != 1:
        raise Exception(f"Safe deployment transaction reverted. Tx: {tx_hash}")

    logs = factory_contract.events.ProxyCreation().processReceipt(receipt)
    if not logs:
        raise Exception(f"ProxyCreation event not found in receipt. Tx: {tx_hash}")

    safe_address = logs[0]['args']['proxy']
    logger.info(f"Safe deployed at {safe_address} (owners={checksum_owners}, threshold={threshold})")
    return safe_address
