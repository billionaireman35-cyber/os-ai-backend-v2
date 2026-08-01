from fastapi import APIRouter, Depends
from app.core.security import get_current_user
from app.core.config import settings

router = APIRouter()

# Token list per chain
TOKEN_LISTS = {
    "polygon": [
        {"symbol": "MATIC", "address": "0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE", "decimals": 18, "native": True},
        {"symbol": "USDC", "address": "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174", "decimals": 6},
        {"symbol": "USDT", "address": "0xc2132D05D31c914a87C6611C10748AEb04B58e8F", "decimals": 6},
        {"symbol": "DAI", "address": "0x8f3Cf7ad23Cd3CaDbD9735AFf958023239c6A063", "decimals": 18},
        {"symbol": "WETH", "address": "0x7ceB23fD6bC0adD59E62ac25578270cFf1b9f619", "decimals": 18},
        {"symbol": "CLOSE", "address": "0x3c6833cFDdED80fE76474a3Cb2Cc050Daec91fe8", "decimals": 18},
        {"symbol": "OSINA", "address": "0xbaf280b74c264a911b41341a26508eac9e74fd4f", "decimals": 18},
    ],
    # Add other chains if needed
}

@router.get("/tokens")
async def get_tokens(chain: str = "polygon"):
    return TOKEN_LISTS.get(chain, [])
