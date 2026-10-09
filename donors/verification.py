"""Who counts as a verified donor. One definition, used by search and alerts."""

from datetime import date

from django.conf import settings
from django.db.models import Q
from django.utils import timezone


def grace_active(today=None):
    raw = getattr(settings, "PHONE_VERIFICATION_GRACE_ENDS", "")
    if not raw:
        return True
    return (today or timezone.localdate()) <= date.fromisoformat(raw)


def verified_q(today=None):
    """Q matching donors whose number is verified (or still in the grace period)."""
    q = Q(phone_verified_at__isnull=False)
    if grace_active(today):
        q |= Q(verification_grace=True)
    return q
