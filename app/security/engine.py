from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from app.security.audit import record_security_event
from app.security.authorization import authorize
from app.security.policy import evaluate_risk, block
from app.security.rate_limit import check_rate_limit
from app.security.risk import RiskSignals, calculate_risk


from app.security.types import (
    SecurityAction,
    SecurityContext,
    SecurityDecision,
    SecurityFailure,
    SecurityResult,
    SENSITIVE_ACTIONS,
)


@dataclass(frozen=True)
class RateLimitPolicy:
    limit: int
    window_seconds: int


ACTION_RATE_LIMITS: dict[str, RateLimitPolicy] = {
    SecurityAction.AUTHENTICATE.value: RateLimitPolicy(10, 60),
    SecurityAction.ACCOUNT_CHANGE.value: RateLimitPolicy(5, 60),
    SecurityAction.WALLET_SIGN.value: RateLimitPolicy(10, 60),
    SecurityAction.TRANSACTION.value: RateLimitPolicy(10, 60),
    SecurityAction.SAFE_OPERATION.value: RateLimitPolicy(10, 60),
    SecurityAction.CLOSE_TRANSFER.value: RateLimitPolicy(5, 60),
    SecurityAction.WITHDRAWAL.value: RateLimitPolicy(5, 60),
    SecurityAction.AI_REQUEST.value: RateLimitPolicy(30, 60),
}


def _action_value(action: SecurityAction | str) -> str:
    return action.value if isinstance(action, SecurityAction) else str(action)


SUPPORTED_ACTIONS = frozenset(item.value for item in SecurityAction)


WALLET_SCOPED_ACTIONS = frozenset({
    SecurityAction.WALLET_SIGN.value,
    SecurityAction.TRANSACTION.value,
    SecurityAction.SAFE_OPERATION.value,
    SecurityAction.CLOSE_TRANSFER.value,
    SecurityAction.WITHDRAWAL.value,
})


def _rate_limit_scope(
    action: str,
    context: SecurityContext,
) -> tuple[str, str]:
    if action in WALLET_SCOPED_ACTIONS and context.wallet_id:
        return "user_wallet", f"{context.user_id}:{context.wallet_id}"

    if context.user_id:
        return "user", context.user_id

    if context.ip_address:
        return "ip", context.ip_address

    return "anonymous", "anonymous"


def _audit(
    context: SecurityContext,
    result: SecurityResult,
) -> SecurityResult:
    try:
        audit_id = record_security_event(
            action=_action_value(context.action),
            decision=result.decision.value,
            risk_score=result.risk_score,
            reason_codes=result.reason_codes,
            user_id=context.user_id,
            wallet_id=context.wallet_id,
            resource_type=context.resource_type,
            resource_id=context.resource_id,
            ip_address=context.ip_address,
            device_fingerprint=context.device_fingerprint,
            metadata=result.metadata,
        )
        return SecurityResult(
            decision=result.decision,
            risk_score=result.risk_score,
            reason_codes=result.reason_codes,
            required_action=result.required_action,
            audit_id=audit_id,
            metadata=result.metadata,
        )
    except Exception:
        if _action_value(context.action) in {item.value for item in SENSITIVE_ACTIONS}:
            return block(SecurityFailure.INFRASTRUCTURE.value)
        return result


def evaluate_security(context: SecurityContext) -> SecurityResult:
    """Deterministically evaluate whether an action may proceed."""
    action = _action_value(context.action)
    sensitive = action in {item.value for item in SENSITIVE_ACTIONS}

    if not action or action not in SUPPORTED_ACTIONS or not context.user_id:
        reason = (
            "unsupported_action"
            if action and action not in SUPPORTED_ACTIONS
            else SecurityFailure.INVALID_CONTEXT.value
        )
        result = block(reason)
        return _audit(context, result)

    try:
        authorization = authorize(context)
        if authorization.blocked:
            return _audit(context, authorization)

        rate_policy = ACTION_RATE_LIMITS.get(action)
        if rate_policy:
            scope, subject = _rate_limit_scope(action, context)
            rate = check_rate_limit(
                scope,
                subject,
                action,
                rate_policy.limit,
                rate_policy.window_seconds,
            )
            if not rate.allowed:
                result = block(
                    "rate_limit_exceeded",
                    required_action=f"retry_after:{rate.retry_after_seconds}",
                )
                return _audit(context, result)

        score, reasons = calculate_risk(
            RiskSignals(
                device_trusted=context.device_trusted,
                sensitive_action=sensitive,
                transaction_value_usd=context.transaction_value_usd,
                destination_risk=context.destination_risk,
                authentication_strength=context.authentication_strength,
            )
        )

        result = evaluate_risk(score, reasons)

        if sensitive and result.decision == SecurityDecision.STEP_UP:
            result = SecurityResult(
                decision=result.decision,
                risk_score=result.risk_score,
                reason_codes=result.reason_codes,
                required_action="step_up_authentication",
                metadata=result.metadata,
            )

        return _audit(context, result)

    except Exception:
        result = block(SecurityFailure.INFRASTRUCTURE.value)
        return _audit(context, result)
