from app.security.types import SecurityDecision, SecurityResult


ALLOW_MAX_RISK = 29
STEP_UP_MAX_RISK = 69
MAX_RISK_SCORE = 100

HARD_BLOCK_REASONS = frozenset({
    "unauthorized",
    "invalid_identity",
    "revoked_session",
    "rate_limit_exceeded",
    "security_infrastructure_failure",
    "compromised_device",
})


def evaluate_risk(
    risk_score: int,
    reason_codes: tuple[str, ...] = (),
    required_action: str | None = None,
) -> SecurityResult:
    """Convert a normalized risk score into a security decision."""
    score = max(0, min(int(risk_score), MAX_RISK_SCORE))

    if any(reason in HARD_BLOCK_REASONS for reason in reason_codes):
        decision = SecurityDecision.BLOCK
        score = MAX_RISK_SCORE
    elif score >= STEP_UP_MAX_RISK + 1:
        decision = SecurityDecision.BLOCK
    elif score > ALLOW_MAX_RISK:
        decision = SecurityDecision.STEP_UP
    else:
        decision = SecurityDecision.ALLOW

    return SecurityResult(
        decision=decision,
        risk_score=score,
        reason_codes=reason_codes,
        required_action=required_action,
    )


def block(
    reason_code: str,
    required_action: str | None = None,
) -> SecurityResult:
    """Create an unconditional security block."""
    return SecurityResult(
        decision=SecurityDecision.BLOCK,
        risk_score=MAX_RISK_SCORE,
        reason_codes=(reason_code,),
        required_action=required_action,
    )
