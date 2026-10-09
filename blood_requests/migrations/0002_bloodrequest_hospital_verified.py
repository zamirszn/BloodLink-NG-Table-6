from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("blood_requests", "0001_initial")]

    operations = [
        migrations.AddField(
            model_name="bloodrequest",
            name="hospital_verified",
            field=models.BooleanField(default=False, help_text="Only staff should enable this after independently verifying the hospital."),
        ),
    ]
