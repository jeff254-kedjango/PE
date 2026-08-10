"""Auth-related Celery tasks.

Both tasks live on the `auth` queue (see core/celery_app.py task_routes) so an
OTP send is never blocked behind an analytics aggregation.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from PE.weespas.core.celery_app import celery_app
from PE.weespas.core.database import SessionLocal
from PE.weespas.models.user import User
from PE.weespas.services import sms_service

logger = logging.getLogger(__name__)


@celery_app.task(
    name="auth.send_otp",
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=30,
    retry_jitter=True,
    max_retries=3,
    acks_late=True,
    # OTP send is short and idempotent (Africa's Talking dedupes by recipient);
    # losing a worker mid-flight + reprocessing is preferable to dropping the
    # SMS entirely. The 30s SETNX dedupe at the call site bounds duplicates.
)
def send_otp(self, phone: str, otp_code: str) -> bool:
    """Async fire-and-forget OTP SMS dispatch.

    The OTP row is already committed before this task runs (see
    auth_service._generate_and_send_otp), so a worker crash never strands a
    user on a code they can't verify — the next /auth/resend-otp will work.
    """
    try:
        return sms_service.send_otp(phone, otp_code)
    except Exception as exc:
        logger.warning("auth.send_otp failed (attempt %s): %s", self.request.retries, exc)
        raise


@celery_app.task(
    name="auth.touch_last_seen",
    ignore_result=True,
    acks_late=False,
    # last_seen_at is a presence indicator; lossy on worker death is fine.
)
def touch_last_seen(user_id: str) -> None:
    """Update `users.last_seen_at` off the request thread.

    The call site (auth_service.get_current_user) already debounces with a
    Redis SETNX at 60s granularity, so this task fires at most once per user
    per minute — even under thundering-herd polling.
    """
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == user_id).first()
        if user is None:
            return
        user.last_seen_at = datetime.now(timezone.utc)
        db.commit()
    except Exception as exc:
        logger.debug("touch_last_seen(%s) failed: %s", user_id, exc)
        try:
            db.rollback()
        except Exception:
            pass
    finally:
        db.close()
