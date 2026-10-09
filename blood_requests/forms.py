from django import forms

from donors.forms import ControlMixin, LocationFieldsMixin

from .models import BLOOD_TYPE_CHOICES, BloodRequest


class BloodRequestForm(LocationFieldsMixin, ControlMixin, forms.ModelForm):
    """The public request-submission form.

    Blood type and urgency are declared explicitly with an empty first choice
    so neither can be submitted by accident. Without that, a ``<select>``
    preselects whatever choice happens to be first, and a requester could
    post a "routine" request for what is actually an emergency.
    """

    recipient_blood_type = forms.ChoiceField(
        label="Recipient blood type",
        choices=[("", "Select the blood type needed")] + BLOOD_TYPE_CHOICES,
        help_text="The blood type of the person who needs blood.",
    )

    urgency = forms.ChoiceField(
        label="Urgency",
        choices=[("", "Select urgency")] + list(BloodRequest.Urgency.choices),
        help_text="Choose Critical when the need is immediate.",
    )

    # A blank location is allowed: it means "no restriction on matching".
    location_required = False

    class Meta:
        model = BloodRequest
        fields = [
            "requester_name",
            "requester_phone",
            "hospital",
            "recipient_blood_type",
            "urgency",
            "units_needed",
            "notes",
        ]
        labels = {
            "requester_name": "Your name",
            "requester_phone": "Phone number",
            "units_needed": "Units needed",
            "notes": "Additional details",
        }
        widgets = {
            "requester_name": forms.TextInput(
                attrs={"placeholder": "e.g. Ada Obi"}
            ),
            "requester_phone": forms.TextInput(
                attrs={"placeholder": "e.g. 08012345678"}
            ),
            "hospital": forms.TextInput(
                attrs={"placeholder": "Optional — leave blank if this is for one patient"}
            ),
            "units_needed": forms.NumberInput(attrs={"min": 1, "max": 20}),
            "notes": forms.Textarea(
                attrs={
                    "rows": 3,
                    "placeholder": "Optional — ward, time, anything a donor should know",
                }
            ),
        }

    def clean_requester_name(self):
        return self.cleaned_data["requester_name"].strip()

    def clean_requester_phone(self):
        return self.cleaned_data["requester_phone"].strip()
