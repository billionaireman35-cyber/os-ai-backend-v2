from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class SecurityAction(str, Enum):
    AUTHENTICATE = "authenticate"
    ACCOUNT_CHANGE = "account_change"
    WALLET_READ = "wallet_read"
    WALLET_SIGN = "wallet_sign"
    TRANSACTION = "transaction"
    SAFE_OPERATION = "safe_operation"
    CLOSE_TRANSFER = "close_transfer"
    WITHDRAWAL = "withdrawal"
    AI_REQUEST = "ai_request"


class SecurityDecision(str, Enum):
    ALLOW = "allow"
    STEP_UP = "step_up"
    BLOCK = "block"


class SecurityFailure(str, Enum):
    INFRASTRUCTURE = "security_infrastructure_failure"
    INVALID_CONTEXT = "invalid_security_context"


SENSITIVE_ACTIONS = frozenset({
    SecurityAction.WALLET_SIGN,
    SecurityAction.TRANSACTION,
    SecurityAction.SAFE_OPERATION,
    SecurityAction.CLOSE_TRANSFER,
    SecurityAction.WITHDRAWAL,
})


@dataclass(frozen=True)
class SecurityContext:
    action: SecurityAction | str
    user_id: str | None = None
    wallet_id: str | None = None
    resource_type: str | None = None
    resource_id: str | None = None
    ip_address: str | None = None
    device_fingerprint: str | None = None
    device_trusted: bool = True
    authentication_strength: int = 100
    transaction_value_usd: float = 0.0
    destination_risk: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SecurityResult:
    decision: SecurityDecision
    risk_score: int = 0
    reason_codes: tuple[str, ...] = ()
    required_action: str | None = None
    audit_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def allowed(self) -> bool:
        return self.decision == SecurityDecision.ALLOW

    @property
    def blocked(self) -> bool:
        return self.decision == SecurityDecision.BLOCK
