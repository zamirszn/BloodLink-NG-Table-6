try:  # Celery is optional: only needed when NOTIFICATION_QUEUE=celery.
    from .celery import app as celery_app

    __all__ = ("celery_app",)
except ImportError:  # pragma: no cover
    pass
