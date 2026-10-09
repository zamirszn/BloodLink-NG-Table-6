"""One-time codes for phone verification and password reset.

Codes are six digits, valid for ten minutes, allowed five wrong guesses, and
stored only as an HMAC. A new code invalidates every earlier one for the same
phone and purpose. The code is texted through the configured notification
backend; the outbox row for it is written with the code redacted.
"""

import hashlib
import hmac
import logging
import secrets
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from blood_requests.notifications import get_backend
from blood_requests.notifications.base import OutgoingMessage

from .models import PhoneOTP
from .ratelimit import hit

logger = logging.getLogger(__name__)

SENT = "sent"
COOLDOWN = "cooldown"
THROTTLED = "throttled"
FAILED = "failed"

OK = "ok"
WRONG = "wrong"
EXPIRED = "expired"
LOCKED = "locked"
NONE = "none"


def _generate_code():
    return "".join(secrets.choice("0123456789") for _ in range(settings.OTP_LENGTH))


def hash_code(phone, purpose, code):
    key = settings.SECRET_KEY.encode()
    message = f"{phone}|{purpose}|{code}".encode()
    return hmac.new(key, message, hashlib.sha256).hexdigest()


def _message_for(purpose, code):
    minutes = settings.OTP_TTL_SECONDS // 60
    what = "password reset" if purpose == PhoneOTP.Purpose.RESET else "verification"
    return (
        f"BloodLink NG {what} code: {code}. It expires in {minutes} minutes. "
        "Never share this code with anyone."
    )


def issue_code(phone, purpose, *, ip="unknown", now=None):
    """Create and text a fresh code. Returns SENT, COOLDOWN, THROTTLED or FAILED."""
    now = now or timezone.now()

    if not hit(f"otp:phone:{phone}", settings.OTP_MAX_SENDS_PER_PHONE_PER_HOUR, 3600):
        return THROTTLED
    if not hit(f"otp:ip:{ip}", settings.OTP_MAX_SENDS_PER_IP_PER_HOUR, 3600):
        return THROTTLED

    latest = PhoneOTP.objects.filter(phone=phone, purpose=purpose).order_by("-created_at").first()
    if latest and now - latest.created_at < timedelta(seconds=settings.OTP_RESEND_COOLDOWN_SECONDS):
        return COOLDOWN

    code = _generate_code()

    with transaction.atomic():
        # A resent code invalidates the previous one.
        PhoneOTP.objects.filter(phone=phone, purpose=purpose).delete()
        PhoneOTP.objects.create(
            phone=phone,
            purpose=purpose,
            code_hash=hash_code(phone, purpose, code),
            expires_at=now + timedelta(seconds=settings.OTP_TTL_SECONDS),
        )

    backend = get_backend()
    try:
        result = backend.send(OutgoingMessage(recipient_phone=phone, body=_message_for(purpose, code)))
        ok = result.status == "sent"
    except Exception:
        logger.exception("OTP send failed")
        ok = False

    return SENT if ok else FAILED


def verify_code(phone, purpose, code, *, now=None):
    """Check a submitted code. A correct code is consumed (single use)."""
    now = now or timezone.now()
    code = (code or "").strip()

    with transaction.atomic():
        otp = (
            PhoneOTP.objects.select_for_update()
            .filter(phone=phone, purpose=purpose)
            .order_by("-created_at")
            .first()
        )

        if otp is None:
            return NONE
        if otp.attempts >= settings.OTP_MAX_ATTEMPTS:
            return LOCKED
        if now >= otp.expires_at:
            return EXPIRED

        if hmac.compare_digest(otp.code_hash, hash_code(phone, purpose, code)):
            otp.delete()
            return OK

        otp.attempts += 1
        otp.save(update_fields=["attempts"])
        return LOCKED if otp.attempts >= settings.OTP_MAX_ATTEMPTS else WRONG


def dummy_work():
    """Burn about the same CPU as a real issue, for unknown numbers."""
    hash_code("0", "dummy", _generate_code())
