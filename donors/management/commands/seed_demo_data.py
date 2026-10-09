"""Add repeatable, clearly labelled demo donors and blood requests.

Run: python manage.py seed_demo_data
Remove only these demo records: python manage.py seed_demo_data --clear-demo
"""
from datetime import date, timedelta
import secrets

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from blood_requests.models import BloodRequest
from donors.models import Donor

DEMO_PREFIX = "[DEMO]"
AREAS = [
    "Ikeja, Lagos", "Yaba, Lagos", "Surulere, Lagos", "Lekki, Lagos",
    "Agege, Lagos", "Ikorodu, Lagos", "Festac, Lagos", "Ojota, Lagos",
    "Otukpo, Benue", "Makurdi, Benue", "Wuse, Abuja", "Garki, Abuja",
]
FIRST_NAMES = ["Ada", "Tunde", "Chiamaka", "Ibrahim", "Zainab", "Emeka", "Amina", "Kelechi", "Musa", "Ngozi", "Bola", "Seyi", "Fatima", "David", "Ifunanya", "Chidi", "Maryam", "Oluwaseun", "Daniel", "Amaka"]
LAST_NAMES = ["Okafor", "Bello", "Adeyemi", "Eze", "Abubakar", "Nwosu", "Balogun", "Usman", "Okonkwo", "Ibrahim", "Lawal", "Ojo", "Uche", "Yusuf", "Afolabi", "Onyeka", "Sani", "James", "Obi", "Musa"]
BLOOD_TYPES = ["A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-"]
GENOTYPES = ["AA", "AS", "AC", "SS", "SC"]

class Command(BaseCommand):
    help = "Create repeatable demo donor profiles and blood requests for local testing."

    def add_arguments(self, parser):
        parser.add_argument("--clear-demo", action="store_true", help="Delete only records created by this seed command.")

    @transaction.atomic
    def handle(self, *args, **options):
        demo_requests = BloodRequest.objects.filter(notes__startswith=DEMO_PREFIX)
        demo_donors = Donor.objects.filter(name__startswith=DEMO_PREFIX)
        if options["clear_demo"]:
            request_count, donor_count = demo_requests.count(), demo_donors.count()
            demo_requests.delete()
            demo_donors.delete()
            self.stdout.write(self.style.SUCCESS(f"Removed {request_count} demo requests and {donor_count} demo donors."))
            return

        today = timezone.localdate()
        donor_created = 0
        for i in range(48):
            name = f"{DEMO_PREFIX} {FIRST_NAMES[i % len(FIRST_NAMES)]} {LAST_NAMES[(i * 7) % len(LAST_NAMES)]} {i + 1:02d}"
            phone = f"0809{7000000 + i:07d}"
            defaults = {
                "blood_type": BLOOD_TYPES[(i * 3 + 2) % len(BLOOD_TYPES)],
                "genotype": GENOTYPES[i % len(GENOTYPES)],
                "location": AREAS[i % len(AREAS)],
                "last_donation": None if i % 4 == 0 else today - timedelta(days=100 + (i * 11) % 700),
                "availability": i % 7 != 0,
                "user": None,
                # Demo donors are pre-verified so they appear in search.
                "phone_verified_at": timezone.now(),
            }
            _, created = Donor.objects.update_or_create(phone=phone, defaults={"name": name, **defaults})
            donor_created += int(created)

        request_created = 0
        request_data = [
            ("O+", "Ikeja, Lagos", "critical", 3, "Lagos University Teaching Hospital"),
            ("A+", "Yaba, Lagos", "urgent", 2, "Randle General Hospital"),
            ("B+", "Surulere, Lagos", "routine", 1, "Massey Street Children's Hospital"),
            ("O-", "Lekki, Lagos", "critical", 2, "Lekki Specialist Hospital"),
            ("AB+", "Agege, Lagos", "urgent", 4, "Agege Central Hospital"),
            ("A-", "Ikorodu, Lagos", "urgent", 2, "Ikorodu General Hospital"),
            ("B-", "Festac, Lagos", "routine", 1, "Amuwo Odofin Medical Centre"),
            ("O+", "Ojota, Lagos", "critical", 5, "Gbagada General Hospital"),
            ("AB-", "Otukpo, Benue", "urgent", 2, "Otukpo General Hospital"),
            ("A+", "Makurdi, Benue", "routine", 1, "Benue State University Teaching Hospital"),
            ("B+", "Wuse, Abuja", "urgent", 3, "Wuse District Hospital"),
            ("O-", "Garki, Abuja", "critical", 2, "National Hospital Abuja"),
            ("O+", "Ikeja, Lagos", "urgent", 1, "Ikeja Medical Centre"),
            ("A+", "Yaba, Lagos", "critical", 2, "Lagos Mainland Hospital"),
            ("B-", "Surulere, Lagos", "routine", 1, "Masha Medical Centre"),
            ("AB+", "Lekki, Lagos", "urgent", 2, "Victoria Island Clinic"),
            ("A-", "Otukpo, Benue", "critical", 3, "St. Mary's Hospital Otukpo"),
            ("O+", "Makurdi, Benue", "routine", 2, "Federal Medical Centre Makurdi"),
        ]
        for i, (blood_type, location, urgency, units, hospital) in enumerate(request_data, start=1):
            _, created = BloodRequest.objects.get_or_create(
                notes=f"{DEMO_PREFIX} Seed request {i:02d}",
                defaults={
                    "requester_name": f"{DEMO_PREFIX} Test Requester {i:02d}",
                    "requester_phone": f"0809{8000000 + i:07d}",
                    "hospital": hospital,
                    "hospital_verified": i % 3 == 0,
                    "recipient_blood_type": blood_type,
                    "location": location,
                    "urgency": urgency,
                    "units_needed": units,
                    "status": "open" if i % 6 else "fulfilled",
                    "status_token": secrets.token_urlsafe(32),
                    "requester_phone_verified_at": timezone.now(),
                },
            )
            request_created += int(created)

        self.stdout.write(self.style.SUCCESS(
            f"Demo data ready. {Donor.objects.filter(name__startswith=DEMO_PREFIX).count()} demo donors; "
            f"{BloodRequest.objects.filter(notes__startswith=DEMO_PREFIX).count()} demo requests. "
            f"Newly created this run: {donor_created} donors, {request_created} requests."
        ))
        self.stdout.write("All demo records are labelled [DEMO]. Donors are not login accounts; no real messages are sent by seeding.")
