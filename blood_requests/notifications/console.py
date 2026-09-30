"""The default backend: log the message, send nothing.

This is what makes the whole request-and-contact flow exercisable offline with
no credentials and no chance of a real message reaching a real handset. To
read a donor's personal reply link during a demo, look for the ``OUTBOX`` line
in the server log (or the NotificationMessage row in the admin).
"""

import logging

from .base import DeliveryResult, NotificationBackend

logger = logging.getLogger("blood_requests.outbox")


class ConsoleBackend(NotificationBackend):
    """Writes each message to the log instead of transmitting it."""

    name = "console"

    def send(self, message):
        logger.info(
            "OUTBOX [%s] -> %s: %s",
            message.channel,
            message.recipient_phone,
            message.body,
        )

        return DeliveryResult(
            status="sent",
            detail="Recorded in the local outbox. No message was transmitted.",
        )
