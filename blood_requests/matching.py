"""Choosing which donors a request alerts, and dispatching to them.

Compatibility comes from :mod:`donors.compatibility` and the waiting period
from :mod:`donors.eligibility`, so neither rule is restated here.

A note on "nearby": ``Donor.location`` is free text and this project holds no
coordinates and no PostGIS, so no distance is computed or displayed anywhere
downstream. Location is a case-insensitive substring match and the interface
says so in as many words.
"""

import logging

from django.conf import settings
from django.db import IntegrityError, transaction

from donors.compatibility import ALL_BLOOD_TYPES, compatible_donor_types
from donors.eligibility import eligible_q
from donors.models import Donor
from donors.verification import verified_q

from .delivery import deliver_alert, enqueue_alert, render_alert_body  # noqa: F401
from .models import BloodRequest, DonorAlert

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
        sms_opt_out=False,
        blood_type__in=compatible_donor_types(recipient_blood_type),
    ).filter(eligible_q(as_of)).filter(verified_q())

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


def send_alert(alert, *, base_url):
    """Deliver one alert now (idempotent) and refresh the in-memory row.

    Kept as the synchronous entry point; the dispatch itself goes through
    :func:`blood_requests.delivery.enqueue_alert` so it can run in a worker.
    """
    deliver_alert(alert.pk, base_url)
    alert.refresh_from_db()


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
        enqueue_alert(alert.pk, base_url)

    logger.info(
        "dispatch request=%s created=%s skipped=%s queue=%s",
        blood_request.pk,
        len(created_alerts),
        skipped,
        settings.NOTIFICATION_QUEUE,
    )

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
        # Texted by a dispatch, and volunteers, are counted separately.
        "alerted": alerts.filter(source=DonorAlert.Source.DISPATCH).count(),
        "volunteered": alerts.filter(source=DonorAlert.Source.VOLUNTEER).count(),
        "responded": alerts.exclude(response=DonorAlert.Response.PENDING).count(),
        "available": alerts.filter(response=DonorAlert.Response.AVAILABLE).count(),
    }


def open_requests_for_donor(donor):
    """Open requests this donor matches but has not been alerted about yet.

    Alerts are only created when a requester presses "alert matching donors",
    so on its own the alerts list can stay empty even while requests the donor
    could help with are open. This is the other half of that page: it applies
    exactly the rules :func:`eligible_donors` uses (compatibility, 90-day
    wait, availability, location), so a request shown here is one the donor
    would be alerted for, and it never shows the donor's own requests or ones
    they already have an alert for.

    Most urgent first, then newest.
    """
    if donor is None:
        return []

    # The recipient types this donor's blood can go to: the compatibility
    # table read the other way round, built from the same function.
    recipient_types = [
        recipient_type
        for recipient_type in ALL_BLOOD_TYPES
        if donor.blood_type in compatible_donor_types(recipient_type)
    ]

    urgency_rank = {
        BloodRequest.Urgency.CRITICAL: 0,
        BloodRequest.Urgency.URGENT: 1,
    }

    candidates = (
        BloodRequest.objects.open_now()
        .filter(recipient_blood_type__in=recipient_types)
        .exclude(alerts__donor=donor)
        .exclude(requester_phone=donor.phone)
    )

    matches = [
        blood_request
        for blood_request in candidates
        if eligible_donors(
            blood_request.recipient_blood_type, blood_request.location
        )
        .filter(pk=donor.pk)
        .exists()
    ]

    matches.sort(
        key=lambda r: (urgency_rank.get(r.urgency, 2), -r.created_at.timestamp())
    )

    return matches


def volunteer_for_request(donor, blood_request, *, now=None):
    """Let a matching donor offer to donate without waiting to be alerted.

    Returns ``(alert, status)`` where status is ``"created"``, ``"already"`` or
    ``"refused"``. The donor must pass the very same match as an alert would
    (compatibility, wait, verified phone, availability, location) and the
    request must be open; otherwise the answer is ``"refused"`` and nothing is
    written. The unique (request, donor) constraint makes a repeat a no-op, and
    a later dispatch skips volunteers through ``donors_to_alert``.
    """
    from django.utils import timezone

    if donor is None or not blood_request.is_open:
        return None, "refused"

    if blood_request.requester_phone == donor.phone:
        return None, "refused"

    matches = eligible_donors(
        blood_request.recipient_blood_type, blood_request.location
    ).filter(pk=donor.pk).exists()

    if not matches:
        return None, "refused"

    now = now or timezone.now()

    try:
        with transaction.atomic():
            alert, created = DonorAlert.objects.get_or_create(
                blood_request=blood_request,
                donor=donor,
                defaults={
                    "source": DonorAlert.Source.VOLUNTEER,
                    "response": DonorAlert.Response.AVAILABLE,
                    "responded_at": now,
                    "delivery_status": DonorAlert.DeliveryStatus.NOT_SENT,
                },
            )
    except IntegrityError:
        alert, created = DonorAlert.objects.get(blood_request=blood_request, donor=donor), False

    if created:
        return alert, "created"

    if alert.response != DonorAlert.Response.AVAILABLE:
        # Already alerted and had not said yes (or said no): this is a yes.
        alert.response = DonorAlert.Response.AVAILABLE
        alert.responded_at = now
        alert.save(update_fields=["response", "responded_at"])

        return alert, "created"

    return alert, "already"
