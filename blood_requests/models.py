"""Blood requests, the donor alerts they dispatch, and the local outbox.

A patient or hospital publishes a :class:`BloodRequest`. Matching compatible
donors each get a :class:`DonorAlert` carrying a personal token they can use
to answer Available / Not. Every outbound message is recorded as a
:class:`NotificationMessage` so a dispatch can be proven after the fact.

Part 4 (verification, rate-limiting) is not implemented here, but nothing in
this module would need to change to add it.
"""

import secrets
from datetime import timedelta

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.urls import reverse
from django.utils import timezone

from donors.compatibility import ALL_BLOOD_TYPES

# How long a donor's alert link stays usable. Measured from the alert's
# creation time so no second column is needed to store an expiry.
ALERT_TOKEN_TTL = timedelta(days=7)

# Reuse the Part 2 vocabulary rather than restating the eight blood types.
BLOOD_TYPE_CHOICES = [(blood_type, blood_type) for blood_type in ALL_BLOOD_TYPES]


def generate_token():
    """Return a fresh URL-safe capability token.

    32 random bytes is 256 bits of entropy, rendered as 43 URL-safe
    characters, comfortably inside the 64-character columns below. It is used
    as a field default so there is no save() override for anyone to forget.
    """
    return secrets.token_urlsafe(32)


class BloodRequest(models.Model):
    """A published need for blood, from a patient or a hospital."""

    class Urgency(models.TextChoices):
        ROUTINE = "routine", "Routine"
        URGENT = "urgent", "Urgent"
        CRITICAL = "critical", "Critical"

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        FULFILLED = "fulfilled", "Fulfilled"
        CANCELLED = "cancelled", "Cancelled"

    requester_name = models.CharField(max_length=100)

    requester_phone = models.CharField(max_length=20)

    hospital = models.CharField(
        max_length=150,
        blank=True,
        help_text="Optional. Leave blank for an individual patient.",
    )

    # The type the *recipient* needs. Which donors can supply it is decided
    # by donors.compatibility.compatible_donor_types, never here.
    recipient_blood_type = models.CharField(max_length=3, choices=BLOOD_TYPE_CHOICES)

    location = models.CharField(
        max_length=200,
        blank=True,
        help_text="Optional. Blank means no location restriction on matching.",
    )

    urgency = models.CharField(
        max_length=8,
        choices=Urgency.choices,
        default=Urgency.URGENT,
    )

    units_needed = models.PositiveSmallIntegerField(
        default=1,
        validators=[MinValueValidator(1), MaxValueValidator(20)],
    )

    notes = models.TextField(blank=True)

    status = models.CharField(
        max_length=9,
        choices=Status.choices,
        default=Status.OPEN,
    )

    # The requester's control-panel credential. There is no authentication in
    # this project, so without this token anyone who guessed a primary key
    # could dispatch alerts to strangers or close someone else's request.
    # Stored raw: see the note on DonorAlert.token.
    status_token = models.CharField(
        max_length=64,
        unique=True,
        default=generate_token,
        editable=False,
    )

    created_at = models.DateTimeField(auto_now_add=True)

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "-created_at"]),
            models.Index(fields=["recipient_blood_type"]),
        ]

    def __str__(self):
        where = self.hospital or self.location or "unspecified location"

        return f"{self.recipient_blood_type} request for {where} ({self.get_urgency_display()})"

    @property
    def is_open(self):
        """Whether donors can still be alerted and still respond."""
        return self.status == self.Status.OPEN

    @property
    def compatible_donor_types(self):
        """Donor blood types this request's recipient can receive."""
        from donors.compatibility import compatible_donor_types

        return compatible_donor_types(self.recipient_blood_type)


class DonorAlert(models.Model):
    """One donor's alert for one request, and their answer to it.

    The alert and the response are the same row because they are created
    together and are always one-to-one; a separate response table would add a
    join and a nullable foreign key for nothing.
    """

    class Channel(models.TextChoices):
        SMS = "sms", "SMS"
        WHATSAPP = "whatsapp", "WhatsApp"

    class DeliveryStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        SENT = "sent", "Sent"
        FAILED = "failed", "Failed"

    class Response(models.TextChoices):
        PENDING = "pending", "Awaiting reply"
        AVAILABLE = "available", "Available"
        NOT_AVAILABLE = "not_available", "Not available"

    blood_request = models.ForeignKey(
        BloodRequest,
        on_delete=models.CASCADE,
        related_name="alerts",
    )

    donor = models.ForeignKey(
        "donors.Donor",
        on_delete=models.CASCADE,
        related_name="alerts",
    )

    # The donor's personal reply link. Stored raw, not hashed, and that is a
    # deliberate call: the message body in NotificationMessage holds the same
    # token verbatim for the outbox demo, and the token is already visible in
    # the URL and the log line, so hashing this column would protect nothing
    # while adding a hash on every request. It is a single-purpose capability
    # that can only ever answer its own alert. If the outbox body were ever
    # dropped, hashing would start to be worth it.
    token = models.CharField(
        max_length=64,
        unique=True,
        default=generate_token,
        editable=False,
    )

    channel = models.CharField(
        max_length=8,
        choices=Channel.choices,
        default=Channel.SMS,
    )

    delivery_status = models.CharField(
        max_length=8,
        choices=DeliveryStatus.choices,
        default=DeliveryStatus.PENDING,
    )

    sent_at = models.DateTimeField(null=True, blank=True)

    response = models.CharField(
        max_length=13,
        choices=Response.choices,
        default=Response.PENDING,
    )

    responded_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            # The real guarantee that re-dispatching cannot duplicate an
            # alert, independent of any application-level check.
            models.UniqueConstraint(
                fields=["blood_request", "donor"],
                name="unique_alert_per_request_donor",
            ),
        ]
        indexes = [
            models.Index(fields=["blood_request", "response"]),
        ]

    def __str__(self):
        return f"Alert to {self.donor.name} for request #{self.blood_request_id}"

    @property
    def is_expired(self):
        """Whether the reply link has aged out."""
        return timezone.now() > self.created_at + ALERT_TOKEN_TTL

    @property
    def can_respond(self):
        """Whether a reply can currently be recorded against this alert."""
        return self.blood_request.is_open and not self.is_expired

    @property
    def respond_path(self):
        """The server-relative reply URL for this alert's token."""
        return reverse("blood_requests:alert_respond", args=[self.token])


class NotificationMessage(models.Model):
    """A record of one outbound message: the local stand-in for a delivery log.

    No network call is made in this build. The default backend writes here and
    to the log, so a dispatch is both visible to the operator and provable
    afterwards. A real provider's delivery receipt would land in ``detail``.
    """

    class Status(models.TextChoices):
        SENT = "sent", "Sent"
        FAILED = "failed", "Failed"

    # Kept even if the alert is later removed, so the outbox stays a faithful
    # record of what was sent.
    alert = models.ForeignKey(
        DonorAlert,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="messages",
    )

    recipient_phone = models.CharField(max_length=20)

    channel = models.CharField(max_length=8, choices=DonorAlert.Channel.choices)

    body = models.TextField()

    backend = models.CharField(max_length=50)

    status = models.CharField(max_length=8, choices=Status.choices)

    detail = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.channel} to {self.recipient_phone} ({self.status})"
