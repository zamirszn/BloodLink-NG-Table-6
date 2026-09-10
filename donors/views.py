from django.shortcuts import render
from .forms import DonorForm


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

    return render(request, "donors/register.html", {"form": form})