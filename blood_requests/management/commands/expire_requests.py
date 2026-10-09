from django.core.management.base import BaseCommand

from blood_requests.lifecycle import expire_requests, send_expiry_reminders


class Command(BaseCommand):
    help = "Expire lapsed requests and text 24-hour warnings. Run daily; safe to repeat."

    def handle(self, *args, **options):
        reminded = send_expiry_reminders()
        expired = expire_requests()
        self.stdout.write(f"Reminders sent: {reminded}. Requests expired: {expired}.")
