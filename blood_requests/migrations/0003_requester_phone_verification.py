from django.db import migrations, models


def trust_existing(apps, schema_editor):
    BloodRequest = apps.get_model("blood_requests", "BloodRequest")
    for blood_request in BloodRequest.objects.all():
        blood_request.requester_phone_verified_at = blood_request.created_at
        blood_request.save(update_fields=["requester_phone_verified_at"])


class Migration(migrations.Migration):
    dependencies = [("blood_requests", "0002_bloodrequest_hospital_verified")]

    operations = [
        migrations.AddField(
            model_name="bloodrequest",
            name="requester_phone_verified_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(trust_existing, migrations.RunPython.noop),
    ]
