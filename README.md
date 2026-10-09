# BloodLink NG

BloodLink NG is a Django MVP for connecting blood requesters with compatible, eligible donors in Nigeria. It currently uses Django templates and SQLite.

## Implemented features

- Donor registration with password validation, login/logout, donor profile editing, phone normalization, blood type, genotype, last donation date, and availability.
- Donor search by recipient blood type, state/LGA location, and availability. Compatibility is centralized and eligibility requires at least 90 days since the last donation.
- Blood request creation, urgency-ordered request board, request details, token-protected request management, and status changes.
- Matching donor alerts, a My alerts page (alerts sent to the donor plus open requests that match them), individual token-based replies, duplicate-alert protection, alert expiry, and a notification outbox. The default notification backend logs messages locally; it does **not** send real SMS/WhatsApp.
- Admin management of donors, requests, alerts, and notification logs. Staff can mark a hospital request as verified after independently checking it. Verification is a staff-controlled badge, not an automatic identity check.
- Basic request spam throttling by IP and phone using Django's local-memory cache. For multiple app workers, configure a shared cache (for example Redis).
- Responsive templates, keyboard focus styles, reduced-motion support, and mobile navigation refinements.

## Current limitations

- Locations are picked from state and LGA dropdowns (all 36 states plus the FCT, 774 LGAs, in `donors/locations.py`) and stored as text like `Ikeja, Lagos`. Matching is a case-insensitive text match, not distance/radius matching, so a request for a whole state matches every donor in it. No coordinates or PostGIS are configured.
- Real SMS/WhatsApp delivery requires a provider backend and credentials. No provider credentials are included.
- The local-memory rate limiter resets when the process restarts and is not shared across workers.
- Hospital verification requires staff review in Django admin; it is not an automated verification service.
- This is an MVP, not a substitute for clinical screening. Donor suitability must be confirmed by qualified medical personnel.

## Requirements

Python 3.12+ and the Django version pinned in `requirements.txt`.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
python -m pip install -r requirements.txt
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

Open `http://127.0.0.1:8000/`. Registration is at `/register/`, donor search at `/search/`, blood requests at `/requests/`, and Django admin at `/admin/`.

## Configuration

Settings may be configured using environment variables:

- `SECRET_KEY`: secret key; set a strong random value outside development.
- `DEBUG`: defaults to `True` for local development. Set `False` in production.
- `ALLOWED_HOSTS`: comma-separated hostnames.
- `TIME_ZONE`: defaults to `Africa/Lagos`.
- `NOTIFICATION_BACKEND`: defaults to `blood_requests.notifications.ConsoleBackend`. A custom backend must implement `blood_requests.notifications.base.NotificationBackend`.
- `CACHE_BACKEND`: optional Django cache backend dotted path; use shared cache for multi-worker deployments.
- `SECURE_SSL_REDIRECT`: defaults to enabled when `DEBUG=False`; disable only if TLS is terminated/configured elsewhere.

SQLite remains the default database. Run `python manage.py makemigrations --check --dry-run` to check model/migration consistency and `python manage.py test` to run tests.

## Safety notes

Donor phone numbers are personal data. Restrict production access, use HTTPS, secure backups, and configure an appropriate privacy/retention policy before launch. The default console notification backend is for development/demo use only.

## License

MIT

## Demo / seed data

A local demo database is included with clearly labelled `[DEMO]` donor profiles and blood requests. These records use fictional names and reserved test phone patterns; they are not login accounts and should never be treated as real donors. Existing records in the included SQLite database are preserved.

To add or refresh demo records in your own local database:

```bash
python manage.py seed_demo_data
```

This command is safe to re-run and does not create duplicate demo requests. To remove only seeded demo records:

```bash
python manage.py seed_demo_data --clear-demo
```

Use the seeded records to test donor search, blood-type compatibility, availability, 90-day eligibility, request listing, urgency/status display, and matching. Notification delivery remains local-only with the default console backend; seeding does not send messages. Do not use this demo database for production.
