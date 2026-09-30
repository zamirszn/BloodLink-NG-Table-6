"""Publishing blood requests, dispatching alerts, and collecting replies.

Identity follows the same no-authentication pattern as Part 1: a donor is
whoever ``session["donor_id"]`` points at, resolved through the single
``current_donor`` lookup in :mod:`donors.views`.

A requester has no session at all, so their control panel is reached with the
request's own ``status_token`` rather than its primary key. A primary key is
guessable and would let a stranger dispatch alerts to real phone numbers or
close someone else's request.
"""

from django.contrib import messages
from django.db.models import Case, IntegerField, Value, When
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from donors.views import current_donor

from .forms import BloodRequestForm
from .matching import (
    MAX_ALERTS_PER_REQUEST,
    donors_to_alert,
    notify_matching_donors,
    request_counts,
)
from .models import BloodRequest, DonorAlert

# Critical needs rise to the top of the board; everything else is newest
# first. Annotated rather than stored, since it is purely presentational.
URGENCY_ORDER = Case(
    When(urgency=BloodRequest.Urgency.CRITICAL, then=Value(0)),
    When(urgency=BloodRequest.Urgency.URGENT, then=Value(1)),
    default=Value(2),
    output_field=IntegerField(),
)

#: Tokens kept in the session so a requester can find their own requests again
#: in the same browser. The manage URL remains the real credential.
SESSION_TOKENS_KEY = "blood_request_tokens"


def _base_url(request):
    """Absolute prefix, so reply links work when opened from a handset."""
    return request.build_absolute_uri("/")


def _plural(count, singular, plural=None):
    return singular if count == 1 else (plural or f"{singular}s")


def blood_request_list(request):
    """The open-request board, plus any requests this browser posted."""
    open_requests = BloodRequest.objects.filter(
        status=BloodRequest.Status.OPEN
    ).order_by(URGENCY_ORDER, "-created_at")

    tokens = request.session.get(SESSION_TOKENS_KEY, [])

    mine = BloodRequest.objects.filter(status_token__in=tokens).order_by("-created_at")

    return render(request, "blood_requests/request_list.html", {
        "requests": open_requests,
        "mine": mine,
    })


def blood_request_create(request):
    """Submit a request. The manage page is the success destination."""
    if request.method == "POST":
        form = BloodRequestForm(request.POST)

        if form.is_valid():
            blood_request = form.save()

            # Keep the token in this browser's session so the requester can
            # find the request again without having bookmarked the URL.
            tokens = request.session.get(SESSION_TOKENS_KEY, [])

            if blood_request.status_token not in tokens:
                tokens.append(blood_request.status_token)

            request.session[SESSION_TOKENS_KEY] = tokens

            messages.success(
                request,
                "Your request has been posted. Use the control panel below to "
                "alert matching donors, and keep this page's address — it is the "
                "only way back to this request.",
            )

            return redirect(
                "blood_requests:blood_request_manage", token=blood_request.status_token
            )

    else:
        form = BloodRequestForm()

    return render(request, "blood_requests/request_form.html", {"form": form})


def blood_request_detail(request, pk):
    """The public view of a request.

    Deliberately carries no donor phone numbers: this page is reachable by
    anyone who guesses a primary key, so contact details stay behind the
    token-gated control panel.
    """
    blood_request = get_object_or_404(BloodRequest, pk=pk)

    return render(request, "blood_requests/request_detail.html", {
        "blood_request": blood_request,
        "counts": request_counts(blood_request),
        "compatible_types": blood_request.compatible_donor_types,
        "alerts": blood_request.alerts.select_related("donor"),
    })


def blood_request_manage(request, token):
    """The requester's control panel, unlocked by the status token."""
    blood_request = get_object_or_404(BloodRequest, status_token=token)

    return render(request, "blood_requests/request_manage.html", {
        "blood_request": blood_request,
        "counts": request_counts(blood_request),
        "compatible_types": blood_request.compatible_donor_types,
        "alerts": blood_request.alerts.select_related("donor"),
        "pending_count": donors_to_alert(blood_request).count(),
        "max_alerts": MAX_ALERTS_PER_REQUEST,
    })


@require_POST
def blood_request_notify(request, token):
    """Dispatch alerts to matching donors. Safe to press more than once."""
    blood_request = get_object_or_404(BloodRequest, status_token=token)

    if not blood_request.is_open:
        messages.error(
            request,
            "This request is no longer open, so no donors were alerted.",
        )

        return redirect(
            "blood_requests:blood_request_manage", token=blood_request.status_token
        )

    created, skipped = notify_matching_donors(
        blood_request, base_url=_base_url(request)
    )

    if created:
        messages.success(
            request,
            f"Alerted {created} {_plural(created, 'donor')}.",
        )
    else:
        messages.info(
            request,
            "No new donors to alert — every matching donor has already been "
            "alerted for this request.",
        )

    if skipped:
        messages.info(
            request,
            f"{skipped} more matching {_plural(skipped, 'donor')} were left for a "
            f"later dispatch (limit {MAX_ALERTS_PER_REQUEST} per press).",
        )

    return redirect(
        "blood_requests:blood_request_manage", token=blood_request.status_token
    )


@require_POST
def blood_request_status(request, token):
    """Mark a request fulfilled or cancelled, or reopen it."""
    blood_request = get_object_or_404(BloodRequest, status_token=token)

    new_status = request.POST.get("status")

    if new_status not in BloodRequest.Status.values:
        messages.error(request, "Unrecognised status — nothing was changed.")

        return redirect(
            "blood_requests:blood_request_manage", token=blood_request.status_token
        )

    blood_request.status = new_status
    # updated_at is auto_now, so it only moves if it is named here.
    blood_request.save(update_fields=["status", "updated_at"])

    messages.success(
        request,
        f"Request marked as {blood_request.get_status_display().lower()}.",
    )

    return redirect(
        "blood_requests:blood_request_manage", token=blood_request.status_token
    )


def alert_respond(request, token):
    """The donor's reply page: GET shows the choice, POST records it."""
    alert = get_object_or_404(
        DonorAlert.objects.select_related("donor", "blood_request"),
        token=token,
    )

    if request.method == "POST":
        # Refuse to change anything once the request is closed or the link has
        # aged out. The template hides the buttons, but a stale form or a
        # hand-crafted POST still has to be turned away here.
        if not alert.can_respond:
            messages.error(
                request,
                "This request is no longer accepting replies, so nothing was changed.",
            )

            return redirect("blood_requests:alert_respond", token=alert.token)

        choice = request.POST.get("response")

        if choice not in (
            DonorAlert.Response.AVAILABLE,
            DonorAlert.Response.NOT_AVAILABLE,
        ):
            messages.error(request, "Please choose one of the two options.")

            return redirect("blood_requests:alert_respond", token=alert.token)

        # Updates this alert in place. A donor may change their mind while the
        # request is open, and no reply can ever touch another donor's row.
        alert.response = choice
        alert.responded_at = timezone.now()
        alert.save(update_fields=["response", "responded_at"])

        messages.success(
            request,
            "Thank you — your reply has been recorded."
            if choice == DonorAlert.Response.AVAILABLE
            else "Thank you for letting us know.",
        )

        return redirect("blood_requests:alert_respond", token=alert.token)

    return render(request, "blood_requests/respond.html", {
        "alert": alert,
        "blood_request": alert.blood_request,
    })


def my_alerts(request):
    """Alerts for the donor in this session.

    This is the route that makes the feature demonstrable without any SMS:
    register a matching donor in the same browser and their alerts show up
    here with a working reply link.
    """
    donor = current_donor(request)

    alerts = DonorAlert.objects.none()

    if donor is not None:
        alerts = DonorAlert.objects.filter(donor=donor).select_related("blood_request")

    return render(request, "blood_requests/my_alerts.html", {
        "donor": donor,
        "alerts": alerts,
    })
