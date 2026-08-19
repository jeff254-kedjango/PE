"""
SMS service — Africa's Talking integration for OTP delivery.

Uses sandbox in development (AT_USERNAME=sandbox), production credentials via env vars.
"""

import logging
from PE.weespas.core.config import settings

logger = logging.getLogger(__name__)


def _ensure_254(phone: str) -> str:
    """Normalize a Kenyan phone number to +254... format."""
    digits = "".join(c for c in phone if c.isdigit())
    if digits.startswith("0") and len(digits) == 10:
        digits = "254" + digits[1:]
    elif not digits.startswith("254"):
        digits = "254" + digits
    return "+" + digits


def send_otp(phone: str, otp_code: str) -> bool:
    """
    Send an OTP code via SMS. Returns True on success, False on failure.
    In debug mode (no API key), logs the OTP to console instead.
    """
    recipient = _ensure_254(phone)
    message = f"Your Weespas verification code is: {otp_code}. Expires in 5 minutes."

    if not settings.at_api_key:
        logger.warning("AT_API_KEY not set — SMS not sent. OTP for %s: %s", recipient, otp_code)
        return True  # Treat as success so the flow continues in dev

    try:
        import africastalking  # lazy import — only needed when key is configured

        africastalking.initialize(settings.at_username, settings.at_api_key)
        sms = africastalking.SMS
        # `sender_id` only set in prod; sandbox ignores it. Passing an empty
        # string causes AT to return InvalidSenderId, so we omit the kwarg
        # entirely when no sender is configured.
        sender = settings.at_sender_id.strip() if settings.at_sender_id else ""
        if sender:
            response = sms.send(message, [recipient], sender_id=sender)
        else:
            response = sms.send(message, [recipient])

        recipients = response.get("SMSMessageData", {}).get("Recipients", [])
        if recipients and recipients[0].get("status") == "Success":
            logger.info("OTP SMS sent to %s", recipient)
            return True

        logger.error("SMS send failed: %s", response)
        return False
    except Exception as exc:
        logger.error("SMS service error: %s", exc)
        return False
