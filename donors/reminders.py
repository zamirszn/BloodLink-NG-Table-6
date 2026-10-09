"""'You can donate again' texts (F4)."""

import logging
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from .eligibility import eligibility_cutoff
from .models import Donor
from .verification import verified_q

logger = logging.getLogger(__name__)


def send_eligibility_reminders(today=None, base_url=None):
    """Text donors whose waiting period has just ended, once per donation.

    ``eligible_reminder_sent_for`` stores the last-donation date a reminder was
    sent for, so re-running the job never repeats it, and a new donation starts
    a new cycle. Only donors whose wait ended within REMINDER_CATCHUP_DAYS are
    considered, so a missed run is caught up without messaging the whole table.
    """
    from blood_requests.models import NotificationMessage
    from blood_requests.notifications import get_backend
    from blood_requests.notifications.base import OutgoingMessage

    today = today or timezone.localdate()
    cutoff = eligibility_cutoff(today)
    oldest = cutoff - timedelta(days=settings.REMINDER_CATCHUP_DAYS)
    base = (base_url or settings.SITE_BASE_URL).rstrip("/")
    backend = get_backend()
    sent = 0

    donors = (
        Donor.objects.filter(
            availability=True,
            sms_opt_out=False,
            last_donation__lte=cutoff,
            last_donation__gte=oldest,
        )
        .filter(verified_q(today))
        .exclude(eligible_reminder_sent_for=models_f("last_donation"))
    )

    for donor in donors:
        claimed = Donor.objects.filter(pk=donor.pk, last_donation=donor.last_donation).exclude(
            eligible_reminder_sent_for=donor.last_donation
        ).update(eligible_reminder_sent_for=donor.last_donation)

        if not claimed:
            continue

        body = (
            f"BloodLink NG: {donor.name}, your 90-day wait is over and you may donate "
            f"blood again. Keep your profile current: {base}/dashboard/"
        )

        try:
            result = backend.send(OutgoingMessage(recipient_phone=donor.phone, body=body))
            status, detail = result.status, result.detail
        except Exception as error:
            logger.exception("Eligibility reminder failed for donor %s", donor.pk)
            status, detail = "failed", str(error)

        NotificationMessage.objects.create(
            recipient_phone=donor.phone, channel="sms", body=body,
            backend=backend.name, status=status, detail=detail,
        )
        sent += 1

    return sent


def models_f(name):
    from django.db.models import F

    return F(name)
