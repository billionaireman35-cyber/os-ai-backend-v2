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
    slippageBps: int = Body(50, description="Slippage tolerance in bps, e.g. 50 = 0.5%"),
    user=Depends(get_current_user)
):
    """
    Encodes the swap into ready-to-sign calldata. Does NOT sign or
    broadcast anything - returns {to, data, value} for the frontend to sign
    with the user's own wallet (via /wallet/send-style signing), matching
    the non-custodial pattern used everywhere else in this app. Matches
    KyberSwap's [V1] Post Swap Route For Encoded Data.
    """
    if not user:
        raise HTTPException(401, "Authentication required")
    if chain not in settings.SUPPORTED_CHAINS:
        raise HTTPException(400, f"Unsupported chain: {chain}")
    if not fromAddress:
        raise HTTPException(400, "fromAddress is required")

    url = f"{KYBERSWAP_API_BASE}/{chain}/api/v1/route/build"
    headers = {"X-Client-Id": KYBERSWAP_CLIENT_ID, "Content-Type": "application/json"}
    body = {
        "routeSummary": routeSummary,
        "sender": fromAddress,
        "recipient": fromAddress,
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
    wallet_address: str = Body(None, description="Swap from a specific imported wallet instead of the primary wallet"),
    user=Depends(get_current_user)
):
    """
    Signs and broadcasts the swap using the user's own wallet - the final
    step after /quote and /swap have prepared the route and calldata. Same
    non-custodial pattern as /wallet/send: password decrypts the user's own
    key locally in this request, never stored, never sent anywhere else.
    """
    if not user:
        raise HTTPException(401, "Authentication required")
    if len(password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters")

    from app.services.wallet_service import sign_and_broadcast_swap
    try:
        tx_hash = sign_and_broadcast_swap(
            user_id=user["id"],
            password=password,
            chain=chain,
            to_address=to,
            data=data,
            value_wei=int(value),
            wallet_address=wallet_address,
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
    wallet_address: str = Body(None, description="Send from a specific imported wallet instead of the primary wallet"),
    user=Depends(get_current_user)
):
    """
    Sends CLOSE with the relayer paying gas, for wallets that don't hold
    POL. First call for a given wallet also runs a one-time bootstrap
    (relayer drips a little POL, wallet approves the relayer to move
    CLOSE) - transparent to the caller, just adds a bit of latency on the
    first sponsored send only. Capped at a few sends per wallet per day;
    see gas_sponsor.DAILY_SPONSORED_TX_CAP.
    """
    if not user:
        raise HTTPException(401, "Authentication required")
    if len(password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters")
    if amount <= 0:
        raise HTTPException(400, "Amount must be greater than 0")
    if not to_address:
        raise HTTPException(400, "to_address is required")

    from app.services.wallet_service import get_user_private_key
    from app.services import gas_sponsor

    try:
        if wallet_address:
            # Ownership verified inside get_user_private_key below (query
            # is scoped to user_id AND address - raises if not owned).
            user_address = wallet_address
        else:
            with get_db() as conn:
                with conn.cursor() as c:
                    c.execute("SELECT wallet_address FROM users WHERE id = %s", (user["id"],))
                    row = c.fetchone()
                    if not row or not row[0]:
                        raise HTTPException(400, "No wallet address found")
                    user_address = row[0]

        private_key = get_user_private_key(user["id"], password, wallet_address)

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
