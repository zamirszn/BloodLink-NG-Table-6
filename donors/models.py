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

    # F4: the last_donation date a "you can donate again" text was sent for,
    # so each waiting period produces one reminder.
    eligible_reminder_sent_for = models.DateField(null=True, blank=True)

    # F3: set when the donor replies STOP; cleared when they say they are
    # available again. Opted-out donors receive no alerts.
    sms_opt_out = models.BooleanField(default=False)

    # F2: set when the donor proves they control the number.
    phone_verified_at = models.DateTimeField(null=True, blank=True)
    # True for donors who existed before verification was introduced; they stay
    # searchable until PHONE_VERIFICATION_GRACE_ENDS.
    verification_grace = models.BooleanField(default=False)

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


class PhoneOTP(models.Model):
    """A hashed one-time code sent to a phone for one purpose."""

    class Purpose(models.TextChoices):
        SIGNUP = "signup", "Sign-up"
        RESET = "reset", "Password reset"
        REQUESTER = "requester", "Requester"

    phone = models.CharField(max_length=20)
    purpose = models.CharField(max_length=10, choices=Purpose.choices)
    code_hash = models.CharField(max_length=64)
    expires_at = models.DateTimeField()
    attempts = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["phone", "purpose", "-created_at"], name="otp_lookup_idx")]

    def __str__(self):
        return f"{self.purpose} code for {self.phone}"
