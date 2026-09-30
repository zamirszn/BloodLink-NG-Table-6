"""Notification backends.

``ConsoleBackend`` is re-exported here so the settings string stays short
(``blood_requests.notifications.ConsoleBackend``) and this module doubles as
the list of backends that ship with the project. A provider you add yourself
is referenced by its own full dotted path and needs no change here.
"""

from django.conf import settings
from django.utils.module_loading import import_string

from .base import DeliveryResult, NotificationBackend, OutgoingMessage
from .console import ConsoleBackend

DEFAULT_BACKEND = "blood_requests.notifications.ConsoleBackend"

__all__ = [
    "ConsoleBackend",
    "DeliveryResult",
    "NotificationBackend",
    "OutgoingMessage",
    "get_backend",
]


def get_backend():
    """Build the configured notification backend.

    Resolved on every call rather than cached at import time, so tests can
    swap the backend with ``override_settings`` and a running process can be
    repointed without a code change.
    """
    dotted_path = getattr(settings, "NOTIFICATION_BACKEND", DEFAULT_BACKEND)

    return import_string(dotted_path)()
