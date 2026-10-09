import django.db.models.deletion
from datetime import timedelta

import django.core.validators
from django.conf import settings
from django.db import migrations, models


def backfill_expiry(apps, schema_editor):
    BloodRequest = apps.get_model("blood_requests", "BloodRequest")
    days = {"critical": 3, "urgent": 7, "routine": 14}

    for blood_request in BloodRequest.objects.filter(status="open", expires_at__isnull=True):
        blood_request.expires_at = blood_request.created_at + timedelta(days=days.get(blood_request.urgency, 7))
        blood_request.save(update_fields=["expires_at"])


class Migration(migrations.Migration):
    dependencies = [
        ("donors", "0006_donor_eligible_reminder_sent_for"),
        ("blood_requests", "0004_delivery_tracking"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="bloodrequest",
            name="expires_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="bloodrequest",
            name="expiry_reminder_sent_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="bloodrequest",
            name="requester_user",
            field=models.ForeignKey(
                blank=True, editable=False, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="blood_requests", to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterField(
            model_name="bloodrequest",
            name="status",
            field=models.CharField(
                choices=[("open", "Open"), ("fulfilled", "Fulfilled"), ("cancelled", "Cancelled"), ("expired", "Expired")],
                default="open", max_length=9,
            ),
        ),
        migrations.AddField(
            model_name="donoralert",
            name="source",
            field=models.CharField(
                choices=[("dispatch", "Alerted by requester"), ("volunteer", "Volunteered")],
                default="dispatch", max_length=9,
            ),
        ),
        migrations.AlterField(
            model_name="donoralert",
            name="delivery_status",
            field=models.CharField(
                choices=[("pending", "Pending"), ("sending", "Sending"), ("not_sent", "Not sent (volunteer)"), ("sent", "Sent"), ("delivered", "Delivered"), ("failed", "Failed")],
                default="pending", max_length=12,
            ),
        ),
        migrations.RunPython(backfill_expiry, migrations.RunPython.noop),
        migrations.CreateModel(
            name="Donation",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("donated_on", models.DateField()),
                ("units", models.PositiveSmallIntegerField(default=1, validators=[django.core.validators.MinValueValidator(1)])),
                ("confirmed_by_donor_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("blood_request", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="donations", to="blood_requests.bloodrequest")),
                ("donor", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="donations", to="donors.donor")),
                ("recorded_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-donated_on", "-created_at"]},
        ),
        migrations.AddConstraint(
            model_name="donation",
            constraint=models.UniqueConstraint(
                condition=models.Q(("blood_request__isnull", False)),
                fields=("donor", "blood_request"),
                name="one_donation_per_donor_per_request",
            ),
        ),
    ]
