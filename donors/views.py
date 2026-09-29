from django.db.models import Q
from django.shortcuts import redirect, render
from datetime import date, timedelta
from .compatibility import compatible_donor_types
from .forms import DonorForm, DonorSearchForm
from .models import Donor


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
    donor_id = request.session.get("donor_id")

    donor = None

    if donor_id is not None:
        donor = Donor.objects.filter(pk=donor_id).first()

    return render(request, "donors/dashboard.html", {
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
    ninety_days_ago = date.today() - timedelta(days=90)

    donors = donors.filter(
        Q(last_donation__isnull=True)
        | Q(last_donation__lte=ninety_days_ago)
    )

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
    })