import requests
from fastapi import APIRouter, Depends, HTTPException, Body
from app.core.config import settings
from app.core.security import get_current_user
from app.core.database import get_db
import logging

router = APIRouter()
logger = logging.getLogger(__name__)

# KyberSwap Aggregator - free, keyless (X-Client-Id is a self-chosen label,
# not a secret). See docs.kyberswap.com/developer-guide/aggregator-api.
# NOTE: KyberSwap's docs mention a newer gated gateway
# (api.kyberswap.com/swap/, requires a requested API key) that this legacy
# free endpoint may eventually be migrated/deprecated toward. If this ever
# stops working, that's the first thing to check.
KYBERSWAP_API_BASE = "https://aggregator-api.kyberswap.com"
KYBERSWAP_CLIENT_ID = "OS-AI"


@router.get("/quote")
async def get_swap_quote(
    chain: str,
    fromTokenAddress: str,
    toTokenAddress: str,
    amount: str,
    user=Depends(get_current_user)
):
    """
    Returns a route preview - price/output estimate only, no calldata yet.
    Matches KyberSwap's [V1] Get Swap Route.
    """
    if not user:
        raise HTTPException(401, "Authentication required")
    if chain not in settings.SUPPORTED_CHAINS:
        raise HTTPException(400, f"Unsupported chain: {chain}")

    url = f"{KYBERSWAP_API_BASE}/{chain}/api/v1/routes"
    params = {
        "tokenIn": fromTokenAddress,
        "tokenOut": toTokenAddress,
        "amountIn": amount,
    }
    headers = {"X-Client-Id": KYBERSWAP_CLIENT_ID}
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=10)
        if resp.status_code != 200:
            logger.error(f"KyberSwap route error: {resp.text}")
            raise HTTPException(400, f"Failed to get quote: {resp.text}")
        data = resp.json()
        route_summary = data.get("data", {}).get("routeSummary")
        if not route_summary:
            raise HTTPException(400, "No route found for this pair")
        return {
            "routeSummary": route_summary,
            "routerAddress": data["data"]["routerAddress"],
            "amountOut": route_summary.get("amountOut"),
            "amountOutUsd": route_summary.get("amountOutUsd"),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"KyberSwap route exception: {e}")
        raise HTTPException(500, "Internal server error")


@router.post("/swap")
async def get_swap_calldata(
    chain: str = Body(...),
    routeSummary: dict = Body(..., description="The routeSummary object exactly as returned by /quote"),
    fromAddress: str = Body(...),
    wallet_id: str = Body(..., description="Stable os_wallets ID for the wallet that should sign"),
    slippageBps: int = Body(50, description="Slippage tolerance in bps, e.g. 50 = 0.5%"),
    user=Depends(get_current_user)
):
    """Build swap calldata for the exact wallet selected by the user."""
    if not user:
        raise HTTPException(401, "Authentication required")
    if chain not in settings.SUPPORTED_CHAINS:
        raise HTTPException(400, f"Unsupported chain: {chain}")
    if not fromAddress:
        raise HTTPException(400, "fromAddress is required")
    if not wallet_id:
        raise HTTPException(400, "wallet_id is required")

    from app.services.wallet_identity import require_signing_wallet
    try:
        wallet = require_signing_wallet(
            user_id=user["id"],
            wallet_id=wallet_id,
            wallet_address=fromAddress,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))

    authoritative_address = wallet["address"]

    url = f"{KYBERSWAP_API_BASE}/{chain}/api/v1/route/build"
    headers = {"X-Client-Id": KYBERSWAP_CLIENT_ID, "Content-Type": "application/json"}
    body = {
        "routeSummary": routeSummary,
        "sender": authoritative_address,
        "recipient": authoritative_address,
        "slippageTolerance": slippageBps,
    }
    try:
        resp = requests.post(url, headers=headers, json=body, timeout=15)
        if resp.status_code != 200:
            logger.error(f"KyberSwap build error: {resp.text}")
            raise HTTPException(400, f"Swap preparation failed: {resp.text}")
        data = resp.json()["data"]
        return {
            "to": data["routerAddress"],
            "data": data["data"],
            "value": data.get("transactionValue", "0"),
            "amountOut": data.get("amountOut"),
            "amountOutUsd": data.get("amountOutUsd"),
            "wallet_id": wallet["id"],
            "wallet_address": authoritative_address,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"KyberSwap build exception: {e}")
        raise HTTPException(500, "Internal server error")

@router.post("/execute")
async def execute_swap(
    chain: str = Body(...),
    to: str = Body(..., description="Router contract address, from /swap response"),
    data: str = Body(..., description="Encoded swap calldata, from /swap response"),
    value: str = Body("0", description="Transaction value in wei, from /swap response"),
    password: str = Body(...),
    wallet_id: str = Body(..., description="Stable os_wallets ID for the wallet that should sign"),
    wallet_address: str = Body(..., description="Authoritative wallet address returned by /swap"),
    user=Depends(get_current_user)
):
    """Sign and broadcast using the exact wallet bound during swap build."""
    if not user:
        raise HTTPException(401, "Authentication required")
    if len(password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters")
    if not wallet_id:
        raise HTTPException(400, "wallet_id is required")
    if not wallet_address:
        raise HTTPException(400, "wallet_address is required")

    from app.services.wallet_identity import require_signing_wallet
    from app.services.wallet_service import sign_and_broadcast_swap
    try:
        wallet = require_signing_wallet(
            user_id=user["id"],
            wallet_id=wallet_id,
            wallet_address=wallet_address,
        )
        tx_hash = sign_and_broadcast_swap(
            user_id=user["id"],
            password=password,
            chain=chain,
            to_address=to,
            data=data,
            value_wei=int(value),
            wallet_id=wallet["id"],
            wallet_address=wallet["address"],
        )
        return {"tx_hash": tx_hash}
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        logger.error(f"Swap execution failed: {e}")
        raise HTTPException(500, f"Swap execution failed: {str(e)}")

@router.post("/send-sponsored")
async def send_sponsored(
    to_address: str = Body(...),
    amount: float = Body(...),
    password: str = Body(...),
    wallet_id: str = Body(..., description="Stable os_wallets ID for the wallet that should sign"),
    user=Depends(get_current_user)
):
    """Send sponsored CLOSE from the exact selected custodial wallet."""
    if not user:
        raise HTTPException(401, "Authentication required")
    if len(password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters")
    if amount <= 0:
        raise HTTPException(400, "Amount must be greater than 0")
    if not to_address:
        raise HTTPException(400, "to_address is required")
    if not wallet_id:
        raise HTTPException(400, "wallet_id is required")

    from app.services.wallet_identity import require_signing_wallet
    from app.services.wallet_service import get_user_private_key
    from app.services import gas_sponsor

    try:
        wallet = require_signing_wallet(
            user_id=user["id"],
            wallet_id=wallet_id,
        )
        user_address = wallet["address"]
        private_key = get_user_private_key(
            user["id"],
            password,
            wallet_id=wallet["id"],
            wallet_address=user_address,
        )

        gas_sponsor.ensure_bootstrapped(user["id"], user_address, private_key)
        result = gas_sponsor.sponsored_close_send(
            user_id=user["id"],
            user_address=user_address,
            to_address=to_address,
            amount=amount,
        )
        return result
    except gas_sponsor.SponsorshipError as e:
        raise HTTPException(400, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Sponsored send failed: {e}")
        raise HTTPException(500, f"Sponsored send failed: {str(e)}")
