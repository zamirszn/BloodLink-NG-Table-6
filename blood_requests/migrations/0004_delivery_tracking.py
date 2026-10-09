from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("blood_requests", "0003_requester_phone_verification")]

    operations = [
        migrations.AddField(
            model_name="donoralert",
            name="claimed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="donoralert",
            name="send_attempts",
            field=models.PositiveSmallIntegerField(default=0),
        ),
        migrations.AlterField(
            model_name="donoralert",
            name="delivery_status",
            field=models.CharField(
                choices=[("pending", "Pending"), ("sending", "Sending"), ("sent", "Sent"), ("delivered", "Delivered"), ("failed", "Failed")],
                default="pending",
                max_length=12,
            ),
        ),
        migrations.AddField(
            model_name="notificationmessage",
            name="provider_message_id",
            field=models.CharField(blank=True, db_index=True, max_length=100),
        ),
        migrations.AlterField(
            model_name="notificationmessage",
            name="status",
            field=models.CharField(
                choices=[("sent", "Sent"), ("delivered", "Delivered"), ("failed", "Failed")],
                max_length=12,
            ),
        ),
    ]
