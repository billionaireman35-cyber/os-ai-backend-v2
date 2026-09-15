import json
from typing import Any

from app.core.database import get_db


AUDIT_METADATA_KEYS = frozenset({
    "source",
    "endpoint",
    "chain",
    "network",
    "risk_model_version",
    "policy_version",
    "wallet_type",
    "transaction_type",
    "method",
    "elevation_seconds",
})


def _sanitize_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
    if not metadata:
        return {}
    return {
        key: value
        for key, value in metadata.items()
        if key in AUDIT_METADATA_KEYS
    }


def record_security_event(
    *,
    action: str,
    decision: str,
    risk_score: int = 0,
    reason_codes: tuple[str, ...] = (),
    user_id: str | None = None,
    wallet_id: str | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    ip_address: str | None = None,
    device_fingerprint: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> str:
    """Persist a security decision and return its event ID."""
    safe_metadata = _sanitize_metadata(metadata)
    safe_reasons = list(reason_codes)
    score = max(0, min(int(risk_score), 100))

    with get_db() as conn:
        with conn.cursor() as c:
            c.execute("""
                INSERT INTO security_events (
                    user_id,
                    wallet_id,
                    action,
                    resource_type,
                    resource_id,
                    decision,
                    risk_score,
                    reason_codes,
                    ip_address,
                    device_fingerprint,
                    metadata
                )
                VALUES (
                    %s, %s, %s, %s, %s, %s, %s,
                    %s::jsonb, %s, %s, %s::jsonb
                )
                RETURNING id
            """, (
                user_id,
                wallet_id,
                action,
                resource_type,
                resource_id,
                decision,
                score,
                json.dumps(safe_reasons),
                ip_address,
                device_fingerprint,
                json.dumps(safe_metadata),
            ))

            event_id = str(c.fetchone()[0])
            conn.commit()

    return event_id
