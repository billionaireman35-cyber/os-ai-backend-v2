"""
Deploy CloseStaking.sol to Polygon mainnet using the distribution wallet
as deployer/owner. Run from ~/OS-AI-Backend:

    python contracts/deploy_staking.py

This costs real gas (POL) from the distribution wallet. It will show you
the exact estimated cost and ask for explicit confirmation before sending
anything.
"""
import json
import os
from web3 import Web3
from app.core.config import settings

HERE = os.path.dirname(os.path.abspath(__file__))

with open(os.path.join(HERE, "CloseStaking.abi.json")) as f:
    ABI = json.load(f)
with open(os.path.join(HERE, "CloseStaking.bytecode.txt")) as f:
    BYTECODE = "0x" + f.read().strip()

def main():
    web3 = Web3(Web3.HTTPProvider(settings.POLYGON_RPC_URL))
    assert web3.isConnected(), "Could not connect to Polygon RPC - check POLYGON_RPC_URL"

    deployer = Web3.toChecksumAddress(settings.DISTRIBUTION_WALLET_ADDRESS)
    private_key = settings.DISTRIBUTION_WALLET_PRIVATE_KEY
    close_token_address = Web3.toChecksumAddress(settings.CLOSE_CONTRACT_ADDRESS)

    balance = web3.eth.get_balance(deployer)
    print(f"Deployer: {deployer}")
    print(f"POL balance: {web3.fromWei(balance, 'ether')}")
    print(f"CLOSE token address (constructor arg): {close_token_address}")

    contract = web3.eth.contract(abi=ABI, bytecode=BYTECODE)
    nonce = web3.eth.get_transaction_count(deployer, 'pending')

    tx = contract.constructor(close_token_address).build_transaction({
        'from': deployer,
        'nonce': nonce,
        'gasPrice': web3.eth.gas_price,
    })

    gas_estimate = web3.eth.estimate_gas(tx)
    tx['gas'] = int(gas_estimate * 1.2)

    est_cost_pol = web3.fromWei(tx['gas'] * tx['gasPrice'], 'ether')
    print(f"Estimated gas: {gas_estimate} (with 20% buffer: {tx['gas']})")
    print(f"Estimated cost: {est_cost_pol} POL")

    confirm = input("Type DEPLOY to send this transaction: ")
    if confirm != "DEPLOY":
        print("Aborted.")
        return

    signed = web3.eth.account.sign_transaction(tx, private_key)
    tx_hash = web3.eth.send_raw_transaction(signed.rawTransaction)
    print(f"Sent. Tx hash: {web3.toHex(tx_hash)}")
    print("Waiting for confirmation...")

    receipt = web3.eth.wait_for_transaction_receipt(tx_hash, timeout=180)
    if receipt.status == 1:
        print(f"SUCCESS. Contract deployed at: {receipt.contractAddress}")
        print(f"Add this to your .env as: CLOSE_STAKING_CONTRACT_ADDRESS={receipt.contractAddress}")
    else:
        print("Deployment transaction FAILED (reverted). Check Polygonscan for the tx hash above.")

if __name__ == "__main__":
    main()
