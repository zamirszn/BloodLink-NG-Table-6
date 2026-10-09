"""F3: provider backends, idempotent sending, retries, receipts, STOP."""

from unittest import mock

from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from donors.models import Donor

from . import delivery
from .models import BloodRequest, DonorAlert, NotificationMessage
from .notifications import AfricasTalkingBackend, TermiiBackend
from .notifications.base import DeliveryResult, NotificationBackend, OutgoingMessage
from .notifications.http import HttpResult

BASE = "http://testserver/"
SENT = []


class FlakyBackend(NotificationBackend):
    """Fails transiently the first N times, then succeeds."""

    name = "flaky"
    fail_times = 1
    calls = 0

    def send(self, message):
        type(self).calls += 1

        if type(self).calls <= type(self).fail_times:
            return DeliveryResult(status="failed", detail="timeout", retryable=True)

        SENT.append(message)
        return DeliveryResult(status="sent", detail="ok", provider_message_id=f"id-{type(self).calls}")


class PermanentFailBackend(NotificationBackend):
    name = "permanent"

    def send(self, message):
        return DeliveryResult(status="failed", detail="invalid number", retryable=False)


def donor(**kw):
    values = dict(
        name="Ada", blood_type="O-", genotype="AA", location="Makurdi",
        phone="08000000000", phone_verified_at=timezone.now(),
    )
    values.update(kw)
    return Donor.objects.create(**values)


def blood_request(**kw):
    values = dict(
        requester_name="Grace", requester_phone="08011112222", recipient_blood_type="O-",
        location="Makurdi", requester_phone_verified_at=timezone.now(),
    )
    values.update(kw)
    return BloodRequest.objects.create(**values)


def make_alert(**kw):
    return DonorAlert.objects.create(blood_request=blood_request(), donor=donor(), **kw)


@override_settings(NOTIFICATION_BACKEND="blood_requests.test_delivery.FlakyBackend", NOTIFICATION_MAX_ATTEMPTS=3)
class DeliverAlertTests(TestCase):
    def setUp(self):
        SENT.clear()
        FlakyBackend.calls = 0
        FlakyBackend.fail_times = 1

    def test_a_transient_failure_asks_for_a_retry_and_a_retry_succeeds_once(self):
        alert = make_alert()

        self.assertEqual(delivery.deliver_alert(alert.pk, BASE), delivery.RETRY)
        alert.refresh_from_db()
        self.assertEqual(alert.delivery_status, DonorAlert.DeliveryStatus.FAILED)

        self.assertEqual(delivery.deliver_alert(alert.pk, BASE), delivery.DONE)
        alert.refresh_from_db()
        self.assertEqual(alert.delivery_status, DonorAlert.DeliveryStatus.SENT)
        self.assertEqual(len(SENT), 1)
        self.assertEqual(alert.send_attempts, 2)

    def test_a_sent_alert_is_never_sent_again(self):
        FlakyBackend.fail_times = 0
        alert = make_alert()

        delivery.deliver_alert(alert.pk, BASE)
        second = delivery.deliver_alert(alert.pk, BASE)
        third = delivery.deliver_alert(alert.pk, BASE)

        self.assertEqual((second, third), (delivery.SKIPPED, delivery.SKIPPED))
        self.assertEqual(len(SENT), 1)
        self.assertEqual(NotificationMessage.objects.count(), 1)

    def test_an_alert_being_sent_elsewhere_is_skipped(self):
        alert = make_alert(
            delivery_status=DonorAlert.DeliveryStatus.SENDING, claimed_at=timezone.now()
        )

        self.assertEqual(delivery.deliver_alert(alert.pk, BASE), delivery.SKIPPED)
        self.assertEqual(SENT, [])

    def test_a_stale_claim_can_be_retaken(self):
        FlakyBackend.fail_times = 0
        alert = make_alert(
            delivery_status=DonorAlert.DeliveryStatus.SENDING,
            claimed_at=timezone.now() - delivery.STALE_CLAIM - delivery.timedelta(seconds=1),
        )

        self.assertEqual(delivery.deliver_alert(alert.pk, BASE), delivery.DONE)
        self.assertEqual(len(SENT), 1)

    def test_retries_stop_at_the_attempt_limit(self):
        FlakyBackend.fail_times = 99
        alert = make_alert()

        results = [delivery.deliver_alert(alert.pk, BASE) for _ in range(3)]

        self.assertEqual(results, [delivery.RETRY, delivery.RETRY, delivery.DONE])

    def test_every_attempt_is_recorded_in_the_outbox(self):
        alert = make_alert()

        delivery.deliver_alert(alert.pk, BASE)
        delivery.deliver_alert(alert.pk, BASE)

        statuses = list(NotificationMessage.objects.order_by("id").values_list("status", flat=True))
        self.assertEqual(statuses, ["failed", "sent"])

    def test_a_donor_who_opted_out_is_not_texted(self):
        FlakyBackend.fail_times = 0
        alert = make_alert()
        Donor.objects.filter(pk=alert.donor_id).update(sms_opt_out=True)

        self.assertEqual(delivery.deliver_alert(alert.pk, BASE), delivery.DONE)
        self.assertEqual(SENT, [])
        self.assertEqual(NotificationMessage.objects.get().status, "failed")

    def test_a_closed_request_is_not_texted(self):
        FlakyBackend.fail_times = 0
        alert = make_alert()
        BloodRequest.objects.filter(pk=alert.blood_request_id).update(status="fulfilled")

        delivery.deliver_alert(alert.pk, BASE)

        self.assertEqual(SENT, [])

    @override_settings(NOTIFICATION_BACKEND="blood_requests.test_delivery.PermanentFailBackend")
    def test_a_permanent_failure_is_not_retried(self):
        alert = make_alert()

        self.assertEqual(delivery.deliver_alert(alert.pk, BASE), delivery.DONE)
        alert.refresh_from_db()
        self.assertEqual(alert.delivery_status, DonorAlert.DeliveryStatus.FAILED)


class QueueTests(TestCase):
    @override_settings(NOTIFICATION_QUEUE="celery")
    def test_dispatch_returns_without_sending_when_queued(self):
        fake_task = mock.Mock()
        fake_module = mock.Mock(send_alert_task=fake_task)

        req = blood_request()
        donor()

        with mock.patch.dict("sys.modules", {"blood_requests.tasks": fake_module}):
            with self.captureOnCommitCallbacks(execute=True):
                from .matching import notify_matching_donors

                created, _ = notify_matching_donors(req, base_url=BASE)

        self.assertEqual(created, 1)
        fake_task.delay.assert_called_once()
        self.assertEqual(NotificationMessage.objects.count(), 0)
        self.assertEqual(DonorAlert.objects.get().delivery_status, DonorAlert.DeliveryStatus.PENDING)


class ProviderTests(TestCase):
    @override_settings(TERMII_API_KEY="k", TERMII_SENDER_ID="BloodLink", TERMII_BASE_URL="https://t.test", TERMII_CHANNEL="dnd")
    def test_termii_success_builds_the_expected_request(self):
        reply = HttpResult(200, '{"message_id": "m-1", "message": "Successfully Sent"}')

        with mock.patch("blood_requests.notifications.http.post", return_value=reply) as post:
            result = TermiiBackend().send(OutgoingMessage("08031234567", "hello"))

        self.assertEqual((result.status, result.provider_message_id), ("sent", "m-1"))
        url = post.call_args.args[0]
        body = post.call_args.kwargs["json_body"]
        self.assertEqual(url, "https://t.test/api/sms/send")
        self.assertEqual(body["to"], "2348031234567")
        self.assertEqual(body["channel"], "dnd")

    @override_settings(TERMII_BASE_URL="https://t.test")
    def test_termii_server_error_is_retryable_and_bad_request_is_not(self):
        with mock.patch("blood_requests.notifications.http.post", return_value=HttpResult(503, "{}")):
            self.assertTrue(TermiiBackend().send(OutgoingMessage("08031234567", "x")).retryable)

        with mock.patch("blood_requests.notifications.http.post", return_value=HttpResult(400, '{"message": "bad"}')):
            self.assertFalse(TermiiBackend().send(OutgoingMessage("08031234567", "x")).retryable)

    @override_settings(TERMII_BASE_URL="https://t.test")
    def test_a_network_error_is_a_retryable_failure_not_an_exception(self):
        with mock.patch("blood_requests.notifications.http.post", side_effect=OSError("down")):
            result = TermiiBackend().send(OutgoingMessage("08031234567", "x"))

        self.assertEqual((result.status, result.retryable), ("failed", True))

    @override_settings(AT_USERNAME="u", AT_API_KEY="k", AT_BASE_URL="https://at.test", AT_SENDER_ID="")
    def test_africas_talking_success_and_rejection(self):
        ok = HttpResult(201, '{"SMSMessageData": {"Recipients": [{"statusCode": 101, "status": "Success", "messageId": "ATXid_1"}]}}')
        bad = HttpResult(201, '{"SMSMessageData": {"Recipients": [{"statusCode": 403, "status": "InvalidPhoneNumber"}]}}')

        with mock.patch("blood_requests.notifications.http.post", return_value=ok) as post:
            result = AfricasTalkingBackend().send(OutgoingMessage("08031234567", "hi"))

        self.assertEqual((result.status, result.provider_message_id), ("sent", "ATXid_1"))
        self.assertEqual(post.call_args.kwargs["form"]["to"], "+2348031234567")

        with mock.patch("blood_requests.notifications.http.post", return_value=bad):
            result = AfricasTalkingBackend().send(OutgoingMessage("08031234567", "hi"))

        self.assertEqual((result.status, result.retryable), ("failed", False))


@override_settings(SMS_WEBHOOK_SECRET="s3cret")
class WebhookTests(TestCase):
    def setUp(self):
        self.client = Client(enforce_csrf_checks=True)
        self.alert = make_alert(delivery_status=DonorAlert.DeliveryStatus.SENT)
        self.message = NotificationMessage.objects.create(
            alert=self.alert, recipient_phone="08000000000", channel="sms", body="b",
            backend="termii", status="sent", provider_message_id="m-1",
        )

    def _receipt(self, token="s3cret", **data):
        url = reverse("blood_requests:sms_receipt") + (f"?token={token}" if token else "")
        return self.client.post(url, data)

    def test_a_delivered_receipt_updates_message_and_alert(self):
        self.assertEqual(self._receipt(message_id="m-1", status="DELIVERED").status_code, 200)

        self.message.refresh_from_db()
        self.alert.refresh_from_db()
        self.assertEqual(self.message.status, "delivered")
        self.assertEqual(self.alert.delivery_status, "delivered")

    def test_a_failed_receipt_records_the_detail(self):
        self._receipt(message_id="m-1", status="Failed", failureReason="AbsentSubscriber")

        self.message.refresh_from_db()
        self.alert.refresh_from_db()
        self.assertEqual((self.message.status, self.alert.delivery_status), ("failed", "failed"))
        self.assertIn("AbsentSubscriber", self.message.detail)

    def test_in_flight_statuses_change_nothing(self):
        self._receipt(message_id="m-1", status="Buffered")

        self.message.refresh_from_db()
        self.assertEqual(self.message.status, "sent")

    def test_a_late_failure_does_not_undo_a_delivery(self):
        self._receipt(message_id="m-1", status="Delivered")
        self._receipt(message_id="m-1", status="Failed")

        self.alert.refresh_from_db()
        self.assertEqual(self.alert.delivery_status, "delivered")

    def test_wrong_or_missing_token_is_forbidden(self):
        self.assertEqual(self._receipt(token="nope", message_id="m-1", status="Delivered").status_code, 403)
        self.assertEqual(self._receipt(token="", message_id="m-1", status="Delivered").status_code, 403)

    @override_settings(SMS_WEBHOOK_SECRET="")
    def test_webhooks_are_off_without_a_secret(self):
        self.assertEqual(self._receipt(token="", message_id="m-1", status="Delivered").status_code, 403)

    def test_an_unknown_message_id_is_acknowledged_and_ignored(self):
        self.assertEqual(self._receipt(message_id="zzz", status="Delivered").status_code, 200)

    def test_stop_opts_the_donor_out_and_excludes_them_from_matching(self):
        from .matching import eligible_donors

        response = self.client.post(
            reverse("blood_requests:sms_inbound") + "?token=s3cret",
            {"from": "+2348000000000", "text": " stop "},
        )

        self.assertEqual(response.status_code, 200)
        d = Donor.objects.get(pk=self.alert.donor_id)
        self.assertTrue(d.sms_opt_out)
        self.assertFalse(d.availability)
        self.assertNotIn(d, eligible_donors("O-"))

    def test_other_words_do_not_opt_out(self):
        self.client.post(
            reverse("blood_requests:sms_inbound") + "?token=s3cret",
            {"from": "08000000000", "text": "yes I can come"},
        )

        self.assertFalse(Donor.objects.get(pk=self.alert.donor_id).sms_opt_out)


class RetryViewTests(TestCase):
    def test_retry_resends_only_failed_unanswered_alerts(self):
        from django.contrib.auth.models import User

        req = blood_request()
        failed = DonorAlert.objects.create(blood_request=req, donor=donor(phone="08000000001"), delivery_status="failed")
        answered = DonorAlert.objects.create(
            blood_request=req, donor=donor(phone="08000000002"), delivery_status="failed", response="available"
        )
        sent = DonorAlert.objects.create(blood_request=req, donor=donor(phone="08000000003"), delivery_status="sent")

        client = Client()
        client.force_login(User.objects.create_user(username="staffer", password="x"))

        with mock.patch("blood_requests.views.enqueue_alert") as enqueue:
            client.post(reverse("blood_requests:blood_request_retry", kwargs={"token": req.status_token}))

        enqueue.assert_called_once()
        self.assertEqual(enqueue.call_args.args[0], failed.pk)
        self.assertNotIn(answered.pk, [c.args[0] for c in enqueue.call_args_list])
        self.assertNotIn(sent.pk, [c.args[0] for c in enqueue.call_args_list])
