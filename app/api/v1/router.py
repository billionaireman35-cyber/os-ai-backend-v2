from fastapi import APIRouter
from app.api.v1 import auth, chat, wallet, swap, bridge, market, admin, founder
from app.api.v1 import developer, wc, founder, workspace, notifications, leaderboard, founder

router = APIRouter()
router.include_router(auth.router, prefix="/auth", tags=["Authentication"])
router.include_router(chat.router, prefix="/chat", tags=["Chat"])
router.include_router(wallet.wallet_router, prefix="/wallet", tags=["Wallet"])
router.include_router(swap.router, prefix="/swap", tags=["Swap"])
router.include_router(bridge.router, prefix="/bridge", tags=["Bridge"])
router.include_router(market.router, prefix="/market", tags=["Market"])
router.include_router(notifications.router, prefix="/notifications", tags=["Notifications"])
router.include_router(leaderboard.router, prefix="/leaderboard", tags=["Leaderboard"])
router.include_router(workspace.router, prefix="/workspace", tags=["Workspace"])
router.include_router(admin.router, prefix="/admin", tags=["Admin"])
router.include_router(developer.router)
router.include_router(wc.router)
router.include_router(founder.router, prefix="/founder", tags=["Founder"])

api_router = router
