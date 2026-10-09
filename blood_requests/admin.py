from django.contrib import admin

from .models import BloodRequest, Donation, DonorAlert, NotificationMessage


class DonorAlertInline(admin.TabularInline):
    """A request's dispatch and reply state, on one page."""

    model = DonorAlert
    extra = 0
    can_delete = False
    fields = (
        "donor",
        "channel",
        "delivery_status",
        "sent_at",
        "response",
        "responded_at",
    )
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        # Alerts are created by the matching service, not by hand.
        return False


@admin.register(BloodRequest)
class BloodRequestAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "recipient_blood_type",
        "location",
        "urgency",
        "status",
        "expires_at",
        "hospital_verified",
        "units_needed",
        "requester_name",
        "created_at",
    )
    list_filter = ("status", "urgency", "recipient_blood_type", "hospital_verified")
    search_fields = ("requester_name", "hospital", "location", "requester_phone")
    # The control-panel credential must never be editable from the admin.
    readonly_fields = ("status_token", "requester_user", "expiry_reminder_sent_at", "created_at", "updated_at")
    fieldsets = (
        ("Request", {"fields": ("requester_name", "requester_phone", "hospital", "hospital_verified", "recipient_blood_type", "location", "urgency", "units_needed", "notes", "status", "expires_at")}),
        ("System", {"fields": ("status_token", "requester_user", "expiry_reminder_sent_at", "created_at", "updated_at")}),
    )
    inlines = [DonorAlertInline]


@admin.register(DonorAlert)
class DonorAlertAdmin(admin.ModelAdmin):
    list_display = (
        "blood_request",
        "donor",
        "channel",
        "delivery_status",
        "response",
        "responded_at",
        "created_at",
    )
    list_filter = ("channel", "delivery_status", "response")
    search_fields = ("donor__name", "donor__phone", "token")
    readonly_fields = ("token", "created_at", "sent_at", "responded_at")


@admin.register(NotificationMessage)
class NotificationMessageAdmin(admin.ModelAdmin):
    """The local outbox. Read this to retrieve a donor's reply link."""

    list_display = (
        "created_at",
        "backend",
        "channel",
        "recipient_phone",
        "status",
    )
    list_filter = ("backend", "channel", "status")
    search_fields = ("recipient_phone", "body")
    readonly_fields = ("created_at",)


@admin.register(Donation)
class DonationAdmin(admin.ModelAdmin):
    list_display = ("donor", "donated_on", "units", "blood_request", "recorded_by", "confirmed_by_donor_at")
    list_filter = ("donated_on",)
    search_fields = ("donor__name", "donor__phone")
    readonly_fields = ("created_at",)
