from dataclasses import dataclass
from datetime import datetime, timezone, timedelta

from app.core.database import get_db


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    count: int
    limit: int
    retry_after_seconds: int


def check_rate_limit(
    scope: str,
    subject: str,
    action: str,
    limit: int,
    window_seconds: int,
) -> RateLimitResult:
    """Atomically count a request inside a fixed UTC window."""
    limit = max(1, int(limit))
    window_seconds = max(1, int(window_seconds))

    now = datetime.now(timezone.utc)
    epoch = int(now.timestamp())
    window_epoch = epoch - (epoch % window_seconds)
    window_start = datetime.fromtimestamp(window_epoch, tz=timezone.utc)
    window_end = window_start + timedelta(seconds=window_seconds)

    with get_db() as conn:
        with conn.cursor() as c:
            c.execute("""
                INSERT INTO security_rate_limits (
                    scope,
                    subject,
                    action,
                    window_start,
                    request_count
                )
                VALUES (%s, %s, %s, %s, 1)
                ON CONFLICT (scope, subject, action, window_start)
                DO UPDATE SET
                    request_count = security_rate_limits.request_count + 1,
                    updated_at = NOW()
                RETURNING request_count
            """, (
                scope,
                subject,
                action,
                window_start.replace(tzinfo=None),
            ))

            row = c.fetchone()
            count = int(row[0])

            allowed = count <= limit

            if not allowed:
                c.execute("""
                    UPDATE security_rate_limits
                    SET blocked_count = blocked_count + 1,
                        updated_at = NOW()
                    WHERE scope = %s
                      AND subject = %s
                      AND action = %s
                      AND window_start = %s
                """, (
                    scope,
                    subject,
                    action,
                    window_start.replace(tzinfo=None),
                ))

            conn.commit()

    retry_after = max(0, int((window_end - now).total_seconds()))

    return RateLimitResult(
        allowed=allowed,
        count=count,
        limit=limit,
        retry_after_seconds=retry_after,
    )
