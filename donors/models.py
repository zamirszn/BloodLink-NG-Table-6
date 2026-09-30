import re
from urllib.parse import quote

from django.conf import settings
from django.db import models


class Donor(models.Model):
    # The login account for this donor. Nullable so donors registered before
    # accounts existed keep working in the database; they just cannot log in.
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="donor",
    )
    name = models.CharField(max_length=100)
    blood_type = models.CharField(max_length=3)
    genotype = models.CharField(max_length=2)
    location = models.CharField(max_length=200)
    phone = models.CharField(max_length=20)
    last_donation = models.DateField(null=True, blank=True)
    availability = models.BooleanField(default=True)

    @property
    def tel_phone(self):
        """The phone field reduced to a safe `tel:` link target.

        Only digits are kept, with an optional single leading "+" preserved
        for international numbers. Returns "" when there is no usable number,
        so the template can skip the link instead of rendering a broken one.
        """
        raw = (self.phone or "").strip()

        if not raw:
            return ""

        digits = re.sub(r"\D", "", raw)

        if not digits:
            return ""

        return f"+{digits}" if raw.startswith("+") else digits

    @property
    def whatsapp_phone(self):
        """Digits in international format for wa.me links, or "".

        Local Nigerian numbers (0803...) become 234803...
        """
        digits = re.sub(r"\D", "", self.phone or "")

        if not digits:
            return ""

        if digits.startswith("0"):
            return "234" + digits[1:]

        return digits

    def whatsapp_url(self, recipient_type=""):
        """A wa.me link with a ready-to-send message, or "" without a number."""
        number = self.whatsapp_phone

        if not number:
            return ""

        need = f"{recipient_type} blood" if recipient_type else "blood"
        text = (
            f"Hello {self.name}, I found you on BloodLink NG. "
            f"Someone needs {need}. Are you able to donate?"
        )

        return f"https://wa.me/{number}?text={quote(text)}"

    def __str__(self):
        return self.name