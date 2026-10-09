"""Celery tasks. Imported only when NOTIFICATION_QUEUE=celery."""

from celery import shared_task
from django.conf import settings

from .delivery import RETRY, deliver_alert


@shared_task(bind=True, max_retries=settings.NOTIFICATION_MAX_ATTEMPTS)
def send_alert_task(self, alert_id, base_url):
    """Send one alert; retry transient failures with exponential backoff.

    ``deliver_alert`` claims the alert atomically, so a retry, a Celery
    redelivery or a duplicate enqueue can never produce a second message.
    """
    if deliver_alert(alert_id, base_url) == RETRY:
        raise self.retry(countdown=min(30 * 2 ** self.request.retries, 3600))
