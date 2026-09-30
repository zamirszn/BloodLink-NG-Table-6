from datetime import date

from django import forms
from django.contrib.auth import authenticate
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import transaction

from .compatibility import ALL_BLOOD_TYPES
from .models import Donor
from .phone import normalize_ng_phone


class ControlMixin:
    """Give every non-radio input the shared `control` CSS class."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        for field in self.fields.values():
            if not isinstance(field.widget, forms.RadioSelect):
                field.widget.attrs.setdefault("class", "control")


class DonorForm(ControlMixin, forms.ModelForm):
    """The donor profile. Used to edit an existing donor."""

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
        label="Date of your last donation",
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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields["name"].label = "Full name"
        self.fields["name"].widget.attrs.update(
            placeholder="e.g. Chiamaka Okafor", autocomplete="name"
        )
        self.fields["location"].widget.attrs.update(
            placeholder="Area and city, e.g. Yaba, Lagos",
            autocomplete="address-level2",
        )
        self.fields["phone"].label = "Phone number"
        self.fields["phone"].widget.attrs.update(
            placeholder="0803 123 4567", inputmode="tel", autocomplete="tel"
        )
        self.fields["last_donation"].widget.attrs["max"] = date.today().isoformat()

    def clean_phone(self):
        phone = normalize_ng_phone(self.cleaned_data["phone"])

        if phone is None:
            raise ValidationError(
                "Enter a valid Nigerian mobile number, "
                "for example 0803 123 4567 or +234 803 123 4567."
            )

        other_donors = Donor.objects.filter(phone=phone).exclude(
            pk=self.instance.pk
        )
        other_users = User.objects.filter(username=phone)

        if self.instance.user_id:
            other_users = other_users.exclude(pk=self.instance.user_id)

        if other_donors.exists() or other_users.exists():
            raise ValidationError(
                "This phone number is already registered."
            )

        return phone

    def clean(self):
        cleaned_data = super().clean()

        donation_status = cleaned_data.get("donation_status")
        last_donation = cleaned_data.get("last_donation")

        if donation_status == "before":
            if not last_donation:
                self.add_error(
                    "last_donation",
                    "Please enter your last donation date."
                )
            elif last_donation > date.today():
                self.add_error(
                    "last_donation",
                    "The last donation date cannot be in the future."
                )

        elif donation_status == "never":
            # A leftover date from a hidden field must not count.
            cleaned_data["last_donation"] = None

        return cleaned_data

    def save(self, commit=True):
        donor = super().save(commit=commit)

        # Keep the login username in step with the phone number.
        if commit and donor.user_id:
            user = donor.user
            user.username = donor.phone
            user.first_name = donor.name[:150]
            user.save(update_fields=["username", "first_name"])

        return donor


class RegisterForm(DonorForm):
    """Registration: the donor profile plus a password for the new account."""

    blood_type = forms.ChoiceField(
        label="Your blood type",
        choices=DonorForm.BLOOD_TYPES,
        widget=forms.RadioSelect,
    )

    donation_status = forms.ChoiceField(
        label="Have you donated blood before?",
        choices=[
            ("never", "Never"),
            ("before", "Yes, I have"),
        ],
        initial="never",
        widget=forms.RadioSelect,
    )

    availability = forms.ChoiceField(
        label="Are you available to donate right now?",
        choices=[
            (True, "Yes"),
            (False, "No"),
        ],
        initial=True,
        widget=forms.RadioSelect,
    )

    password1 = forms.CharField(
        label="Password",
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )

    password2 = forms.CharField(
        label="Confirm password",
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )

    def clean(self):
        cleaned_data = super().clean()

        password1 = cleaned_data.get("password1")
        password2 = cleaned_data.get("password2")

        if password1 and password2 and password1 != password2:
            self.add_error("password2", "The two passwords do not match.")

        elif password1:
            # Run Django's password rules (length, common, all-numeric,
            # similar to the user's details) against the would-be user.
            candidate = User(
                username=cleaned_data.get("phone") or "",
                first_name=(cleaned_data.get("name") or "")[:150],
            )

            try:
                validate_password(password1, user=candidate)
            except ValidationError as error:
                self.add_error("password1", error)

        return cleaned_data

    def save(self, commit=True):
        # A donor without an account cannot log in, so the user and the
        # donor are always created together or not at all.
        with transaction.atomic():
            user = User.objects.create_user(
                username=self.cleaned_data["phone"],
                password=self.cleaned_data["password1"],
                first_name=self.cleaned_data["name"][:150],
            )

            donor = super().save(commit=False)
            donor.user = user
            donor.save()

        return donor


class LoginForm(ControlMixin, forms.Form):

    phone = forms.CharField(
        label="Phone number",
        widget=forms.TextInput(attrs={
            "placeholder": "0803 123 4567",
            "inputmode": "tel",
            "autocomplete": "tel",
            "autofocus": True,
        }),
    )

    password = forms.CharField(
        label="Password",
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}),
    )

    def __init__(self, *args, request=None, **kwargs):
        super().__init__(*args, **kwargs)

        self.request = request
        self.user = None

    def clean(self):
        cleaned_data = super().clean()

        raw_phone = cleaned_data.get("phone")
        password = cleaned_data.get("password")

        if raw_phone and password:
            # Same normalisation as registration, so 0803..., +234803...
            # and "0803 123 4567" all reach the same account.
            username = normalize_ng_phone(raw_phone) or raw_phone.strip()

            self.user = authenticate(
                self.request, username=username, password=password
            )

            if self.user is None:
                # One message for "no such number" and "wrong password" so
                # the form cannot be used to find out who is registered.
                raise ValidationError(
                    "Incorrect phone number or password."
                )

        return cleaned_data


class DonorSearchForm(ControlMixin, forms.Form):

    blood_type = forms.ChoiceField(
        label="Blood type of the person who needs blood",
        # The recipient's blood type, not the donor's: the search returns
        # every donor whose blood type the recipient can receive.
        help_text=(
            "We list donors whose blood the patient can safely receive. "
            "Choose \"Any\" to list every eligible donor without checking "
            "compatibility."
        ),
        choices=[("", "Any")] + [(t, t) for t in ALL_BLOOD_TYPES],
        initial="",
        required=False,
        widget=forms.RadioSelect,
    )

    location = forms.CharField(
        max_length=200,
        required=False,
        label="Location",
        widget=forms.TextInput(attrs={
            "placeholder": "Area or city, e.g. Ikeja",
        }),
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