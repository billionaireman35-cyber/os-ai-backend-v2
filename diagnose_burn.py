"""Standalone diagnostic - simulates the exact transfer call burn_close()
makes, with full exception detail. Run from ~/OS-AI-Backend:
    python diagnose_burn.py
"""
from web3 import Web3
from eth_utils import to_checksum_address
from app.core.config import settings

ERC20_ABI = [
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
]

DEAD_ADDRESS = to_checksum_address("0x000000000000000000000000000000000000dEaD")

web3 = Web3(Web3.HTTPProvider(settings.POLYGON_RPC_URL))
print("Connected:", web3.isConnected())

contract_address = to_checksum_address(settings.CLOSE_CONTRACT_ADDRESS)
from_address = to_checksum_address(settings.DISTRIBUTION_WALLET_ADDRESS)
contract = web3.eth.contract(address=contract_address, abi=ERC20_ABI)

print("Contract address:", contract_address)
print("From address:", from_address)
print("Dead address:", DEAD_ADDRESS)

balance = contract.functions.balanceOf(from_address).call()
print("Distribution wallet CLOSE balance (raw wei):", balance)
print("Distribution wallet CLOSE balance (whole):", balance / 10**18)

amount_wei = int(25 * 10**18)
print("Attempting to transfer (wei):", amount_wei)

try:
    result = contract.functions.transfer(DEAD_ADDRESS, amount_wei).call({'from': from_address})
    print("call() succeeded, would return:", result)
except Exception as e:
    print("call() FAILED. Full exception:")
    print(repr(e))

try:
    gas = contract.functions.transfer(DEAD_ADDRESS, amount_wei).estimate_gas({'from': from_address})
    print("estimate_gas succeeded:", gas)
except Exception as e:
    print("estimate_gas FAILED. Full exception:")
    print(repr(e))
