# BloodLink-NG -Table-6


# BloodLink NG

## Project Objective
BloodLink NG solves the problem of slow, unreliable blood donor matching in Nigeria, where patients and hospitals currently rely on informal social media or word-of-mouth networks during emergencies. The platform lets donors register their blood type and availability, and enables patients/hospitals to instantly search and connect with nearby eligible donors via SMS/WhatsApp — saving critical time when it matters most.

## Key Features and Functionality

1. **Donor Registration** — Sign up with blood type, genotype, location, phone number, and an availability toggle.
2. **Location-Based Search** — Find nearby compatible donors sorted by distance, filtered by availability and eligibility (90-day donation gap).
3. **Request & Notification System** — Submit urgent blood requests; nearby donors are alerted via SMS/WhatsApp and can respond instantly.
4. **Eligibility Tracking** — Auto-tracks last donation date to ensure donors are only matched when medically eligible to donate again.
5. **Verification & Trust Layer** — Hospital verification badges, spam rate-limiting on requests, and donor donation history for credibility.

## Tech Stack

- **Backend:** Django + Django REST Framework
- **Database:** PostgreSQL (with PostGIS for geo-based search)
- **Task Queue:** Celery + Redis (for async SMS/WhatsApp notifications)
- **Notifications:** Termii / Africa's Talking / Twilio (SMS & WhatsApp)
- **Frontend:** Django templates + HTMX (or a separate mobile app consuming the REST API)

## Project Structure (Planned)

```
bloodlink_ng/
├── donors/          # Donor registration, profiles, availability
├── requests/        # Blood requests, matching logic
├── notifications/   # SMS/WhatsApp alert handling (Celery tasks)
├── verification/    # Hospital verification, trust/reputation logic
├── core/            # Shared utilities, settings
└── manage.py
```

## Build Order

1. Donor Registration
2. Location-Based Search & Matching
3. Request & Notification System
4. Verification & Trust Layer

## Getting Started

```bash

## step 1
# Create virtual environment
python -m venv venv
source venv/bin/activate

## step 2
# Install dependencies
pip install -r requirements.txt

## step 3
# Run migrations
python manage.py migrate

## step 4
# Start development server
python manage.py runserver
```


## pip freeze command
pip freeze > requirements.txt

## Environment Variables

```
DATABASE_URL=
SMS_API_KEY=
SMS_API_SECRET=
SECRET_KEY=
DEBUG=True
```

## Status

🚧 In development — MVP phase.

## License

MIT


