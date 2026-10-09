"""Sending one alert: idempotent, retry-aware, and independent of any request.

``deliver_alert`` is the only place a message leaves the system. It *claims*
the alert first (an atomic UPDATE from pending/failed to sending), so two
workers, a Celery redelivery, or a double click can never send the same alert
twice. A claim that is never finished (the worker died mid-send) goes stale
after ``STALE_CLAIM`` and can be taken again; in that rare case the donor may
get a duplicate, which is the safe side of the trade-off.
"""

import logging
from datetime import timedelta
from urllib.parse import urljoin

from django.conf import settings
from django.db import transaction
from django.db.models import F, Q
from django.template.loader import render_to_string
from django.utils import timezone

from .models import DonorAlert, NotificationMessage
from .notifications import get_backend
from .notifications.base import OutgoingMessage

logger = logging.getLogger(__name__)

STALE_CLAIM = timedelta(minutes=10)

DONE = "done"
RETRY = "retry"
SKIPPED = "skipped"


def render_alert_body(alert, respond_url):
    """Render one alert's message text from the plain-text template."""
    return render_to_string(
        "blood_requests/messages/alert_sms.txt",
        {"alert": alert, "request": alert.blood_request, "respond_url": respond_url},
    ).strip()


def _record(alert, body, backend_name, status, detail="", provider_message_id=""):
    NotificationMessage.objects.create(
        alert=alert,
        recipient_phone=alert.donor.phone,
        channel=alert.channel,
        body=body,
        backend=backend_name,
        status=status,
        detail=detail,
        provider_message_id=provider_message_id,
    )


def _finish(alert_id, status, *, sent):
    fields = {"delivery_status": status}
    if sent:
        fields["sent_at"] = timezone.now()
    # Only if still SENDING, so a delivery receipt that raced ahead is kept.
    DonorAlert.objects.filter(pk=alert_id, delivery_status=DonorAlert.DeliveryStatus.SENDING).update(**fields)


def deliver_alert(alert_id, base_url):
    """Send one alert once. Returns DONE, RETRY (transient failure) or SKIPPED."""
    now = timezone.now()
    status = DonorAlert.DeliveryStatus

    claimed = (
        DonorAlert.objects.filter(pk=alert_id)
        .filter(
            Q(delivery_status__in=[status.PENDING, status.FAILED])
            | Q(delivery_status=status.SENDING, claimed_at__lt=now - STALE_CLAIM)
        )
        .update(
            delivery_status=status.SENDING,
            claimed_at=now,
            send_attempts=F("send_attempts") + 1,
        )
    )

    if not claimed:
        return SKIPPED

    alert = DonorAlert.objects.select_related("donor", "blood_request").get(pk=alert_id)
    donor = alert.donor

    backend = get_backend()
    body = render_alert_body(alert, urljoin(base_url, alert.respond_path))

    # Opt-out and closure are re-checked at send time: a queued or retried
    # alert may be sent minutes or hours after it was created.
    if donor.sms_opt_out or not donor.availability:
        _record(alert, body, backend.name, "failed", "Not sent: the donor opted out or is unavailable.")
        _finish(alert_id, status.FAILED, sent=False)
        return DONE

    if not alert.blood_request.is_open:
        _record(alert, body, backend.name, "failed", "Not sent: the request is no longer open.")
        _finish(alert_id, status.FAILED, sent=False)
        return DONE

    retryable = False
    provider_id = ""

    try:
        result = backend.send(
            OutgoingMessage(recipient_phone=donor.phone, body=body, channel=alert.channel)
        )
        outcome, detail = result.status, result.detail
        retryable = result.retryable
        provider_id = result.provider_message_id
    except Exception as error:
        logger.exception("Notification backend %r raised", backend.name)
        outcome, detail, retryable = "failed", str(error), True

    _record(alert, body, backend.name, outcome, detail, provider_id)

    if outcome == "sent":
        _finish(alert_id, status.SENT, sent=True)
        return DONE

    _finish(alert_id, status.FAILED, sent=False)
    logger.warning("Alert %s failed (attempt %s): %s", alert_id, alert.send_attempts, detail)

    if retryable and alert.send_attempts < settings.NOTIFICATION_MAX_ATTEMPTS:
        return RETRY

    return DONE


def enqueue_alert(alert_id, base_url):
    """Hand an alert to the background worker, or send inline in sync mode."""
    if settings.NOTIFICATION_QUEUE == "celery":
        from .tasks import send_alert_task

        transaction.on_commit(lambda: send_alert_task.delay(alert_id, base_url))
    else:
        deliver_alert(alert_id, base_url)


# --- Delivery receipts ------------------------------------------------------

_DELIVERED_WORDS = ("deliver", "success")
_FAILED_WORDS = ("fail", "reject", "undeliver", "expire", "absent", "blacklist", "invalid")


def map_receipt_status(raw):
    """Provider status text -> "delivered", "failed", or None (still in flight)."""
    text = (raw or "").strip().lower()

    if not text:
        return None
    if any(word in text for word in _FAILED_WORDS):
        return "failed"
    if any(word in text for word in _DELIVERED_WORDS):
        return "delivered"
    return None


def apply_receipt(provider_message_id, raw_status, detail=""):
    """Record a delivery receipt. Returns True if a message was updated."""
    state = map_receipt_status(raw_status)

    if not provider_message_id or state is None:
        return False

    updated = False

    for message in NotificationMessage.objects.filter(provider_message_id=provider_message_id):
        if message.status == NotificationMessage.Status.DELIVERED:
            continue

        message.status = state
        message.detail = (detail or str(raw_status))[:500]
        message.save(update_fields=["status", "detail"])
        updated = True

        if message.alert_id:
            DonorAlert.objects.filter(pk=message.alert_id).exclude(
                delivery_status=DonorAlert.DeliveryStatus.DELIVERED
            ).update(
                delivery_status=(
                    DonorAlert.DeliveryStatus.DELIVERED
                    if state == "delivered"
                    else DonorAlert.DeliveryStatus.FAILED
                )
            )

    return updated
