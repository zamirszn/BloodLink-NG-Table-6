from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("donors", "0005_donor_sms_opt_out")]

    operations = [
        migrations.AddField(
            model_name="donor",
            name="eligible_reminder_sent_for",
            field=models.DateField(blank=True, null=True),
        ),
    ]
