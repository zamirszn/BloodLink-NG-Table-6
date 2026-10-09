"""BloodLink NG settings loaded from the project-root .env file."""

import os
import sys
from importlib.util import find_spec
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlparse

from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

# Load local configuration from <project-root>/.env. Existing shell variables
# are not allowed to override values in .env. Keep .env out of version control.
load_dotenv(BASE_DIR / ".env", override=True)


def env_bool(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_list(name, default=""):
    return [item.strip() for item in os.environ.get(name, default).split(",") if item.strip()]


def env_int(name, default):
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


DEBUG = env_bool("DEBUG", False)
TESTING = len(sys.argv) > 1 and sys.argv[1] == "test"
PRODUCTION = not DEBUG and not TESTING

SECRET_KEY = os.environ.get("SECRET_KEY", "")
if not SECRET_KEY:
    if PRODUCTION:
        raise ImproperlyConfigured("SECRET_KEY must be set when DEBUG is off.")
    SECRET_KEY = "dev-only-" + "x" * 60

ALLOWED_HOSTS = env_list(
    "ALLOWED_HOSTS", "localhost,127.0.0.1,testserver" if not PRODUCTION else ""
)
CSRF_TRUSTED_ORIGINS = env_list("CSRF_TRUSTED_ORIGINS")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "donors",
    "blood_requests",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
if find_spec("whitenoise"):
    MIDDLEWARE.insert(1, "whitenoise.middleware.WhiteNoiseMiddleware")

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"


def _database_from_url(url):
    parsed = urlparse(url)
    scheme = parsed.scheme.split("+")[0]
    if scheme in {"sqlite", "sqlite3"}:
        path = unquote(parsed.path)
        name = ":memory:" if path in {"", "/:memory:"} else path[1:] if path.startswith("//") else str(BASE_DIR / path.lstrip("/"))
        return {"ENGINE": "django.db.backends.sqlite3", "NAME": name}
    if scheme in {"postgres", "postgresql"}:
        config = {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": unquote(parsed.path.lstrip("/")),
            "USER": unquote(parsed.username or ""),
            "PASSWORD": unquote(parsed.password or ""),
            "HOST": parsed.hostname or "",
            "PORT": str(parsed.port or ""),
            "CONN_MAX_AGE": env_int("DB_CONN_MAX_AGE", 60),
            "CONN_HEALTH_CHECKS": True,
        }
        options = dict(parse_qsl(parsed.query))
        if options:
            config["OPTIONS"] = options
        return config
    raise ImproperlyConfigured(f"Unsupported DATABASE_URL scheme: {parsed.scheme!r}")


_database_url = os.environ.get("DATABASE_URL", "")
if _database_url:
    DATABASES = {"default": _database_from_url(_database_url)}
elif PRODUCTION:
    raise ImproperlyConfigured("DATABASE_URL must be set when DEBUG is off.")
else:
    DATABASES = {
        "default": {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "db.sqlite3"}
    }

# Shared cache. Rate limits and OTP throttles live here, so production must use
# Redis (or another shared cache) or each worker would keep its own counters.
_cache_url = os.environ.get("CACHE_URL", "")
if _cache_url:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": _cache_url,
        }
    }
elif PRODUCTION and not env_bool("ALLOW_LOCMEM_CACHE"):
    raise ImproperlyConfigured(
        "CACHE_URL (e.g. redis://localhost:6379/1) must be set in production so "
        "rate limits are shared across workers. Set ALLOW_LOCMEM_CACHE=1 to override."
    )
else:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "bloodlink-ng",
        }
    }

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = os.environ.get("TIME_ZONE", "Africa/Lagos")
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
if PRODUCTION and find_spec("whitenoise"):
    STORAGES = {
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
    }

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
LOGIN_URL = "login"

# --- Security ---------------------------------------------------------------
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = True
SECURE_REFERRER_POLICY = "same-origin"
if PRODUCTION:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_SSL_REDIRECT = env_bool("SECURE_SSL_REDIRECT", True)
    SECURE_HSTS_SECONDS = env_int("SECURE_HSTS_SECONDS", 31536000)
    SECURE_HSTS_INCLUDE_SUBDOMAINS = env_bool("SECURE_HSTS_INCLUDE_SUBDOMAINS", True)
    SECURE_HSTS_PRELOAD = env_bool("SECURE_HSTS_PRELOAD", True)
    if env_bool("TRUST_PROXY_SSL_HEADER", True):
        # Set to 0 only if the app is exposed directly, never behind a proxy.
        SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# Behind a reverse proxy REMOTE_ADDR is the proxy. Set this to the number of
# trusted proxies in front of the app so per-IP throttles see real clients.
TRUSTED_PROXY_COUNT = env_int("TRUSTED_PROXY_COUNT", 0)

# --- Phone verification and one-time codes (F1, F2) -------------------------
OTP_LENGTH = 6
OTP_TTL_SECONDS = 10 * 60
OTP_MAX_ATTEMPTS = 5
OTP_RESEND_COOLDOWN_SECONDS = 60
OTP_MAX_SENDS_PER_PHONE_PER_HOUR = env_int("OTP_MAX_SENDS_PER_PHONE_PER_HOUR", 5)
OTP_MAX_SENDS_PER_IP_PER_HOUR = env_int("OTP_MAX_SENDS_PER_IP_PER_HOUR", 20)

# Donors who registered before verification existed stay searchable until this
# date (YYYY-MM-DD). Empty means the grace period has no end yet.
PHONE_VERIFICATION_GRACE_ENDS = os.environ.get("PHONE_VERIFICATION_GRACE_ENDS", "")

# --- Notifications ------------------------------------------------------------
NOTIFICATION_BACKEND = os.environ.get(
    "NOTIFICATION_BACKEND", "blood_requests.notifications.ConsoleBackend"
)

# F3: how alerts are sent. "sync" sends inside the request (development and
# tests); "celery" enqueues one task per alert for a worker (production).
NOTIFICATION_QUEUE = os.environ.get("NOTIFICATION_QUEUE", "sync")
NOTIFICATION_MAX_ATTEMPTS = env_int("NOTIFICATION_MAX_ATTEMPTS", 5)

# Absolute site address, used for links in messages sent outside a request
# (reminders, wave dispatch). Example: https://bloodlink.example.ng
SITE_BASE_URL = os.environ.get("SITE_BASE_URL", "http://localhost:8000")

CELERY_BROKER_URL = os.environ.get("CELERY_BROKER_URL", os.environ.get("CACHE_URL", "redis://localhost:6379/0"))
CELERY_TASK_ACKS_LATE = True
CELERY_TASK_REJECT_ON_WORKER_LOST = True
CELERY_BROKER_TRANSPORT_OPTIONS = {"visibility_timeout": 3600}

# Termii (set NOTIFICATION_BACKEND=blood_requests.notifications.TermiiBackend).
TERMII_API_KEY = os.environ.get("TERMII_API_KEY", "")
TERMII_SENDER_ID = os.environ.get("TERMII_SENDER_ID", "")
TERMII_BASE_URL = os.environ.get("TERMII_BASE_URL", "https://api.ng.termii.com")
TERMII_CHANNEL = os.environ.get("TERMII_CHANNEL", "dnd")

# Africa's Talking (blood_requests.notifications.AfricasTalkingBackend).
AT_USERNAME = os.environ.get("AT_USERNAME", "")
AT_API_KEY = os.environ.get("AT_API_KEY", "")
AT_SENDER_ID = os.environ.get("AT_SENDER_ID", "")
AT_BASE_URL = os.environ.get("AT_BASE_URL", "https://api.africastalking.com")

# Shared secret in the webhook URL (?token=...). Empty disables the webhooks.
SMS_WEBHOOK_SECRET = os.environ.get("SMS_WEBHOOK_SECRET", "")

# --- Request lifecycle (F5) and donation reminders (F4) -----------------------
REQUEST_EXPIRY_DAYS = {
    "critical": env_int("REQUEST_EXPIRY_DAYS_CRITICAL", 3),
    "urgent": env_int("REQUEST_EXPIRY_DAYS_URGENT", 7),
    "routine": env_int("REQUEST_EXPIRY_DAYS_ROUTINE", 14),
}
# A donor is reminded only if their wait ended within this many days, so the
# first run after deploying does not message everyone who ever donated.
REMINDER_CATCHUP_DAYS = env_int("REMINDER_CATCHUP_DAYS", 7)

# Open decision (plan section 8): may anyone holding a manage link use it?
# True: the link works for any logged-in user who has it (the original model).
# False: owned requests open only for their owner or staff.
MANAGE_TOKEN_LINK_ENABLED = env_bool("MANAGE_TOKEN_LINK_ENABLED", True)

# --- Logging and error tracking ----------------------------------------------
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "json": {"()": "config.logjson.JsonFormatter"},
        "plain": {"format": "%(asctime)s %(levelname)s %(name)s: %(message)s"},
    },
    "handlers": {
        "stdout": {
            "class": "logging.StreamHandler",
            "stream": "ext://sys.stdout",
            "formatter": "json" if PRODUCTION else "plain",
        },
    },
    "root": {"handlers": ["stdout"], "level": os.environ.get("LOG_LEVEL", "INFO")},
    "loggers": {
        "django.server": {"handlers": ["stdout"], "level": "INFO", "propagate": False},
        "blood_requests.outbox": {"handlers": ["stdout"], "level": "INFO", "propagate": False},
    },
}

SENTRY_DSN = os.environ.get("SENTRY_DSN", "")
if SENTRY_DSN and find_spec("sentry_sdk"):
    import sentry_sdk

    sentry_sdk.init(
        dsn=SENTRY_DSN,
        send_default_pii=False,
        traces_sample_rate=float(os.environ.get("SENTRY_TRACES_SAMPLE_RATE", "0")),
        environment=os.environ.get("SENTRY_ENVIRONMENT", "production" if PRODUCTION else "development"),
    )