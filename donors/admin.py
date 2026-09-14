from django.contrib import admin
from .models import Donor


@admin.register(Donor)
class DonorAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "blood_type",
        "genotype",
        "location",
        "phone",
        "last_donation",
        "availability",
    )

