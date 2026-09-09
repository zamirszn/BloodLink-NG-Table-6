from django.shortcuts import render


def register_donor(request):
    return render(request, "donors/register.html")

# Create your views here.
