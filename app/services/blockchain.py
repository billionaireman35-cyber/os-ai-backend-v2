import json
import threading
import requests
from web3 import Web3
from app.core.config import settings

w3_polygon = Web3(Web3.HTTPProvider(settings.POLYGON_RPC_URL))
w3_ethereum = Web3(Web3.HTTPProvider(settings.ETHEREUM_RPC_URL))
w3_bsc = Web3(Web3.HTTPProvider(settings.BSC_RPC_URL))
w3_arbitrum = Web3(Web3.HTTPProvider(settings.ARBITRUM_RPC_URL))
w3_base = Web3(Web3.HTTPProvider(settings.BASE_RPC_URL))

CHAINS = {
    "polygon": {"web3": w3_polygon, "chain_id": 137, "symbol": "POL", "explorer": "https://polygonscan.com"},
    "ethereum": {"web3": w3_ethereum, "chain_id": 1, "symbol": "ETH", "explorer": "https://etherscan.io"},
    "bsc": {"web3": w3_bsc, "chain_id": 56, "symbol": "BNB", "explorer": "https://bscscan.com"},
    "arbitrum": {"web3": w3_arbitrum, "chain_id": 42161, "symbol": "ETH", "explorer": "https://arbiscan.io"},
    "base": {"web3": w3_base, "chain_id": 8453, "symbol": "ETH", "explorer": "https://basescan.org"},
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

def burn_close_onchain(wallet: str, private_key: str, amount: int) -> str:
    web3 = w3_polygon
    contract = web3.eth.contract(address=settings.CLOSE_CONTRACT_ADDRESS, abi=ERC20_ABI)
    amount_wei = int(amount * 10**18)
    lock = get_wallet_lock(wallet)
    with lock:
        nonce = web3.eth.get_transaction_count(wallet, 'pending')
        tx = contract.functions.burn(amount_wei).build_transaction({
            'from': wallet,
            'nonce': nonce,
            'gas': 100000,
            'gasPrice': web3.eth.gas_price
        })
        return send_raw_tx(web3, private_key, tx)

def broadcast_signed_transaction(chain: str, signed_tx_hex: str) -> str:
    web3 = CHAINS.get(chain, CHAINS["polygon"])["web3"]
    return web3.eth.send_raw_transaction(signed_tx_hex).hex()
