from fastapi import APIRouter
from app.api.v1 import auth, chat, wallet, swap, bridge, market, admin, safe
from app.api.v1 import developer
from app.api.v1 import wc

router.include_router(wc.router)
router = APIRouter()
router.include_router(auth.router, prefix="/auth", tags=["Authentication"])
router.include_router(chat.router, prefix="/chat", tags=["Chat"])
router.include_router(wallet.router, prefix="/wallet", tags=["Wallet"])
router.include_router(swap.router, prefix="/swap", tags=["Swap"])
router.include_router(bridge.router, prefix="/bridge", tags=["Bridge"])
router.include_router(market.router, prefix="/market", tags=["Market"])
router.include_router(admin.router, prefix="/admin", tags=["Admin"])
router.include_router(safe.router)   # <-- New Safe router
router.include_router(developer.router)