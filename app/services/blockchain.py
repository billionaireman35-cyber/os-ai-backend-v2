# app/services/blockchain.py – full version with all required functions
from web3 import Web3
from eth_utils import to_checksum_address
from app.core.config import settings
import threading
import logging

logger = logging.getLogger(__name__)

# ERC-20 ABI (includes burn, transfer, balanceOf, decimals)
ERC20_ABI = [
    {
        "constant": False,
        "inputs": [{"name": "amount", "type": "uint256"}],
        "name": "burn",
        "outputs": [{"name": "", "type": "bool"}],
        "type": "function"
    },
    {
        "constant": False,
        "inputs": [
            {"name": "recipient", "type": "address"},
            {"name": "amount", "type": "uint256"}
        ],
        "name": "transfer",
        "outputs": [{"name": "", "type": "bool"}],
        "type": "function"
    },
    {
        "constant": True,
        "inputs": [{"name": "owner", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "", "type": "uint256"}],
        "type": "function"
    },
    {
        "constant": True,
        "inputs": [],
        "name": "decimals",
        "outputs": [{"name": "", "type": "uint8"}],
        "type": "function"
    }
]

# RPC URL map (extend as needed)
def get_web3(chain: str):
    """
    Returns a connected Web3 instance for the given chain, trying each
    configured RPC URL in order (Alchemy -> Infura -> public fallback, per
    settings.get_rpc_urls()) until one actually responds to a real request.
    Added 2026-08-20: a free public RPC (polygon-rpc.com) returned a
    stale/incorrect zero balance during a real send, causing a failed
    broadcast despite the wallet genuinely having funds. Testing
    connectivity here - not just constructing the object - catches that
    class of failure before it reaches a real transaction.
    """
    urls = settings.get_rpc_urls(chain)
    if not urls:
        raise ValueError(f"No RPC URL configured for chain: {chain}")

    last_error = None
    for url in urls:
        try:
            web3 = Web3(Web3.HTTPProvider(url, request_kwargs={'timeout': 15}))
            # Real connectivity check, not just object construction -
            # this is what actually catches a bad/stale RPC endpoint.
            web3.eth.block_number
            return web3
        except Exception as e:
            logger.warning(f"RPC endpoint failed for {chain} ({url.split('/v')[0] if '/v' in url else url}): {e}")
            last_error = e
            continue

    raise ConnectionError(f"All RPC endpoints failed for chain {chain}: {last_error}")

def send_raw_tx(web3, private_key, tx):
    signed = web3.eth.account.sign_transaction(tx, private_key)
    tx_hash = web3.eth.send_raw_transaction(signed.rawTransaction)
    return web3.toHex(tx_hash)

# Simple lock for nonce management (thread-safe per wallet)
_wallet_locks = {}
def get_wallet_lock(address):
    if address not in _wallet_locks:
        _wallet_locks[address] = threading.Lock()
    return _wallet_locks[address]

def broadcast_signed_transaction(web3, signed_tx):
    """Broadcast a signed transaction and return the tx hash."""
    tx_hash = web3.eth.send_raw_transaction(signed_tx.rawTransaction)
    return web3.toHex(tx_hash)

DEAD_ADDRESS = to_checksum_address("0x000000000000000000000000000000000000dEaD")

def burn_close(amount: int) -> str:
    """'Burn' CLOSE by sending to the dead address - nobody, including
    CloseAI Technologies, holds its private key. This contract has no
    native burn() function (confirmed by reading its verified source),
    so this is the standard workaround real tokens use."""
    web3 = get_web3("polygon")
    contract_address = to_checksum_address(settings.CLOSE_CONTRACT_ADDRESS)
    from_address = to_checksum_address(settings.DISTRIBUTION_WALLET_ADDRESS)
    contract = web3.eth.contract(address=contract_address, abi=ERC20_ABI)
    amount_wei = int(amount * 10**18)
    lock = get_wallet_lock(from_address)
    with lock:
        nonce = web3.eth.get_transaction_count(from_address, 'pending')
        gas_estimate = contract.functions.transfer(DEAD_ADDRESS, amount_wei).estimate_gas({'from': from_address})
        gas_limit = int(gas_estimate * 1.2)
        tx = contract.functions.transfer(DEAD_ADDRESS, amount_wei).build_transaction({
            'from': from_address,
            'nonce': nonce,
            'gas': gas_limit,
            'gasPrice': web3.eth.gas_price
        })
        return send_raw_tx(web3, settings.DISTRIBUTION_WALLET_PRIVATE_KEY, tx)

def send_close_from_distribution(to_address: str, amount: int) -> str:
    """Send CLOSE tokens from the distribution wallet to a user's address."""
    web3 = get_web3("polygon")
    contract_address = to_checksum_address(settings.CLOSE_CONTRACT_ADDRESS)
    from_address = to_checksum_address(settings.DISTRIBUTION_WALLET_ADDRESS)
    to_address = to_checksum_address(to_address)
    contract = web3.eth.contract(address=contract_address, abi=ERC20_ABI)
    amount_wei = int(amount * 10**18)
    lock = get_wallet_lock(from_address)
    with lock:
        nonce = web3.eth.get_transaction_count(from_address, 'pending')
        gas_estimate = contract.functions.transfer(to_address, amount_wei).estimate_gas({'from': from_address})
        gas_limit = int(gas_estimate * 1.2)
        tx = contract.functions.transfer(to_address, amount_wei).build_transaction({
            'from': from_address,
            'nonce': nonce,
            'gas': gas_limit,
            'gasPrice': web3.eth.gas_price
        })
        return send_raw_tx(web3, settings.DISTRIBUTION_WALLET_PRIVATE_KEY, tx)

def get_balance(chain: str, address: str):
    """Get native currency balance for a given chain and address (checksummed)."""
    address = to_checksum_address(address)
    web3 = get_web3(chain)
    balance = web3.eth.get_balance(address)
    return web3.fromWei(balance, 'ether')

def get_token_balance(chain: str, token_address: str, wallet_address: str) -> float:
    """Get ERC-20 token balance for a given chain and wallet."""
    web3 = get_web3(chain)
    token_address = to_checksum_address(token_address)
    wallet_address = to_checksum_address(wallet_address)
    contract = web3.eth.contract(address=token_address, abi=ERC20_ABI)
    try:
        decimals = contract.functions.decimals().call()
        balance_wei = contract.functions.balanceOf(wallet_address).call()
        return balance_wei / (10 ** decimals)
    except Exception:
        # Fallback: assume 18 decimals if `decimals()` fails
        balance_wei = contract.functions.balanceOf(wallet_address).call()
        return web3.from_wei(balance_wei, 'ether')

SYMBOL_MAP = {
    "polygon": "POL", "ethereum": "ETH", "bsc": "BNB",
    "arbitrum": "ETH", "base": "ETH",
}

def get_all_balances(address: str) -> dict:
    """Get native + token balances for all supported chains, in the
    nested shape wallet_service.get_user_balance() expects."""
    address = to_checksum_address(address)
    result = {}
    for chain in settings.SUPPORTED_CHAINS:
        try:
            native_bal = float(get_balance(chain, address))
        except Exception as e:
            logger.error(f"get_balance failed for {chain}/{address}: {e}")
            native_bal = 0.0

        result[chain] = {
            "native": {"symbol": SYMBOL_MAP.get(chain, chain.upper()), "balance": native_bal},
            "tokens": {}
        }

        if chain == "polygon":
            token_list = [
                {"symbol": "CLOSE", "address": settings.CLOSE_CONTRACT_ADDRESS},
                {"symbol": "USDC", "address": "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"},
            ]
            for token in token_list:
                try:
                    bal = get_token_balance(chain, token["address"], address)
                    if bal > 0:
                        result[chain]["tokens"][token["symbol"]] = {
                            "address": token["address"], "balance": bal
                        }
                except Exception:
                    pass
    return result

# ── CLOSE staking discount tiers ──────────────────────────────────────────
STAKING_ABI = [
    {
        "inputs": [{"internalType": "address", "name": "user", "type": "address"}],
        "name": "getDiscountTier",
        "outputs": [{"internalType": "uint8", "name": "", "type": "uint8"}],
        "stateMutability": "view",
        "type": "function"
    }
]

_discount_tier_cache = {}
_DISCOUNT_TIER_CACHE_TTL = 60  # seconds

def get_discount_tier(user_address: str) -> int:
    """Read the caller's staking discount tier (0-3) from CloseStaking.
    Cached briefly to avoid an RPC call on every single chat message."""
    import time
    now = time.time()
    cached = _discount_tier_cache.get(user_address)
    if cached and (now - cached[1]) < _DISCOUNT_TIER_CACHE_TTL:
        return cached[0]

    if not getattr(settings, "CLOSE_STAKING_CONTRACT_ADDRESS", None):
        return 0

    try:
        web3 = get_web3("polygon")
        contract = web3.eth.contract(
            address=to_checksum_address(settings.CLOSE_STAKING_CONTRACT_ADDRESS),
            abi=STAKING_ABI
        )
        tier = contract.functions.getDiscountTier(to_checksum_address(user_address)).call()
        _discount_tier_cache[user_address] = (tier, now)
        return tier
    except Exception:
        return 0

# Discount tier -> percent off BURN_PER_MESSAGE
DISCOUNT_PERCENT_BY_TIER = {0: 0, 1: 5, 2: 15, 3: 30}

def get_effective_burn_amount(user_address: str, base_amount: int) -> int:
    """Apply the user's staking discount to a base CLOSE burn amount."""
    if not user_address:
        return base_amount
    tier = get_discount_tier(user_address)
    discount_pct = DISCOUNT_PERCENT_BY_TIER.get(tier, 0)
    return max(1, int(base_amount * (100 - discount_pct) / 100))
