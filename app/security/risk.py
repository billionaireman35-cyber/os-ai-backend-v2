from dataclasses import dataclass


@dataclass(frozen=True)
class RiskSignals:
    device_trusted: bool = True
    sensitive_action: bool = False
    transaction_value_usd: float = 0.0
    velocity_score: int = 0
    destination_risk: int = 0
    authentication_strength: int = 100


def calculate_risk(signals: RiskSignals) -> tuple[int, tuple[str, ...]]:
    """Return a normalized 0-100 security risk score and reason codes."""
    score = 0
    reasons: list[str] = []

    if signals.device_trusted is not True:
        score += 25
        reasons.append(
            "untrusted_device"
            if signals.device_trusted is False
            else "device_trust_unknown"
        )

    if signals.sensitive_action:
        score += 10
        reasons.append("sensitive_action")

    value = max(0.0, float(signals.transaction_value_usd))
    if value > 50_000_000:
        score += 80
        reasons.append("transaction_value_extreme")
    elif value > 10_000_000:
        score += 70
        reasons.append("transaction_value_very_high")
    elif value > 1_000_000:
        score += 60
        reasons.append("transaction_value_critical")
    elif value > 100_000:
        score += 50
        reasons.append("transaction_value_high")
    elif value > 50_000:
        score += 40
        reasons.append("transaction_value_elevated")
    elif value > 10_000:
        score += 30
        reasons.append("transaction_value_moderate")
    elif value > 1_000:
        score += 20
        reasons.append("transaction_value_low")
    elif value > 100:
        score += 10
        reasons.append("transaction_value_small")
    elif value > 0:
        reasons.append("transaction_value_present")

    velocity = max(0, min(int(signals.velocity_score), 40))
    if velocity:
        score += velocity
        reasons.append("transaction_velocity")

    destination = max(0, min(int(signals.destination_risk), 40))
    if destination:
        score += destination
        reasons.append("destination_risk")

    auth_strength = signals.authentication_strength
    if auth_strength is None:
        score += 15
        reasons.append("authentication_strength_unknown")
    else:
        auth_strength = max(0, min(int(auth_strength), 100))
        if auth_strength < 70:
            score += 15
            reasons.append("weak_authentication")

    return min(score, 100), tuple(reasons)
