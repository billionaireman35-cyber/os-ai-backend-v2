import json
from pywebpush import webpush
from app.core.config import settings
import logging

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
            data=json.dumps(payload),
            vapid_private_key=vapid_private_key,
            vapid_public_key=vapid_public_key,
            ttl=86400,
        )
        logger.info("Push notification sent successfully")
    except Exception as e:
        logger.error(f"Failed to send push notification: {e}")
        raise
