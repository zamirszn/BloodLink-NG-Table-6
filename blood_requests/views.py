"""Publishing blood requests, dispatching alerts, and collecting replies.

Every page here requires a login, for the same reason donor search does: the
board says who needs blood and where, and the control panel lists the phone
numbers of the donors who replied.

The one exception is :func:`alert_respond`. A donor reaches it by tapping the
link in the alert that was texted to them, on a handset that has no session, so
requiring a login there would break the reply flow outright. It is a
capability link instead: the token is personal to one alert and can only ever
answer that alert.

Two credentials are in play and they are not interchangeable. Being logged in
opens the page; the request's own ``status_token`` still decides *which*
request the control panel may act on. A primary key would not do, because it
is guessable and would let any logged-in stranger dispatch alerts to real
phone numbers or close someone else's request.
"""

from django.conf import settings
from django.contrib import messages
from django.core.cache import cache
from django.contrib.auth.decorators import login_required
from django.db.models import Case, IntegerField, Q, Value, When
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from donors import otp
from donors.forms import OTPCodeForm
from donors.models import PhoneOTP
from donors.ratelimit import client_ip
from donors.views import _flash_issue_result, current_donor

from .forms import BloodRequestForm
from .delivery import enqueue_alert
from .matching import (
    MAX_ALERTS_PER_REQUEST,
    volunteer_for_request,
    donors_to_alert,
    notify_matching_donors,
    open_requests_for_donor,
    request_counts,
)
from .models import BloodRequest, Donation, DonorAlert

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


def _can_manage(user, blood_request):
    """Owner, staff, or (for ownerless or token-enabled requests) a token holder."""
    if user.is_staff:
        return True
    if blood_request.requester_user_id is None:
        return True  # made before accounts owned requests: token-only access
    if blood_request.requester_user_id == user.id:
        return True

    return settings.MANAGE_TOKEN_LINK_ENABLED


def _managed_request(request, token):
    """The request for this token, 404 unless this user may manage it."""
    blood_request = _managed_request(request, token)

    if not _can_manage(request.user, blood_request):
        raise Http404

    return blood_request


def _base_url(request):
    """Absolute prefix, so reply links work when opened from a handset."""
    return request.build_absolute_uri("/")


def _plural(count, singular, plural=None):
    return singular if count == 1 else (plural or f"{singular}s")


@login_required(login_url="login")
def blood_request_list(request):
    """The open-request board, plus any requests this browser posted."""
    open_requests = BloodRequest.objects.open_now().order_by(URGENCY_ORDER, "-created_at")

    # Your requests are an account query. Older, ownerless requests are still
    # found through the tokens remembered in this browser.
    tokens = request.session.get(SESSION_TOKENS_KEY, [])

    mine = BloodRequest.objects.filter(
        Q(requester_user=request.user)
        | Q(requester_user__isnull=True, status_token__in=tokens)
    ).order_by("-created_at")

    return render(request, "blood_requests/request_list.html", {
        "requests": open_requests,
        "mine": mine,
    })


@login_required(login_url="login")
def blood_request_create(request):
    """Submit a request. The manage page is the success destination."""
    if request.method == "POST":
        form = BloodRequestForm(request.POST)

        if form.is_valid():
            # Slow automated/spam submissions without storing IP addresses in the DB.
            # Shared-cache deployment is recommended when running multiple workers.
            ip = request.META.get("REMOTE_ADDR", "unknown")
            phone = form.cleaned_data["requester_phone"]
            ip_key = f"bloodlink:request:ip:{ip}"
            phone_key = f"bloodlink:request:phone:{phone}"
            if cache.get(ip_key, 0) >= 5 or cache.get(phone_key, 0) >= 3:
                messages.error(request, "Too many requests have been submitted recently. Please wait before trying again.")
                return render(request, "blood_requests/request_form.html", {"form": form}, status=429)
            cache.set(ip_key, cache.get(ip_key, 0) + 1, 60 * 60)
            cache.set(phone_key, cache.get(phone_key, 0) + 1, 60 * 60)
            blood_request = form.save(commit=False)
            blood_request.requester_user = request.user
            blood_request.save()

            # A logged-in donor posting with their own verified number has
            # already proved they control it.
            me = current_donor(request)

            if me and me.phone_verified_at and me.phone == blood_request.requester_phone:
                blood_request.requester_phone_verified_at = me.phone_verified_at
                blood_request.save(update_fields=["requester_phone_verified_at"])

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


@login_required(login_url="login")
def blood_request_detail(request, pk):
    """The read-only view of a request.

    Deliberately carries no donor phone numbers. Logging in is what opens the
    page; the contact details stay behind the token-gated control panel on top
    of that, so a donor browsing the board still cannot dial a stranger.
    """
    blood_request = get_object_or_404(BloodRequest, pk=pk)

    return render(request, "blood_requests/request_detail.html", {
        "blood_request": blood_request,
        "counts": request_counts(blood_request),
        "compatible_types": blood_request.compatible_donor_types,
        "alerts": blood_request.alerts.select_related("donor"),
    })


@login_required(login_url="login")
def blood_request_manage(request, token):
    """The requester's control panel, unlocked by the status token."""
    blood_request = _managed_request(request, token)

    # A list, not a queryset, so each row can carry its own deep link.
    alerts = list(blood_request.alerts.select_related("donor"))

    donations = {d.donor_id: d for d in blood_request.donations.all()}

    for alert in alerts:
        alert.donation = donations.get(alert.donor_id)
        # The same WhatsApp link the donor search builds, so contact from here
        # names the blood type instead of sending a bare "someone needs blood".
        alert.donor.wa_url = alert.donor.whatsapp_url(
            blood_request.recipient_blood_type
        )

    return render(request, "blood_requests/request_manage.html", {
        "blood_request": blood_request,
        "counts": request_counts(blood_request),
        "compatible_types": blood_request.compatible_donor_types,
        "alerts": alerts,
        "pending_count": donors_to_alert(blood_request).count(),
        "failed_count": sum(1 for a in alerts if a.delivery_status == DonorAlert.DeliveryStatus.FAILED and a.response == DonorAlert.Response.PENDING),
        "max_alerts": MAX_ALERTS_PER_REQUEST,
    })


@login_required(login_url="login")
@require_POST
def blood_request_notify(request, token):
    """Dispatch alerts to matching donors. Safe to press more than once."""
    blood_request = _managed_request(request, token)

    if not blood_request.is_open:
        messages.error(
            request,
            "This request is no longer open, so no donors were alerted.",
        )

        return redirect(
            "blood_requests:blood_request_manage", token=blood_request.status_token
        )

    if not blood_request.requester_phone_verified_at:
        messages.error(
            request,
            "Verify your phone number before alerting donors.",
        )

        return redirect(
            "blood_requests:requester_verify", token=blood_request.status_token
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


@login_required(login_url="login")
@require_POST
def blood_request_retry(request, token):
    """Re-send alerts that failed to go out. Idempotent per alert."""
    blood_request = _managed_request(request, token)
    manage = redirect("blood_requests:blood_request_manage", token=blood_request.status_token)

    if not blood_request.is_open or not blood_request.requester_phone_verified_at:
        messages.error(request, "This request cannot send alerts right now.")

        return manage

    failed = list(
        blood_request.alerts.filter(
            delivery_status=DonorAlert.DeliveryStatus.FAILED,
            response=DonorAlert.Response.PENDING,
        ).values_list("pk", flat=True)
    )

    for alert_id in failed:
        enqueue_alert(alert_id, _base_url(request))

    messages.info(request, f"Retrying {len(failed)} {_plural(len(failed), 'alert')}.")

    return manage


@login_required(login_url="login")
@require_POST
def blood_request_extend(request, token):
    """Restart a request's clock (reopens it if it had expired)."""
    blood_request = _managed_request(request, token)

    if blood_request.can_extend:
        blood_request.extend()
        messages.success(request, "Request extended. It will stay open for a further period.")
    else:
        messages.error(request, "A fulfilled or cancelled request cannot be extended.")

    return redirect("blood_requests:blood_request_manage", token=blood_request.status_token)


@login_required(login_url="login")
def blood_request_manage_by_id(request, pk):
    """Owner's way back to a request without the token link (F7)."""
    blood_request = get_object_or_404(BloodRequest, pk=pk)

    if blood_request.requester_user_id != request.user.id and not request.user.is_staff:
        raise Http404

    return redirect("blood_requests:blood_request_manage", token=blood_request.status_token)


@login_required(login_url="login")
@require_POST
def record_donation(request, token, alert_id):
    """The requester notes that a responding donor gave blood (F4).

    This only *claims* it. The donor confirms from their own account, and only
    that confirmation updates their last donation date.
    """
    blood_request = _managed_request(request, token)
    alert = get_object_or_404(
        blood_request.alerts, pk=alert_id, response=DonorAlert.Response.AVAILABLE
    )

    _, created = Donation.objects.get_or_create(
        donor=alert.donor,
        blood_request=blood_request,
        defaults={"donated_on": timezone.localdate(), "recorded_by": request.user},
    )

    messages.success(
        request,
        f"Recorded. {alert.donor.name} will be asked to confirm."
        if created
        else "That donation was already recorded.",
    )

    return redirect("blood_requests:blood_request_manage", token=blood_request.status_token)


@login_required(login_url="login")
@require_POST
def confirm_donation(request, pk):
    """The donor confirms a donation recorded in their name."""
    donor = current_donor(request)
    donation = get_object_or_404(Donation, pk=pk, donor=donor) if donor else None

    if donation is None:
        raise Http404

    if request.POST.get("decision") == "reject":
        if not donation.is_confirmed:
            donation.delete()
            messages.info(request, "Thanks. That record has been removed and nothing changed.")
    else:
        donation.confirm()
        messages.success(request, "Thank you for donating. Your waiting period has started.")

    next_url = request.POST.get("next", "")

    if next_url and url_has_allowed_host_and_scheme(
        next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return redirect(next_url)

    return redirect("donor_dashboard")


@login_required(login_url="login")
@require_POST
def volunteer(request, pk):
    """A matching donor offers to donate to an open request (F6)."""
    blood_request = get_object_or_404(BloodRequest, pk=pk)

    _, outcome = volunteer_for_request(current_donor(request), blood_request)

    if outcome == "created":
        messages.success(request, "Thank you. The requester can now see that you can donate.")
    elif outcome == "already":
        messages.info(request, "You have already offered to donate to this request.")
    else:
        messages.error(request, "You do not match this request, or it is no longer open.")

    return redirect("blood_requests:my_alerts")


@login_required(login_url="login")
def requester_verify(request, token):
    """Confirm the requester's phone with a texted code before they can alert."""
    blood_request = _managed_request(request, token)
    manage = redirect("blood_requests:blood_request_manage", token=blood_request.status_token)

    if blood_request.requester_phone_verified_at:
        return manage

    form = OTPCodeForm()

    if request.method == "POST":
        if request.POST.get("action") == "send":
            _flash_issue_result(
                request,
                otp.issue_code(
                    blood_request.requester_phone,
                    PhoneOTP.Purpose.REQUESTER,
                    ip=client_ip(request),
                ),
            )

            return redirect("blood_requests:requester_verify", token=blood_request.status_token)

        form = OTPCodeForm(request.POST)

        if form.is_valid():
            outcome = otp.verify_code(
                blood_request.requester_phone,
                PhoneOTP.Purpose.REQUESTER,
                form.cleaned_data["code"],
            )

            if outcome == otp.OK:
                blood_request.requester_phone_verified_at = timezone.now()
                blood_request.save(update_fields=["requester_phone_verified_at", "updated_at"])
                messages.success(request, "Phone verified. You can now alert donors.")

                return manage

            form.add_error(
                "code",
                "Too many wrong attempts. Ask for a new code."
                if outcome == otp.LOCKED
                else "That code is incorrect or has expired.",
            )

    return render(request, "blood_requests/requester_verify.html", {
        "form": form,
        "blood_request": blood_request,
    })


@login_required(login_url="login")
@require_POST
def blood_request_status(request, token):
    """Mark a request fulfilled or cancelled, or reopen it."""
    blood_request = _managed_request(request, token)

    new_status = request.POST.get("status")

    if new_status not in (
        BloodRequest.Status.OPEN,
        BloodRequest.Status.FULFILLED,
        BloodRequest.Status.CANCELLED,
    ):
        messages.error(request, "Unrecognised status — nothing was changed.")

        return redirect(
            "blood_requests:blood_request_manage", token=blood_request.status_token
        )

    if new_status == BloodRequest.Status.OPEN and (
        blood_request.expires_at is None or blood_request.expires_at <= timezone.now()
    ):
        # Reopening a lapsed request restarts its clock.
        blood_request.extend()
    else:
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
    """The donor's reply page: GET shows the choice, POST records it.

    The only view in this module with no login gate, and deliberately so: the
    donor arrives from the link in their alert, opened on a handset that has no
    session. The token carries the authority instead, and it is scoped to one
    alert, so an anonymous visitor can answer their own alert and nothing else.
    """
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


@login_required(login_url="login")
def my_alerts(request):
    """Alerts for the logged-in donor, plus open requests that match them.

    Two lists, because an alert only exists once a requester has dispatched
    one. ``alerts`` are the ones that were sent, each with a working reply
    link whether or not the message ever left the outbox. ``matching`` are
    open requests the donor fits but has not been alerted about, so the page
    is useful before anyone presses the alert button.
    """
    donor = current_donor(request)

    alerts = DonorAlert.objects.none()
    matching = []

    if donor is not None:
        alerts = DonorAlert.objects.filter(donor=donor).select_related("blood_request")
        matching = open_requests_for_donor(donor)

    pending_donations = (
        donor.donations.filter(confirmed_by_donor_at__isnull=True).select_related("blood_request")
        if donor is not None
        else []
    )

    return render(request, "blood_requests/my_alerts.html", {
        "donor": donor,
        "alerts": alerts,
        "matching": matching,
        "pending_donations": pending_donations,
    })
