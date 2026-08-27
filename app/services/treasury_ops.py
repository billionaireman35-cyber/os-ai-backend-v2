# app/services/treasury_ops.py
#
# Keeps the relayer and distribution wallets topped up with POL for gas by
# auto-swapping a small amount of their own CLOSE holdings via KyberSwap
# when their POL balance drops below a threshold. Runs opportunistically -
# checked right before a wallet is about to spend POL on gas (see call
# sites in gas_sponsor.py and wallet_service.py) rather than on a
# schedule, since this repo has no cron/background worker.

import logging
import requests
from eth_utils import to_checksum_address

from app.core.config import settings
from app.services.blockchain import get_web3, get_wallet_lock, send_raw_tx, ERC20_ABI

logger = logging.getLogger(__name__)

KYBERSWAP_API_BASE = "https://aggregator-api.kyberswap.com"
KYBERSWAP_CLIENT_ID = "OS-AI"

# KyberSwap's convention for "native token" (POL on Polygon) in a route.
NATIVE_TOKEN_ADDRESS = "0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE"

# Refill below this POL balance...
GAS_REFILL_THRESHOLD_POL = getattr(settings, "GAS_REFILL_THRESHOLD_POL", 0.5)
# ...by swapping this much CLOSE into POL.
GAS_REFILL_CLOSE_AMOUNT = getattr(settings, "GAS_REFILL_CLOSE_AMOUNT", 200)


class TreasuryOpsError(Exception):
    pass


def _get_pol_balance(web3, address: str) -> float:
    balance_wei = web3.eth.get_balance(to_checksum_address(address))
    return balance_wei / 10**18


def _get_close_balance(web3, address: str) -> float:
    contract = web3.eth.contract(
        address=to_checksum_address(settings.CLOSE_CONTRACT_ADDRESS), abi=ERC20_ABI
    )
    balance_wei = contract.functions.balanceOf(to_checksum_address(address)).call()
    return balance_wei / 10**18


def _kyberswap_quote(chain: str, from_token: str, to_token: str, amount_wei: int) -> dict:
    url = f"{KYBERSWAP_API_BASE}/{chain}/api/v1/routes"
    params = {"tokenIn": from_token, "tokenOut": to_token, "amountIn": str(amount_wei)}
    headers = {"X-Client-Id": KYBERSWAP_CLIENT_ID}
    resp = requests.get(url, headers=headers, params=params, timeout=10)
    if resp.status_code != 200:
        raise TreasuryOpsError(f"KyberSwap route error: {resp.text}")
    data = resp.json()
    route_summary = data.get("data", {}).get("routeSummary")
    if not route_summary:
        raise TreasuryOpsError("No swap route found for CLOSE -> POL")
    return {"routeSummary": route_summary, "routerAddress": data["data"]["routerAddress"]}


def _kyberswap_build(chain: str, route_summary: dict, sender: str, slippage_bps: int = 100) -> dict:
    url = f"{KYBERSWAP_API_BASE}/{chain}/api/v1/route/build"
    headers = {"X-Client-Id": KYBERSWAP_CLIENT_ID, "Content-Type": "application/json"}
    body = {
        "routeSummary": route_summary,
        "sender": sender,
        "recipient": sender,
        "slippageTolerance": slippage_bps,
    }
    resp = requests.post(url, headers=headers, json=body, timeout=15)
    if resp.status_code != 200:
        raise TreasuryOpsError(f"KyberSwap build error: {resp.text}")
    return resp.json()["data"]


def _approve_router_if_needed(web3, wallet_address: str, private_key: str, router_address: str, amount_wei: int):
    contract = web3.eth.contract(
        address=to_checksum_address(settings.CLOSE_CONTRACT_ADDRESS), abi=ERC20_ABI
    )
    allowance = contract.functions.allowance(
        to_checksum_address(wallet_address), to_checksum_address(router_address)
    ).call()
    if allowance >= amount_wei:
        return

    lock = get_wallet_lock(wallet_address)
    with lock:
        nonce = web3.eth.get_transaction_count(to_checksum_address(wallet_address), 'pending')
        approve_amount = 2**200  # large allowance, same pattern as gas_sponsor.py
        gas_estimate = contract.functions.approve(
            to_checksum_address(router_address), approve_amount
        ).estimate_gas({'from': to_checksum_address(wallet_address)})
        tx = contract.functions.approve(to_checksum_address(router_address), approve_amount).build_transaction({
            'from': to_checksum_address(wallet_address),
            'nonce': nonce,
            'gas': int(gas_estimate * 1.2),
            'gasPrice': web3.eth.gas_price,
        })
        approve_hash = send_raw_tx(web3, private_key, tx)
    logger.info(f"Treasury wallet {wallet_address} approved router {router_address}, tx: {approve_hash}")


def ensure_gas_funded(wallet_address: str, private_key: str, label: str = "treasury") -> dict:
    """
    Checks the given wallet's POL balance. If below GAS_REFILL_THRESHOLD_POL,
    swaps GAS_REFILL_CLOSE_AMOUNT worth of the wallet's own CLOSE into POL
    via KyberSwap. Call this right before any operation that will spend
    this wallet's POL on gas (e.g. before a relayer send, or before the
    distribution wallet sends the signup bonus).

    Returns {"refilled": bool, "tx_hash": str|None, "pol_balance": float}.
    Raises TreasuryOpsError only on a genuine failure to refill when a
    refill was actually needed and attempted - a wallet that already has
    enough POL, or one with insufficient CLOSE to swap, is reported back
    in the return value rather than raised, so callers can log and
    continue rather than crash the operation that triggered the check.
    """
    web3 = get_web3("polygon")
    wallet_address = to_checksum_address(wallet_address)

    pol_balance = _get_pol_balance(web3, wallet_address)
    if pol_balance >= GAS_REFILL_THRESHOLD_POL:
        return {"refilled": False, "tx_hash": None, "pol_balance": pol_balance}

    logger.warning(
        f"{label} wallet {wallet_address} POL balance ({pol_balance:.4f}) below "
        f"threshold ({GAS_REFILL_THRESHOLD_POL}) - attempting auto-refill from CLOSE"
    )

    close_balance = _get_close_balance(web3, wallet_address)
    if close_balance < GAS_REFILL_CLOSE_AMOUNT:
        logger.error(
            f"{label} wallet {wallet_address} has insufficient CLOSE "
            f"({close_balance:.2f}) to auto-refill gas (needs {GAS_REFILL_CLOSE_AMOUNT}). "
            f"Manual top-up required."
        )
        return {"refilled": False, "tx_hash": None, "pol_balance": pol_balance}

    try:
        amount_wei = int(GAS_REFILL_CLOSE_AMOUNT * 10**18)
        quote = _kyberswap_quote(
            "polygon", settings.CLOSE_CONTRACT_ADDRESS, NATIVE_TOKEN_ADDRESS, amount_wei
        )

        _approve_router_if_needed(
            web3, wallet_address, private_key, quote["routerAddress"], amount_wei
        )

        built = _kyberswap_build("polygon", quote["routeSummary"], wallet_address)

        lock = get_wallet_lock(wallet_address)
        with lock:
            nonce = web3.eth.get_transaction_count(wallet_address, 'pending')
            tx = {
                'from': wallet_address,
                'to': to_checksum_address(built["routerAddress"]),
                'data': built["data"],
                'value': int(built.get("transactionValue", "0")),
                'nonce': nonce,
                'gas': 500000,  # swap calldata gas varies; generous fixed cap for a treasury op
                'gasPrice': web3.eth.gas_price,
            }
            tx_hash = send_raw_tx(web3, private_key, tx)

        logger.info(
            f"Auto-refilled {label} wallet {wallet_address}: swapped "
            f"{GAS_REFILL_CLOSE_AMOUNT} CLOSE -> POL, tx: {tx_hash}"
        )
        return {"refilled": True, "tx_hash": tx_hash, "pol_balance": pol_balance}

    except Exception as e:
        logger.error(f"Auto-refill failed for {label} wallet {wallet_address}: {e}")
        return {"refilled": False, "tx_hash": None, "pol_balance": pol_balance}
