"""Choosing which donors a request alerts, and dispatching to them.

Compatibility comes from :mod:`donors.compatibility` and the waiting period
from :mod:`donors.eligibility`, so neither rule is restated here.

A note on "nearby": ``Donor.location`` is free text and this project holds no
coordinates and no PostGIS, so no distance is computed or displayed anywhere
downstream. Location is a case-insensitive substring match and the interface
says so in as many words.
"""

import logging
from urllib.parse import urljoin

from django.db import IntegrityError, transaction
from django.template.loader import render_to_string
from django.utils import timezone

from donors.compatibility import compatible_donor_types
from donors.eligibility import eligible_q
from donors.models import Donor

from .models import DonorAlert, NotificationMessage
from .notifications import get_backend
from .notifications.base import OutgoingMessage

logger = logging.getLogger(__name__)

#: Ceiling on a single dispatch. A request with no location matches every
#: compatible eligible donor in the table, so a deliberate bound stops one
#: click alerting an unbounded crowd. The shortfall is reported in the UI
#: rather than truncated silently.
MAX_ALERTS_PER_REQUEST = 100


def eligible_donors(recipient_blood_type, location="", *, as_of=None):
    """Donors who should be alerted for a request.

    Compatible with the recipient, past the 90-day wait, currently available,
    and — when the request names a location — whose recorded location contains
    that text (case-insensitively).
    """
    donors = Donor.objects.filter(
        availability=True,
        blood_type__in=compatible_donor_types(recipient_blood_type),
    ).filter(eligible_q(as_of))

    location = (location or "").strip()

    if location:
        donors = donors.filter(location__icontains=location)

    return donors.order_by("name")


def donors_to_alert(blood_request):
    """Matching donors who have not already been alerted for this request.

    This is what makes re-dispatching a no-op rather than a second round of
    messages.
    """
    already_alerted = blood_request.alerts.values_list("donor_id", flat=True)

    return eligible_donors(
        blood_request.recipient_blood_type, blood_request.location
    ).exclude(pk__in=already_alerted)


def render_alert_body(alert, respond_url):
    """Render one alert's message text.

    Copy lives in a plain-text template so wording changes never touch Python
    and a different channel can get its own file.
    """
    return render_to_string(
        "blood_requests/messages/alert_sms.txt",
        {
            "alert": alert,
            "request": alert.blood_request,
            "respond_url": respond_url,
        },
    ).strip()


def send_alert(alert, *, base_url):
    """Deliver one alert and record it in the outbox.

    The outbox row is written here rather than inside the backend, so every
    backend gets an audit trail while staying free of model imports.
    """
    body = render_alert_body(alert, urljoin(base_url, alert.respond_path))

    backend = get_backend()

    try:
        result = backend.send(
            OutgoingMessage(
                recipient_phone=alert.donor.phone,
                body=body,
                channel=alert.channel,
            )
        )
        status, detail = result.status, result.detail
    except Exception as error:
        # One unreachable handset or a misbehaving provider must not abort
        # the rest of the dispatch.
        logger.exception("Notification backend %r raised", backend.name)
        status, detail = NotificationMessage.Status.FAILED, str(error)

    NotificationMessage.objects.create(
        alert=alert,
        recipient_phone=alert.donor.phone,
        channel=alert.channel,
        body=body,
        backend=backend.name,
        status=status,
        detail=detail,
    )

    sent = status == NotificationMessage.Status.SENT

    alert.delivery_status = (
        DonorAlert.DeliveryStatus.SENT if sent else DonorAlert.DeliveryStatus.FAILED
    )
    alert.sent_at = timezone.now()
    alert.save(update_fields=["delivery_status", "sent_at"])


def notify_matching_donors(blood_request, *, base_url):
    """Alert every eligible donor not yet alerted for this request.

    Idempotent. The unique constraint on (request, donor) is the real
    guarantee; the pre-filter keeps the ordinary case from doing wasted work,
    and the IntegrityError guard covers a concurrent dispatch. Donors who have
    already answered are never re-messaged.

    Returns ``(created, skipped)`` — how many alerts went out, and how many
    matched donors were left untouched because the ceiling was reached.
    """
    pending = donors_to_alert(blood_request)

    # Counted before the slice so the shortfall reported to the requester is
    # the real number left over, not just the one row sliced off the end.
    total = pending.count()
    to_alert = list(pending[:MAX_ALERTS_PER_REQUEST])
    skipped = max(0, total - len(to_alert))

    if skipped:
        logger.warning(
            "Request #%s matches %s donors; alerting the first %s and leaving "
            "%s for a later dispatch.",
            blood_request.pk,
            total,
            MAX_ALERTS_PER_REQUEST,
            skipped,
        )

    created_alerts = []

    for donor in to_alert:
        try:
            # Each insert gets its own atomic block. A bare try/except around
            # create() would leave the surrounding transaction unusable, and
            # every later query in it would raise TransactionManagementError —
            # this savepoint is what makes the duplicate-key case survivable.
            with transaction.atomic():
                alert = DonorAlert.objects.create(
                    blood_request=blood_request,
                    donor=donor,
                )
        except IntegrityError:
            # Another dispatch created this alert first; that donor is covered.
            continue

        created_alerts.append(alert)

    # Delivery happens after every row is committed. A real provider makes a
    # network call, and holding a database transaction open across one is how
    # a slow API turns into lock contention.
    for alert in created_alerts:
        send_alert(alert, base_url=base_url)

    return len(created_alerts), skipped


def request_counts(blood_request):
    """Headline numbers for a request's summary.

    ``matched`` is how many donors the rules select in total; ``alerted`` and
    ``responded`` describe what has actually happened.
    """
    alerts = blood_request.alerts.all()

    return {
        "matched": eligible_donors(
            blood_request.recipient_blood_type, blood_request.location
        ).count(),
        "alerted": alerts.count(),
        "responded": alerts.exclude(response=DonorAlert.Response.PENDING).count(),
        "available": alerts.filter(response=DonorAlert.Response.AVAILABLE).count(),
    }
