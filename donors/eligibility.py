"""The 90-day blood-donation eligibility rule.

This module is the single source of truth for how long a donor must wait
between donations. It was previously written out inline in several places;
both the donor views and the blood-request matching import from here so the
rule can never silently drift between features.

The rule: a donor is eligible if they have never donated, or if their last
donation was at least ``ELIGIBILITY_DAYS`` ago.
"""

from datetime import date, timedelta

from django.db.models import Q

# Whole days a donor must wait between donations.
ELIGIBILITY_DAYS = 90


def eligibility_cutoff(as_of=None):
    """The earliest last-donation date that still counts as eligible.

    A donor is eligible when their last donation is on or before this date.
    ``as_of`` defaults to today and exists so tests can pin the clock.
    """
    return (as_of or date.today()) - timedelta(days=ELIGIBILITY_DAYS)


def next_eligible_date(last_donation):
    """The first day the wait is over for a donor who last donated then."""
    if last_donation is None:
        return None

    return last_donation + timedelta(days=ELIGIBILITY_DAYS)


def eligible_q(as_of=None):
    """Q object matching donors who have passed the 90-day wait.

    Built here rather than in each queryset so callers cannot accidentally
    drop half the rule (for example keeping only the ``last_donation`` side
    and silently excluding first-time donors).
    """
    return Q(last_donation__isnull=True) | Q(last_donation__lte=eligibility_cutoff(as_of))
