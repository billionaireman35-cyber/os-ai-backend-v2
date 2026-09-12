import json
import logging

from pywebpush import webpush

from app.core.config import settings
from app.core.database import get_db

logger = logging.getLogger(__name__)


def send_push_notification(subscription_info, payload):
    try:
        vapid_private_key = settings.VAPID_PRIVATE_KEY
        vapid_public_key = settings.VAPID_PUBLIC_KEY

        if not vapid_private_key or not vapid_public_key:
            logger.warning("VAPID keys not set. Push notifications will not work.")
            return

        webpush(
            subscription_info=subscription_info,
            data=json.dumps(payload, default=str),
            vapid_private_key=vapid_private_key,
            vapid_public_key=vapid_public_key,
            ttl=86400,
        )
        logger.info("Push notification sent successfully")

    except Exception as exc:
        logger.error("Failed to send push notification: %s", exc)
        raise


def send_push_to_user(user_id: str, payload: dict) -> None:
    """Best-effort push delivery to every registered device for a user."""
    if not user_id:
        return

    with get_db() as conn:
        with conn.cursor() as c:
            c.execute(
                """
                SELECT id, endpoint, auth_key, p256dh_key
                FROM push_subscriptions
                WHERE user_id = %s
                """,
                (user_id,),
            )
            subscriptions = c.fetchall()

    logger.info(
        "Push dispatch: user=%s subscriptions=%s",
        user_id,
        len(subscriptions),
    )

    for subscription_id, endpoint, auth_key, p256dh_key in subscriptions:
        subscription_info = {
            "endpoint": endpoint,
            "keys": {
                "auth": auth_key,
                "p256dh": p256dh_key,
            },
        }

        try:
            send_push_notification(subscription_info, payload)

        except Exception as exc:
            response = getattr(exc, "response", None)
            status_code = getattr(response, "status_code", None)

            if status_code in (404, 410):
                try:
                    with get_db() as conn:
                        with conn.cursor() as c:
                            c.execute(
                                """
                                DELETE FROM push_subscriptions
                                WHERE id = %s
                                """,
                                (subscription_id,),
                            )
                            conn.commit()

                    logger.info(
                        "Removed expired push subscription %s",
                        subscription_id,
                    )
                except Exception as cleanup_exc:
                    logger.error(
                        "Failed to remove expired push subscription %s: %s",
                        subscription_id,
                        cleanup_exc,
                    )
            else:
                logger.error(
                    "Push delivery failed for subscription %s: %s",
                    subscription_id,
                    exc,
                )
