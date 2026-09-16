from django import forms
from .models import Donor


class DonorForm(forms.ModelForm):

    BLOOD_TYPES = [
        ("A+", "A+"),
        ("A-", "A-"),
        ("B+", "B+"),
        ("B-", "B-"),
        ("AB+", "AB+"),
        ("AB-", "AB-"),
        ("O+", "O+"),
        ("O-", "O-"),
    ]

    GENOTYPES = [
        ("AA", "AA"),
        ("AS", "AS"),
        ("SS", "SS"),
        ("AC", "AC"),
        ("SC", "SC"),
    ]

    blood_type = forms.ChoiceField(choices=BLOOD_TYPES)

    genotype = forms.ChoiceField(choices=GENOTYPES)

    donation_status = forms.ChoiceField(
        label="Have you donated blood before?",
        choices=[
            ("never", "Never"),
            ("before", "I have donated before"),
        ]
    )

    last_donation = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date"})
    )

    availability = forms.ChoiceField(
        label="Are you currently available to donate blood?",
        choices=[
            (True, "Yes"),
            (False, "No"),
        ]
    )

    class Meta:
        model = Donor
        fields = [
            "name",
            "blood_type",
            "genotype",
            "location",
            "phone",
            "donation_status",
            "last_donation",
            "availability",
        ]

    def clean(self):
        cleaned_data = super().clean()

        donation_status = cleaned_data.get("donation_status")
        last_donation = cleaned_data.get("last_donation")

        if donation_status == "before" and not last_donation:
            self.add_error(
                "last_donation",
                "Please enter your last donation date."
            )

        return cleaned_data


class DonorSearchForm(forms.Form):

    blood_type = forms.ChoiceField(
        choices=[
            ("", "Any blood type"),
            ("A+", "A+"),
            ("A-", "A-"),
            ("B+", "B+"),
            ("B-", "B-"),
            ("AB+", "AB+"),
            ("AB-", "AB-"),
            ("O+", "O+"),
            ("O-", "O-"),
        ],
        required=False
    )

    location = forms.CharField(
        max_length=200,
        required=False,
        label="Location"
    )

    availability = forms.ChoiceField(
        choices=[
            ("", "Any availability"),
            ("True", "Available"),
            ("False", "Not available"),
        ],
        required=False,
        label="Availability"
    )