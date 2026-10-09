from django.db import migrations, models


def grandfather_existing(apps, schema_editor):
    Donor = apps.get_model("donors", "Donor")
    Donor.objects.update(verification_grace=True)


class Migration(migrations.Migration):
    dependencies = [("donors", "0003_donor_user")]

    operations = [
        migrations.AddField(
            model_name="donor",
            name="phone_verified_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="donor",
            name="verification_grace",
            field=models.BooleanField(default=False),
        ),
        migrations.RunPython(grandfather_existing, migrations.RunPython.noop),
        migrations.CreateModel(
            name="PhoneOTP",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("phone", models.CharField(max_length=20)),
                ("purpose", models.CharField(choices=[("signup", "Sign-up"), ("reset", "Password reset"), ("requester", "Requester")], max_length=10)),
                ("code_hash", models.CharField(max_length=64)),
                ("expires_at", models.DateTimeField()),
                ("attempts", models.PositiveSmallIntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "indexes": [models.Index(fields=["phone", "purpose", "-created_at"], name="otp_lookup_idx")],
            },
        ),
    ]
