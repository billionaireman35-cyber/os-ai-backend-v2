from fastapi import APIRouter
from app.api.v1 import auth, chat, wallet, swap, bridge, market, admin
from app.api.v1 import developer
from app.api.v1 import wc
# from app.services import safe

router = APIRouter()
router.include_router(auth.router, prefix="/auth", tags=["Authentication"])
router.include_router(chat.router, prefix="/chat", tags=["Chat"])
router.include_router(wallet.router, prefix="/wallet", tags=["Wallet"])
router.include_router(wallet.wallet_router, prefix="/wallet", tags=["Wallet"])
router.include_router(swap.router, prefix="/swap", tags=["Swap"])
router.include_router(bridge.router, prefix="/bridge", tags=["Bridge"])
router.include_router(market.router, prefix="/market", tags=["Market"])
router.include_router(admin.router, prefix="/admin", tags=["Admin"])
# router.include_router(safe.router, prefix="/wallet")
router.include_router(developer.router)
router.include_router(wc.router)

api_router = router
