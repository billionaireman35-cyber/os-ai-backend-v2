import requests
import json
from app.core.config import settings
import logging

logger = logging.getLogger(__name__)

def rpc_call(chain: str, method: str, params: list) -> dict:
    """Make a JSON-RPC call to the blockchain."""
    rpc_url = settings.get_rpc_url(chain)
    payload = {
        "jsonrpc": "2.0",
        "method": method,
        "params": params,
        "id": 1
    }
    response = requests.post(rpc_url, json=payload, timeout=10)
    response.raise_for_status()
    return response.json()

def get_balance(chain: str, address: str) -> float:
    """Get native currency balance (in ETH/POL/BNB)."""
    try:
        result = rpc_call(chain, "eth_getBalance", [address, "latest"])
        wei = int(result.get("result", "0x0"), 16)
        return wei / 10**18
    except Exception as e:
        logger.error(f"Balance fetch failed for {chain}: {e}")
        return 0.0

def get_token_balance(chain: str, token_address: str, wallet_address: str, decimals: int = 18) -> float:
    """Get ERC-20 token balance using eth_call."""
    # Minimal ERC-20 ABI for balanceOf
    data = "0x70a08231" + "0" * 24 + wallet_address[2:].lower()
    params = [{
        "to": token_address,
        "data": data
    }, "latest"]
    try:
        result = rpc_call(chain, "eth_call", params)
        balance_hex = result.get("result", "0x0")
        balance = int(balance_hex, 16) / 10**decimals
        return balance
    except Exception as e:
        logger.error(f"Token balance fetch failed: {e}")
        return 0.0

def send_transaction(chain: str, signed_tx_hex: str) -> str:
    """Broadcast a signed transaction."""
    try:
        result = rpc_call(chain, "eth_sendRawTransaction", [signed_tx_hex])
        return result.get("result", "")
    except Exception as e:
        logger.error(f"Transaction broadcast failed: {e}")
        raise

# For backward compatibility with existing code that imports from blockchain
def get_web3(chain: str):
    """Placeholder to maintain compatibility."""
    return None

def broadcast_signed_transaction(chain: str, signed_tx_hex: str) -> str:
    return send_transaction(chain, signed_tx_hex)

def get_all_balances(address: str) -> dict:
    """Get balances across all chains."""
    result = {}
    for chain in settings.SUPPORTED_CHAINS:
        try:
            native_balance = get_balance(chain, address)
            result[chain] = {
                "native": {
                    "symbol": chain.capitalize(),
                    "balance": native_balance,
                    "usd": 0.0
                },
                "tokens": {}
            }
        except Exception as e:
            logger.error(f"Failed to fetch balance for chain {chain}: {e}")
            result[chain] = {
                "native": {"symbol": "ERROR", "balance": 0, "usd": 0.0},
                "tokens": {}
            }
    return result

def burn_close(amount: int) -> str:
    """Simulate a burn transaction (placeholder)."""
    # For now, return a fake tx hash
    # Later, we can implement actual CLOSE token burn using the RPC client
    return f"0x{hash(str(amount) + settings.CLOSE_CONTRACT_ADDRESS):064x}"

def send_close_from_distribution(to_address: str, amount: int) -> str:
    """Send CLOSE from distribution wallet (placeholder)."""
    # Simulate for now
    return f"0x{hash(str(amount) + to_address):064x}"
