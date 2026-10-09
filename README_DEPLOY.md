# Deploying BloodLink NG

1. Provision PostgreSQL and Redis. Set the variables in `.env.example`.
2. `docker build -t bloodlink . && docker run --env-file .env -p 8000:8000 bloodlink`
   (or use the `Procfile` on a Heroku-style host).
3. Run `python manage.py migrate` (the Procfile `release` step does this).
4. Put TLS in front (the app redirects to HTTPS and sets HSTS). Set
   `TRUSTED_PROXY_COUNT` to the number of proxies so per-IP throttles work.
5. Create a staff user: `python manage.py createsuperuser` (username is a phone number).
6. See `docs/BACKUP.md` for backups.

Local development: `DEBUG=1 python manage.py migrate && DEBUG=1 python manage.py runserver`.
Tests: `python manage.py test`. CI also runs `check --deploy`.

## SMS delivery (F3)

* Choose a provider (Termii or Africa's Talking), register your sender ID, and
  confirm DND-route rules and pricing with them first; run one real test send to
  a staff phone on each network before inviting donors.
* Production runs a worker next to the web process: `celery -A config worker`
  (see `Procfile`) with `NOTIFICATION_QUEUE=celery`.
* Register `https://<host>/requests/webhooks/sms/receipt/?token=<SMS_WEBHOOK_SECRET>`
  for delivery reports and `.../webhooks/sms/inbound/?token=...` for STOP replies.
  Check each provider's payload against `blood_requests/webhooks.py`; field names
  are matched loosely but not guaranteed.
* WhatsApp is deliberately not included yet: it needs approved message templates
  and should be added behind the same `NotificationBackend` interface once SMS is stable.

## Daily jobs (F4, F5)

Schedule both once a day (cron, your platform's scheduler, or Celery beat). Both
are safe to run twice.

    python manage.py expire_requests              # 24h warnings, then expire lapsed requests
    python manage.py send_eligibility_reminders   # "you can donate again" texts, once per donation

Defaults live in settings and can be set by environment variable:
`REQUEST_EXPIRY_DAYS_CRITICAL/URGENT/ROUTINE` (3/7/14), `REMINDER_CATCHUP_DAYS` (7),
`MANAGE_TOKEN_LINK_ENABLED` (true: anyone logged in who holds a manage link can use it;
false: owned requests open only for their owner or staff).
