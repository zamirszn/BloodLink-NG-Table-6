"""Daily jobs for request expiry (F5). Both are safe to run any number of times."""

import logging
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from .models import BloodRequest, NotificationMessage
from .notifications import get_backend
from .notifications.base import OutgoingMessage

logger = logging.getLogger(__name__)

REMINDER_WINDOW = timedelta(hours=24)


def expire_requests(now=None):
    """Mark lapsed open requests expired. Returns how many changed."""
    now = now or timezone.now()

    return BloodRequest.objects.filter(
        status=BloodRequest.Status.OPEN, expires_at__lte=now
    ).update(status=BloodRequest.Status.EXPIRED, updated_at=now)


def send_expiry_reminders(now=None, base_url=None):
    """Text each requester once, up to 24 hours before their request lapses."""
    now = now or timezone.now()
    base = (base_url or settings.SITE_BASE_URL).rstrip("/")
    backend = get_backend()
    sent = 0

    due = BloodRequest.objects.filter(
        status=BloodRequest.Status.OPEN,
        expires_at__gt=now,
        expires_at__lte=now + REMINDER_WINDOW,
        expiry_reminder_sent_at__isnull=True,
        requester_phone_verified_at__isnull=False,
    )

    for blood_request in due:
        # Claim first so a second run, or a concurrent one, cannot double-send.
        claimed = BloodRequest.objects.filter(
            pk=blood_request.pk, expiry_reminder_sent_at__isnull=True
        ).update(expiry_reminder_sent_at=now)

        if not claimed:
            continue

        body = (
            f"BloodLink NG: your {blood_request.recipient_blood_type} blood request closes "
            f"within 24 hours. Extend or close it here: "
            f"{base}/requests/manage/{blood_request.status_token}/"
        )

        try:
            result = backend.send(OutgoingMessage(recipient_phone=blood_request.requester_phone, body=body))
            status, detail = result.status, result.detail
        except Exception as error:
            logger.exception("Expiry reminder failed for request %s", blood_request.pk)
            status, detail = "failed", str(error)

        NotificationMessage.objects.create(
            recipient_phone=blood_request.requester_phone,
            channel="sms",
            body=body,
            backend=backend.name,
            status=status,
            detail=detail,
        )
        sent += 1

    return sent
