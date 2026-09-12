import json
import logging
import uuid

from app.core.database import get_db

logger = logging.getLogger(__name__)


def _dispatch_push(user_id: str, payload: dict) -> None:
    try:
        from app.services.push_service import send_push_to_user
    except Exception as exc:
        logger.warning("Push service unavailable: %s", exc)
        return

    try:
        send_push_to_user(user_id=user_id, payload=payload)
    except Exception as exc:
        logger.error("Push dispatch failed: %s", exc)


def create_notification(
    user_id: str,
    notification_type: str,
    title: str,
    body: str,
    url: str | None = None,
    data: dict | None = None,
    event_key: str | None = None,
) -> dict:
    """Create a persistent notification and best-effort push it to the user."""
    if not user_id:
        raise ValueError("user_id is required")

    if not notification_type:
        raise ValueError("notification_type is required")

    if not title:
        raise ValueError("title is required")

    if not body:
        raise ValueError("body is required")

    notification_id = str(uuid.uuid4())
    payload = json.loads(json.dumps(data or {}, default=str))

    with get_db() as conn:
        with conn.cursor() as c:
            if event_key:
                c.execute(
                    """
                    SELECT id, user_id, type, title, body, url, data,
                           read, event_key, created_at
                    FROM notifications
                    WHERE user_id = %s AND event_key = %s
                    LIMIT 1
                    """,
                    (user_id, event_key),
                )
                existing = c.fetchone()

                if existing:
                    return {
                        "id": str(existing[0]),
                        "user_id": str(existing[1]),
                        "type": existing[2],
                        "title": existing[3],
                        "body": existing[4],
                        "url": existing[5],
                        "data": existing[6] or {},
                        "read": existing[7],
                        "event_key": existing[8],
                        "created_at": existing[9],
                    }

            c.execute(
                """
                INSERT INTO notifications (
                    id,
                    user_id,
                    type,
                    title,
                    body,
                    url,
                    data,
                    read,
                    event_key
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, FALSE, %s)
                RETURNING id, user_id, type, title, body, url,
                          data, read, event_key, created_at
                """,
                (
                    notification_id,
                    user_id,
                    notification_type,
                    title,
                    body,
                    url,
                    payload,
                    event_key,
                ),
            )

            row = c.fetchone()
            conn.commit()

    notification = {
        "id": str(row[0]),
        "user_id": str(row[1]),
        "type": row[2],
        "title": row[3],
        "body": row[4],
        "url": row[5],
        "data": row[6] or {},
        "read": row[7],
        "event_key": row[8],
        "created_at": row[9],
    }

    _dispatch_push(
        user_id=str(row[1]),
        payload={
            "id": notification["id"],
            "type": notification["type"],
            "title": notification["title"],
            "body": notification["body"],
            "url": notification["url"],
            "data": notification["data"],
        },
    )

    return notification
