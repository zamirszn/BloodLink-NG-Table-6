from django import forms
from .models import Donor


class DonorForm(forms.ModelForm):
    class Meta:
        model = Donor
        fields = [
            "name",
            "blood_type",
            "genotype",
            "location",
            "phone",
            "last_donation",
            "availability",
        ]