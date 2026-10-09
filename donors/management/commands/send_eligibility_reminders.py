from django.core.management.base import BaseCommand

from donors.reminders import send_eligibility_reminders


class Command(BaseCommand):
    help = "Text donors whose waiting period ended. Run daily; each donor is reminded once."

    def handle(self, *args, **options):
        self.stdout.write(f"Reminders sent: {send_eligibility_reminders()}.")
