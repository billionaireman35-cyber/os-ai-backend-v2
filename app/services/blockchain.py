import json
import threading
import requests
from web3 import Web3
from app.core.config import settings
import logging

logger = logging.getLogger(__name__)

def get_web3(chain: str) -> Web3:
    rpc = settings.get_rpc_url(chain)
    return Web3(Web3.HTTPProvider(rpc))

CHAINS = {
    "polygon": {"web3": get_web3("polygon"), "chain_id": 137, "symbol": "POL", "explorer": "https://polygonscan.com"},
    "ethereum": {"web3": get_web3("ethereum"), "chain_id": 1, "symbol": "ETH", "explorer": "https://etherscan.io"},
    "bsc": {"web3": get_web3("bsc"), "chain_id": 56, "symbol": "BNB", "explorer": "https://bscscan.com"},
    "arbitrum": {"web3": get_web3("arbitrum"), "chain_id": 42161, "symbol": "ETH", "explorer": "https://arbiscan.io"},
    "base": {"web3": get_web3("base"), "chain_id": 8453, "symbol": "ETH", "explorer": "https://basescan.org"},
}

ERC20_ABI = json.loads('[{"constant":true,"inputs":[{"name":"_owner","type":"address"}],"name":"balanceOf","outputs":[{"name":"balance","type":"uint256"}],"type":"function"},{"constant":false,"inputs":[{"name":"_to","type":"address"},{"name":"_value","type":"uint256"}],"name":"transfer","outputs":[{"name":"","type":"bool"}],"type":"function"},{"constant":false,"inputs":[{"name":"amount","type":"uint256"}],"name":"burn","outputs":[],"type":"function"}]')

wallet_locks = {}

def get_wallet_lock(address: str):
    if address not in wallet_locks:
        wallet_locks[address] = threading.Lock()
    return wallet_locks[address]

def send_raw_tx(web3, private_key, tx):
    signed = web3.eth.account.sign_transaction(tx, private_key)
    return web3.eth.send_raw_transaction(signed.rawTransaction).hex()

def broadcast_signed_transaction(chain: str, signed_tx_hex: str) -> str:
    web3 = CHAINS.get(chain, CHAINS["polygon"])["web3"]
    return web3.eth.send_raw_transaction(signed_tx_hex).hex()

def get_token_balance(chain: str, wallet_address: str, token_address: str, decimals: int) -> float:
    try:
        web3 = get_web3(chain)
        contract = web3.eth.contract(address=token_address, abi=ERC20_ABI)
        balance = contract.functions.balanceOf(wallet_address).call()
        return balance / (10 ** decimals)
    except Exception as e:
        logger.error(f"Token balance fetch failed for {chain}/{token_address}: {e}")
        return 0.0

def get_all_balances(address: str) -> dict:
    result = {}
    for chain_name, chain_data in CHAINS.items():
        web3 = chain_data["web3"]
        native_balance = web3.eth.get_balance(address)
        native_symbol = chain_data["symbol"]
        result[chain_name] = {
            "native": {
                "symbol": native_symbol,
                "balance": web3.from_wei(native_balance, "ether"),
                "usd": 0.0
            },
            "tokens": {}
        }
        # Fetch token balances for known tokens (we'll use a hardcoded list for now)
        # We'll define tokens per chain
        token_list = []
        if chain_name == "polygon":
            token_list = [
                {"address": "0x3c6833cFDdED80fE76474a3Cb2Cc050Daec91fe8", "symbol": "CLOSE", "decimals": 18},
                {"address": "0xbaf280b74c264a911b41341a26508eac9e74fd4f", "symbol": "OSINA", "decimals": 18},
                {"address": "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174", "symbol": "USDC", "decimals": 6},
            ]
        # Add more chains later
        for token in token_list:
            balance = get_token_balance(chain_name, address, token["address"], token["decimals"])
            if balance > 0:
                result[chain_name]["tokens"][token["symbol"]] = {
                    "address": token["address"],
                    "balance": balance,
                    "usd": 0.0
                }
    return result

def send_close_from_distribution(to_address: str, amount: int) -> str:
    """
    Send CLOSE tokens from the distribution wallet to the given address.
    Returns the transaction hash.
    """
    web3 = w3_polygon
    contract = web3.eth.contract(address=settings.CLOSE_CONTRACT_ADDRESS, abi=ERC20_ABI)
    amount_wei = int(amount * 10**18)
    nonce = web3.eth.get_transaction_count(settings.DISTRIBUTION_WALLET_ADDRESS, 'pending')
    tx = contract.functions.transfer(to_address, amount_wei).build_transaction({
        'from': settings.DISTRIBUTION_WALLET_ADDRESS,
        'nonce': nonce,
        'gas': 100000,
        'gasPrice': web3.eth.gas_price
    })
    return send_raw_tx(web3, settings.DISTRIBUTION_WALLET_PRIVATE_KEY, tx)
