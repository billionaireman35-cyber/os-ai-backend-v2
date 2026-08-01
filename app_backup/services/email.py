import resend
import logging
from app.core.config import settings

logger = logging.getLogger(__name__)
resend.api_key = settings.RESEND_API_KEY

async def send_verification_email(email: str, code: str, purpose: str = "verification") -> bool:
    config = {
        "verification": {"subject": "Verify your OS AI account", "title": "Verify Your Email"},
        "password_reset": {"subject": "Reset your OS AI password", "title": "Password Reset Code"}
    }.get(purpose, {})
    
    html = f"""
    <div style="background:#0B0D12;padding:40px;font-family:'Segoe UI',sans-serif;">
        <div style="max-width:480px;margin:auto;background:#1A1D24;border-radius:24px;padding:32px;border:1px solid rgba(255,255,255,0.06);">
            <h1 style="color:#a855f7;font-size:24px;margin-top:0;">{config.get('title', 'Verify')}</h1>
            <p style="color:#E2E8F0;font-size:16px;">Your verification code is:</p>
            <div style="background:#0B0D12;border:2px dashed #6366f1;border-radius:16px;padding:16px;text-align:center;font-size:36px;font-weight:bold;letter-spacing:8px;color:#a855f7;font-family:'Courier New',monospace;">
                {code}
            </div>
            <p style="color:#64748B;font-size:14px;margin-top:24px;">⏰ Expires in 15 minutes</p>
            <p style="color:#64748B;font-size:14px;">🔒 If you didn't request this, ignore this email.</p>
        </div>
    </div>
    """
    
    try:
        resend.Emails.send({
            "from": "OS AI <noreply@osai.io>",
            "to": [email],
            "subject": config.get("subject", "Verify your email"),
            "html": html
        })
        logger.info(f"✅ Email sent to {email} for {purpose}")
        return True
    except Exception as e:
        logger.error(f"❌ Email failed to {email}: {e}")
        return False
