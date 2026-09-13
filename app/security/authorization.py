from app.security.types import SecurityAction, SecurityContext, SecurityDecision, SecurityResult
from app.security.policy import block
FINANCIAL_ACTIONS = frozenset({
    SecurityAction.WALLET_SIGN.value,
    SecurityAction.TRANSACTION.value,
    SecurityAction.SAFE_OPERATION.value,
    SecurityAction.CLOSE_TRANSFER.value,
    SecurityAction.WITHDRAWAL.value,
})


def authorize(context: SecurityContext) -> SecurityResult:
    """Deterministically verify that the authenticated user may perform the action."""
    action = context.action.value if isinstance(context.action, SecurityAction) else str(context.action)

    if not context.user_id:
        return block("unauthorized")

    if action in FINANCIAL_ACTIONS:
        if not context.wallet_id:
            return block("unauthorized")
        from app.services.wallet_identity import resolve_wallet_identity
        try:
            wallet = resolve_wallet_identity(
                user_id=context.user_id,
                wallet_id=context.wallet_id,
            )
        except (ValueError, TypeError):
            return block("unauthorized")

        if not wallet.get("is_active"):
            return block("unauthorized")

        return SecurityResult(
            decision=SecurityDecision.ALLOW,
            metadata={
                "wallet_type": wallet.get("wallet_type"),
                "wallet_authorized": True,
            },
        )

    return SecurityResult(
        decision=SecurityDecision.ALLOW,
        metadata={"authorization_verified": True},
    )
