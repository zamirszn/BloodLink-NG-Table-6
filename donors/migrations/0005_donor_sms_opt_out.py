from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("donors", "0004_phone_verification")]

    operations = [
        migrations.AddField(
            model_name="donor",
            name="sms_opt_out",
            field=models.BooleanField(default=False),
        ),
    ]
