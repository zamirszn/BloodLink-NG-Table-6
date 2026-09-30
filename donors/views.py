from django.contrib import messages
from django.shortcuts import redirect, render

from .compatibility import compatible_donor_types
from .eligibility import eligibility_cutoff, eligible_q, next_eligible_date
from .forms import DonorForm, DonorSearchForm
from .models import Donor


def current_donor(request):
    """The donor this browser session belongs to, or None.

    There is no authentication here: the donor ID written to the session at
    registration is what identifies the current donor. Every view that needs
    "my donor" goes through this one lookup so the rule stays in one place.
    """
    donor_id = request.session.get("donor_id")

    if donor_id is None:
        return None

    return Donor.objects.filter(pk=donor_id).first()


def register_donor(request):
    if request.method == "POST":
        form = DonorForm(request.POST)

        if form.is_valid():
            donor = form.save()

            # Remember which donor just registered so /dashboard/ can show them.
            request.session["donor_id"] = donor.pk

            return redirect("donor_dashboard")

    else:
        form = DonorForm()

    return render(request, "donors/register.html", {
        "form": form
    })


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
    })


def edit_donor(request):
    donor = current_donor(request)

    # No donor in this session, or the record is gone: this session has
    # nothing it is allowed to edit. Never fall back to some other donor's
    # record — the dashboard already explains the situation and offers
    # registration, so send them there.
    if donor is None:
        return redirect("donor_dashboard")

    if request.method == "POST":
        # Bound to the EXISTING record, so a valid save updates this donor
        # instead of inserting a new one.
        form = DonorForm(request.POST, instance=donor)

        if form.is_valid():
            form.save()

            messages.success(request, "Your profile has been updated.")

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
    # This is applied BEFORE (and independently of) the form filters.
    # Opening /search/ with no parameters leaves the form unbound, so
    # relying on form.is_valid() here would silently skip the rule.
    ninety_days_ago = eligibility_cutoff()

    donors = donors.filter(eligible_q())

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

    return render(request, "donors/search.html", {
        "form": form,
        "donors": donors,
        "recipient_type": recipient_type,
        "compatible_types": compatible_donor_types(recipient_type),
        # Exposed so each result card can show the 90-day eligibility
        # status using the same cutoff that filtered the queryset above.
        "ninety_days_ago": ninety_days_ago,
    })