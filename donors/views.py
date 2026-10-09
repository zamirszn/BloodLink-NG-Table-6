from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.models import User
from django.utils import timezone
from django.contrib.auth.decorators import login_required
from django.db.models import Case, IntegerField, Value, When
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from .compatibility import ALL_BLOOD_TYPES, COMPATIBILITY, compatible_donor_types
from .eligibility import eligibility_cutoff, eligible_q, next_eligible_date
from . import otp
from .forms import (
    DonorForm,
    DonorSearchForm,
    LoginForm,
    OTPCodeForm,
    PasswordResetConfirmForm,
    PasswordResetRequestForm,
    RegisterForm,
)
from .models import Donor, PhoneOTP
from .ratelimit import client_ip, hit
from .verification import verified_q


# Donor blood type -> recipient types that can receive it. This is the
# COMPATIBILITY table read the other way round; it feeds the "who can you
# help" panel on the registration page.
RECIPIENTS_BY_DONOR_TYPE = {
    donor_type: [
        recipient_type
        for recipient_type in ALL_BLOOD_TYPES
        if donor_type in COMPATIBILITY[recipient_type]
    ]
    for donor_type in ALL_BLOOD_TYPES
}


def home(request):
    """Public landing page for BloodLink NG."""
    return render(request, "donors/home.html")


def current_donor(request):
    """The donor profile of the logged-in user, or None.

    None also covers a logged-in account with no donor profile (for
    example an admin user), so callers must handle it.
    """
    if not request.user.is_authenticated:
        return None

    return Donor.objects.filter(user=request.user).first()


def register_donor(request):
    if current_donor(request) is not None:
        return redirect("donor_dashboard")

    if request.method == "POST":
        form = RegisterForm(request.POST)

        if form.is_valid():
            donor = form.save()

            # Registering also signs the donor in. The donor is not searchable
            # or alertable until the texted code has been entered.
            login(request, donor.user)

            status = otp.issue_code(
                donor.phone, PhoneOTP.Purpose.SIGNUP, ip=client_ip(request)
            )
            _flash_issue_result(request, status)

            return redirect("verify_phone")

    else:
        form = RegisterForm()

    return render(request, "donors/register.html", {
        "form": form,
        "recipients_by_donor_type": RECIPIENTS_BY_DONOR_TYPE,
    })


def _flash_issue_result(request, status):
    if status == otp.SENT:
        messages.success(request, "We texted you a 6-digit code.")
    elif status == otp.COOLDOWN:
        messages.info(request, "A code was sent a moment ago. Please wait a minute before asking for another.")
    elif status == otp.THROTTLED:
        messages.error(request, "Too many codes requested. Please try again later.")
    else:
        messages.error(request, "We could not send the text message. Please try again shortly.")


@login_required(login_url="login")
def verify_phone(request):
    """Confirm the donor's number with the code texted to it (F2)."""
    donor = current_donor(request)

    if donor is None or donor.phone_verified_at:
        return redirect("donor_dashboard")

    form = OTPCodeForm()

    if request.method == "POST":
        if request.POST.get("action") == "send":
            _flash_issue_result(
                request,
                otp.issue_code(donor.phone, PhoneOTP.Purpose.SIGNUP, ip=client_ip(request)),
            )

            return redirect("verify_phone")

        form = OTPCodeForm(request.POST)

        if form.is_valid():
            outcome = otp.verify_code(
                donor.phone, PhoneOTP.Purpose.SIGNUP, form.cleaned_data["code"]
            )

            if outcome == otp.OK:
                donor.phone_verified_at = timezone.now()
                donor.save(update_fields=["phone_verified_at"])
                messages.success(request, "Your phone number is verified.")

                return redirect("donor_dashboard")

            if outcome == otp.LOCKED:
                form.add_error("code", "Too many wrong attempts. Ask for a new code.")
            elif outcome in (otp.EXPIRED, otp.NONE):
                form.add_error("code", "That code has expired. Ask for a new one.")
            else:
                form.add_error("code", "That code is not correct.")

    return render(request, "donors/verify_phone.html", {"form": form, "donor": donor})


RESET_SESSION_KEY = "password_reset_phone"
_GENERIC_RESET_NOTICE = (
    "If that number is registered, we have texted it a code. "
    "Enter the code below with your new password."
)


def password_reset_request(request):
    """Step 1 of an SMS password reset. Identical response for every number."""
    form = PasswordResetRequestForm(request.POST or None)

    if request.method == "POST" and form.is_valid():
        phone = form.cleaned_data["phone"]
        ip = client_ip(request)

        # Throttled before looking the number up, so the limits cannot be used
        # to tell registered numbers from unregistered ones.
        allowed = hit(f"reset:ip:{ip}", 10, 3600) and hit(f"reset:phone:{phone}", 5, 3600)

        if allowed and User.objects.filter(username=phone, is_active=True).exists():
            otp.issue_code(phone, PhoneOTP.Purpose.RESET, ip=ip)
        else:
            otp.dummy_work()

        request.session[RESET_SESSION_KEY] = phone
        messages.info(request, _GENERIC_RESET_NOTICE)

        return redirect("password_reset_confirm")

    return render(request, "donors/password_reset_request.html", {"form": form})


def password_reset_confirm(request):
    """Step 2: enter the code and choose a new password."""
    phone = request.session.get(RESET_SESSION_KEY)

    if not phone:
        return redirect("password_reset_request")

    user = User.objects.filter(username=phone, is_active=True).first()

    if request.method == "POST":
        form = PasswordResetConfirmForm(request.POST, user=user)

        if form.is_valid():
            outcome = otp.verify_code(phone, PhoneOTP.Purpose.RESET, form.cleaned_data["code"])

            if outcome == otp.OK and user is not None:
                user.set_password(form.cleaned_data["password1"])
                user.save(update_fields=["password"])
                # Changing the password changes the session auth hash, which
                # logs out every other session for this user.

                donor = Donor.objects.filter(user=user).first()
                if donor and not donor.phone_verified_at:
                    # They just proved they control this number.
                    donor.phone_verified_at = timezone.now()
                    donor.save(update_fields=["phone_verified_at"])

                request.session.pop(RESET_SESSION_KEY, None)
                messages.success(request, "Your password has been changed. Please log in.")

                return redirect("login")

            # One message for wrong, expired, locked and unknown numbers.
            form.add_error("code", "That code is incorrect or has expired.")
    else:
        form = PasswordResetConfirmForm(user=user)

    return render(request, "donors/password_reset_confirm.html", {"form": form, "phone": phone})


def login_view(request):
    if request.user.is_authenticated:
        return redirect("donor_dashboard")

    next_url = request.POST.get("next") or request.GET.get("next", "")

    if request.method == "POST":
        form = LoginForm(request.POST, request=request)

        if form.is_valid():
            login(request, form.user)

            # Only follow a "next" address that stays on this site.
            is_safe = url_has_allowed_host_and_scheme(
                next_url,
                allowed_hosts={request.get_host()},
                require_https=request.is_secure(),
            )

            return redirect(next_url if is_safe else "donor_dashboard")

    else:
        form = LoginForm(request=request)

    return render(request, "donors/login.html", {
        "form": form,
        "next": next_url,
    })


@require_POST
def logout_view(request):
    logout(request)

    messages.info(request, "You have been logged out.")

    return redirect("login")


@login_required(login_url="login")
def donor_dashboard(request):
    donor = current_donor(request)

    # The 90-day rule itself lives in donors/eligibility.py, shared with the
    # blood-request matching so the two can never disagree. Here it only
    # drives what this donor is told about their own status.
    ninety_days_ago = eligibility_cutoff()

    is_eligible = False
    next_date = None

    if donor is not None:
        if donor.last_donation is None:
            is_eligible = True
        else:
            is_eligible = donor.last_donation <= ninety_days_ago
            # The first day the 90-day wait is over, purely for display.
            next_date = next_eligible_date(donor.last_donation)

    return render(request, "donors/dashboard.html", {
        "donor": donor,
        "ninety_days_ago": ninety_days_ago,
        "is_eligible": is_eligible,
        "next_eligible_date": next_date,
        "phone_verified": bool(donor and donor.phone_verified_at),
        "history": donor.donations.filter(confirmed_by_donor_at__isnull=False)[:20] if donor else [],
        "pending_donations": donor.donations.filter(confirmed_by_donor_at__isnull=True) if donor else [],
    })


@login_required(login_url="login")
def edit_donor(request):
    donor = current_donor(request)

    # A logged-in account with no donor profile has nothing to edit. Never
    # fall back to some other donor's record; the dashboard explains it.
    if donor is None:
        return redirect("donor_dashboard")

    if request.method == "POST":
        # Bound to the EXISTING record, so a valid save updates this donor
        # instead of inserting a new one.
        form = DonorForm(request.POST, instance=donor)

        if form.is_valid():
            form.save()

            messages.success(request, "Your profile has been updated.")

            if "phone" in form.changed_data:
                messages.info(request, "You changed your number, so please verify it again.")

                return redirect("verify_phone")

            return redirect("donor_dashboard")

    else:
        # The stored record only knows the last donation date, so tell the
        # form which of its two "have you donated before?" answers that means.
        form = DonorForm(instance=donor, initial={
            "donation_status": "before" if donor.last_donation else "never"
        })

    return render(request, "donors/edit_profile.html", {
        "form": form,
        "donor": donor,
    })


# Donor phone numbers are shown here, so only logged-in users can search.
# To open the search to everyone, remove the decorator below.
@login_required(login_url="login")
def search_donors(request):
    form = DonorSearchForm(request.GET or None)

    donors = Donor.objects.all()

    # The blood type the *recipient* needs. Used by the template to label
    # each result as compatible with the person who needs blood.
    recipient_type = ""

    # Only show donors who are eligible.
    # They must have never donated OR their last donation must be at
    # least 90 days ago.
    #
    # The rule comes from donors/eligibility.py, the same module the
    # blood-request matching uses, so a donor can never be shown as eligible
    # here and then left unalerted for a matching request.
    #
    # This is applied BEFORE (and independently of) the form filters.
    # Opening /search/ with no parameters leaves the form unbound, so
    # relying on form.is_valid() here would silently skip the rule.
    ninety_days_ago = eligibility_cutoff()

    donors = donors.filter(eligible_q()).filter(verified_q())

    # The person searching is not a match for themselves.
    me = current_donor(request)

    if me is not None:
        donors = donors.exclude(pk=me.pk)

    if form.is_valid():
        blood_type = form.cleaned_data.get("blood_type")
        location = form.cleaned_data.get("location")
        availability = form.cleaned_data.get("availability")

        # Filter by recipient blood type.
        #
        # The selected value is the RECIPIENT's blood type, so a donor
        # matches when their own blood type is one the recipient can
        # receive. The rules live in compatibility.py, not here.
        #
        # An empty selection means "Any blood type": no restriction.
        if blood_type:
            recipient_type = blood_type

            donors = donors.filter(
                blood_type__in=compatible_donor_types(blood_type)
            )

        # Filter by location
        if location:
            donors = donors.filter(
                location__icontains=location
            )

        # Filter by availability
        if availability:
            is_available = availability == "True"

            donors = donors.filter(
                availability=is_available
            )

    # Best matches first: available donors, then donors with exactly the
    # recipient's blood type, then alphabetical.
    if recipient_type:
        donors = donors.annotate(
            type_rank=Case(
                When(blood_type=recipient_type, then=Value(0)),
                default=Value(1),
                output_field=IntegerField(),
            )
        ).order_by("-availability", "type_rank", "name")
    else:
        donors = donors.order_by("-availability", "name")

    donors = list(donors)

    for donor in donors:
        donor.wa_url = donor.whatsapp_url(recipient_type)

    return render(request, "donors/search.html", {
        "form": form,
        "donors": donors,
        "recipient_type": recipient_type,
        "compatible_types": compatible_donor_types(recipient_type),
        "searched": bool(request.GET),
        # Exposed so each result card can show the 90-day eligibility
        # status using the same cutoff that filtered the queryset above.
        "ninety_days_ago": ninety_days_ago,
    })