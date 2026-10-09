"""Provider callbacks: delivery receipts and inbound STOP replies.

Authenticated by a shared secret in the URL (``?token=``) because providers
differ in how (or whether) they sign callbacks. Set SMS_WEBHOOK_SECRET to a long
random value and register the full URL, with the token, in the provider
dashboard. With no secret configured both endpoints answer 403.
"""

import hmac
import json
import logging

from django.conf import settings
from django.http import HttpResponse, HttpResponseForbidden
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from donors.models import Donor
from donors.phone import normalize_ng_phone

from .delivery import apply_receipt

logger = logging.getLogger(__name__)

STOP_WORDS = {"STOP", "STOPALL", "UNSUBSCRIBE", "CANCEL", "END", "QUIT"}


def _authorised(request):
    secret = settings.SMS_WEBHOOK_SECRET
    supplied = request.GET.get("token", "")

    return bool(secret) and hmac.compare_digest(secret, supplied)


def _payload(request):
    """JSON or form-encoded body as a flat dict."""
    if "json" in (request.content_type or ""):
        try:
            data = json.loads(request.body or b"{}")
        except ValueError:
            return {}

        return data if isinstance(data, dict) else {}

    return request.POST.dict()


def _first(data, *keys):
    for key in keys:
        if data.get(key):
            return str(data[key])

    return ""


@csrf_exempt
@require_POST
def delivery_receipt(request):
    if not _authorised(request):
        return HttpResponseForbidden()

    data = _payload(request)
    message_id = _first(data, "message_id", "messageId", "id")
    status = _first(data, "status", "deliveryStatus")
    detail = _first(data, "failureReason", "reason", "message")

    apply_receipt(message_id, status, detail)

    # Always 200 so the provider does not retry receipts we cannot match.
    return HttpResponse("ok")


@csrf_exempt
@require_POST
def inbound_sms(request):
    if not _authorised(request):
        return HttpResponseForbidden()

    data = _payload(request)
    sender = normalize_ng_phone(_first(data, "from", "sender", "phoneNumber", "msisdn"))
    text = _first(data, "text", "message", "sms", "body").strip().upper()

    if sender and text in STOP_WORDS:
        stopped = Donor.objects.filter(phone=sender).update(sms_opt_out=True, availability=False)
        logger.info("STOP received; donors opted out=%s", stopped)

    return HttpResponse("ok")
