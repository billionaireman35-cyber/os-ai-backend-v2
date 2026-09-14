from app.security.types import SecurityAction, SecurityContext, SecurityDecision, SecurityResult
from app.security.policy import block
from app.core.database import get_db
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

        if context.resource_type == "goldx_company_wallet":
            purpose = context.metadata.get("goldx_purpose")
            if not purpose:
                return block("unauthorized")

            try:
                with get_db() as conn:
                    with conn.cursor() as c:
                        c.execute("""
                            SELECT u.is_founder, u.stake_tier, w.purpose, w.is_active
                            FROM users u
                            JOIN goldx_company_wallets w ON w.id = %s::uuid
                            WHERE u.id = %s
                            LIMIT 1
                        """, (context.wallet_id, context.user_id))
                        row = c.fetchone()
            except Exception:
                return block("unauthorized")

            if not row:
                return block("unauthorized")

            is_founder, stake_tier, wallet_purpose, is_active = row

            if not is_founder and stake_tier != "founder":
                return block("unauthorized")

            if not is_active or wallet_purpose != purpose:
                return block("unauthorized")

            return SecurityResult(
                decision=SecurityDecision.ALLOW,
                metadata={
                    "wallet_type": "goldx_company",
                    "wallet_authorized": True,
                    "goldx_purpose": wallet_purpose,
                },
            )

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
