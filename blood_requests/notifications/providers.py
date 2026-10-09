"""SMS providers: Termii and Africa's Talking.

Credentials, sender ID and endpoints come from environment variables (see
settings). Both classes follow the provider APIs as documented at the time of
writing; **confirm the endpoint, field names, sender-ID registration and
Do-Not-Disturb rules in the provider's current documentation and with a test
send before go-live.** Neither is called by the test suite.
"""

import logging
import re

from django.conf import settings

from . import http
from .base import DeliveryResult, NotificationBackend

logger = logging.getLogger(__name__)


def international(phone):
    """08031234567 -> 2348031234567 (digits only)."""
    digits = re.sub(r"\D", "", phone or "")
    return "234" + digits[1:] if digits.startswith("0") else digits


def _failure(status_code, detail):
    # 408/429/5xx can succeed later; other 4xx will not.
    retryable = status_code in (408, 429) or status_code >= 500
    return DeliveryResult(status="failed", detail=detail[:500], retryable=retryable)


class TermiiBackend(NotificationBackend):
    name = "termii"

    def send(self, message):
        payload = {
            "to": international(message.recipient_phone),
            "from": settings.TERMII_SENDER_ID,
            "sms": message.body,
            "type": "plain",
            # "dnd" is the transactional route that reaches Do-Not-Disturb numbers.
            "channel": settings.TERMII_CHANNEL,
            "api_key": settings.TERMII_API_KEY,
        }

        try:
            result = http.post(settings.TERMII_BASE_URL.rstrip("/") + "/api/sms/send", json_body=payload)
        except OSError as error:
            return DeliveryResult(status="failed", detail=f"Network error: {error}", retryable=True)

        data = result.json()
        message_id = str(data.get("message_id") or "")

        if 200 <= result.status < 300 and message_id:
            return DeliveryResult(
                status="sent",
                detail=str(data.get("message", "Accepted by Termii")),
                provider_message_id=message_id,
            )

        return _failure(result.status, str(data.get("message") or result.body))


class AfricasTalkingBackend(NotificationBackend):
    name = "africastalking"

    def send(self, message):
        form = {
            "username": settings.AT_USERNAME,
            "to": "+" + international(message.recipient_phone),
            "message": message.body,
        }

        if settings.AT_SENDER_ID:
            form["from"] = settings.AT_SENDER_ID

        try:
            result = http.post(
                settings.AT_BASE_URL.rstrip("/") + "/version1/messaging",
                form=form,
                headers={"apiKey": settings.AT_API_KEY, "Accept": "application/json"},
            )
        except OSError as error:
            return DeliveryResult(status="failed", detail=f"Network error: {error}", retryable=True)

        data = result.json()
        recipients = (data.get("SMSMessageData") or {}).get("Recipients") or []

        if 200 <= result.status < 300 and recipients:
            first = recipients[0]

            # 100 Processed, 101 Sent, 102 Queued.
            if first.get("statusCode") in (100, 101, 102):
                return DeliveryResult(
                    status="sent",
                    detail=str(first.get("status", "Accepted by Africa's Talking")),
                    provider_message_id=str(first.get("messageId", "")),
                )

            return DeliveryResult(status="failed", detail=str(first.get("status", "Rejected")), retryable=False)

        return _failure(result.status, result.body)
