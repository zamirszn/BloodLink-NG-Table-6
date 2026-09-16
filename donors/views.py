from django.shortcuts import render
from datetime import date, timedelta
from .forms import DonorForm, DonorSearchForm
from .models import Donor


def register_donor(request):
    if request.method == "POST":
        form = DonorForm(request.POST)

        if form.is_valid():
            form.save()

            return render(request, "donors/register.html", {
                "form": DonorForm(),
                "success": True
            })

    else:
        form = DonorForm()

    return render(request, "donors/register.html", {
        "form": form
    })


def search_donors(request):
    form = DonorSearchForm(request.GET or None)

    donors = Donor.objects.all()

    # 90-day eligibility check
    ninety_days_ago = date.today() - timedelta(days=90)

    if form.is_valid():
        blood_type = form.cleaned_data.get("blood_type")
        location = form.cleaned_data.get("location")
        availability = form.cleaned_data.get("availability")

        # Filter by blood type
        if blood_type:
            donors = donors.filter(blood_type=blood_type)

        # Filter by location
        if location:
            donors = donors.filter(
                location__icontains=location
            )

        # Filter by availability
        if availability:
            donors = donors.filter(
                availability=availability
            )

        # Only show donors who are eligible
        # They must have never donated OR
        # their last donation must be at least 90 days ago.
        donors = donors.filter(
            last_donation__isnull=True
        ) | donors.filter(
            last_donation__lte=ninety_days_ago
        )

    return render(request, "donors/search.html", {
        "form": form,
        "donors": donors,
    })