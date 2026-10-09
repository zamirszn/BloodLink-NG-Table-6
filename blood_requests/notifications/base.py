"""The seam a real SMS/WhatsApp provider plugs into.

A backend takes one already-rendered message and reports what happened. It
must not import or write Django models: the caller records the outbox row, so
every backend gets an audit trail for free and adding a provider is a single
self-contained file plus one settings string.

Nothing in this build makes a network call. See ``console.py``.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class OutgoingMessage:
    """One message, ready to hand to a provider."""

    recipient_phone: str
    body: str
    channel: str = "sms"


@dataclass(frozen=True)
class DeliveryResult:
    """What the provider reported back."""

    #: One of NotificationMessage.Status, kept as a plain string so backends
    #: stay free of model imports.
    status: str

    detail: str = ""

    #: The provider's own id for the message, used to match delivery receipts.
    provider_message_id: str = ""

    #: True when a failure is worth retrying (timeout, 5xx, rate limit); False
    #: when retrying cannot help (bad number, bad credentials, rejected).
    retryable: bool = False


class NotificationBackend(ABC):
    """Base class for delivery backends."""

    #: Short identifier stored on each outbox row.
    name = "base"

    @abstractmethod
    def send(self, message):
        """Deliver ``message`` and return a :class:`DeliveryResult`.

        A provider that cannot deliver should return a failed result rather
        than raise, so one unreachable handset cannot abort the rest of a
        dispatch. The caller also guards against exceptions, but reporting
        failure normally keeps the outbox row informative.
        """
        raise NotImplementedError
