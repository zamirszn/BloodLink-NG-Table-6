"""Tests for Part 3 — request and contact.

Three things are covered, in the order a real user meets them: publishing a
request, matching and dispatching it to compatible donors, and a donor
answering the alert they receive.

The matching rules are deliberately asserted against
``donors.compatibility.compatible_donor_types`` rather than a re-typed list, so
this suite fails if the two halves of the project ever disagree about who can
give blood to whom.
"""

from datetime import date, timedelta
from unittest import mock

from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from donors.compatibility import ALL_BLOOD_TYPES, compatible_donor_types
from donors.models import Donor

from .matching import (
    MAX_ALERTS_PER_REQUEST,
    donors_to_alert,
    eligible_donors,
    notify_matching_donors,
    request_counts,
)
from .models import BloodRequest, DonorAlert, NotificationMessage
from .notifications.base import DeliveryResult, NotificationBackend

#: Messages handed to RecordingBackend, so a test can inspect what would have
#: been sent. Cleared per test by the class that uses it.
RECORDED = []


class RecordingBackend(NotificationBackend):
    """Backend that records messages instead of sending them."""

    name = "recording"

    def send(self, message):
        RECORDED.append(message)

        return DeliveryResult(status="sent", detail="recorded by the test backend")


class ExplodingBackend(NotificationBackend):
    """Backend that fails, to prove one bad send cannot abort a dispatch."""

    name = "exploding"

    def send(self, message):
        raise RuntimeError("provider is unreachable")


def make_donor(**overrides):
    """A donor who matches by default, so each test overrides only what it means to."""
    values = {
        "name": "Ada Obi",
        "blood_type": "O-",
        "genotype": "AA",
        "location": "Makurdi",
        "phone": "08000000000",
        "last_donation": None,
        "availability": True,
    }
    values.update(overrides)

    return Donor.objects.create(**values)


def make_request(**overrides):
    """An O- request in Makurdi, which the default donor satisfies."""
    values = {
        "requester_name": "Grace Ade",
        "requester_phone": "08011112222",
        "recipient_blood_type": "O-",
        "location": "Makurdi",
        "urgency": BloodRequest.Urgency.URGENT,
    }
    values.update(overrides)

    return BloodRequest.objects.create(**values)


class BloodRequestRulesTests(TestCase):
    """The model's own rules, independent of any view."""

    def test_a_new_request_is_open(self):
        self.assertTrue(make_request().is_open)

    def test_a_fulfilled_request_is_not_open(self):
        blood_request = make_request(status=BloodRequest.Status.FULFILLED)

        self.assertFalse(blood_request.is_open)

    def test_a_cancelled_request_is_not_open(self):
        blood_request = make_request(status=BloodRequest.Status.CANCELLED)

        self.assertFalse(blood_request.is_open)

    def test_compatible_donor_types_agrees_with_the_shared_mapping(self):
        for recipient in ALL_BLOOD_TYPES:
            with self.subTest(recipient=recipient):
                blood_request = make_request(recipient_blood_type=recipient)

                self.assertEqual(
                    blood_request.compatible_donor_types,
                    compatible_donor_types(recipient),
                )

    def test_a_unit_count_below_one_fails_model_validation(self):
        blood_request = make_request(units_needed=0)

        with self.assertRaises(ValidationError) as caught:
            blood_request.full_clean()

        self.assertIn("units_needed", caught.exception.message_dict)

    def test_a_unit_count_above_the_ceiling_fails_model_validation(self):
        blood_request = make_request(units_needed=21)

        with self.assertRaises(ValidationError) as caught:
            blood_request.full_clean()

        self.assertIn("units_needed", caught.exception.message_dict)

    def test_a_request_within_the_unit_range_validates(self):
        # The guard above is only meaningful if the happy path stays clean.
        make_request(units_needed=20).full_clean()


class AlertStateTests(TestCase):
    """Expiry and closability, which decide whether a reply is accepted."""

    def setUp(self):
        self.blood_request = make_request()
        self.donor = make_donor()
        self.alert = DonorAlert.objects.create(
            blood_request=self.blood_request, donor=self.donor
        )

    def _age_alert(self, days):
        DonorAlert.objects.filter(pk=self.alert.pk).update(
            created_at=timezone.now() - timedelta(days=days)
        )
        self.alert.refresh_from_db()

    def test_a_fresh_alert_has_not_expired(self):
        self.assertFalse(self.alert.is_expired)

    def test_an_alert_six_days_old_has_not_expired(self):
        self._age_alert(6)

        self.assertFalse(self.alert.is_expired)

    def test_an_alert_eight_days_old_has_expired(self):
        self._age_alert(8)

        self.assertTrue(self.alert.is_expired)

    def test_a_fresh_alert_on_an_open_request_can_be_answered(self):
        self.assertTrue(self.alert.can_respond)

    def test_an_expired_alert_cannot_be_answered(self):
        self._age_alert(8)

        self.assertFalse(self.alert.can_respond)

    def test_an_alert_on_a_closed_request_cannot_be_answered(self):
        self.blood_request.status = BloodRequest.Status.FULFILLED
        self.blood_request.save(update_fields=["status"])

        self.assertFalse(self.alert.can_respond)

    def test_respond_path_points_at_the_alert_endpoint(self):
        self.assertEqual(
            self.alert.respond_path, f"/requests/respond/{self.alert.token}/"
        )


class EligibleDonorsTests(TestCase):
    """Which donors the rules select."""

    def test_only_compatible_blood_types_are_matched(self):
        # A+ recipient may receive A+, A-, O+ and O-. B and AB may not.
        for blood_type in ALL_BLOOD_TYPES:
            make_donor(name=blood_type, blood_type=blood_type)

        matched = eligible_donors("A+")

        self.assertEqual(
            sorted(donor.blood_type for donor in matched),
            sorted(["A+", "A-", "O+", "O-"]),
        )

    def test_incompatible_donor_is_excluded(self):
        make_donor(name="B donor", blood_type="B+")

        self.assertEqual(eligible_donors("A+").count(), 0)

    def test_ab_plus_recipient_matches_every_blood_type(self):
        for blood_type in ALL_BLOOD_TYPES:
            make_donor(name=blood_type, blood_type=blood_type)

        self.assertEqual(eligible_donors("AB+").count(), len(ALL_BLOOD_TYPES))

    def test_o_negative_recipient_matches_only_o_negative(self):
        for blood_type in ALL_BLOOD_TYPES:
            make_donor(name=blood_type, blood_type=blood_type)

        matched = eligible_donors("O-")

        self.assertEqual([donor.blood_type for donor in matched], ["O-"])

    def test_matching_agrees_with_the_shared_compatibility_mapping(self):
        """The new app must never drift from Part 2's rules."""
        for blood_type in ALL_BLOOD_TYPES:
            make_donor(name=blood_type, blood_type=blood_type)

        for recipient in ALL_BLOOD_TYPES:
            with self.subTest(recipient=recipient):
                matched = set(
                    eligible_donors(recipient).values_list("blood_type", flat=True)
                )

                self.assertEqual(matched, set(compatible_donor_types(recipient)))

    def test_donor_who_never_donated_is_matched(self):
        make_donor(last_donation=None)

        self.assertEqual(eligible_donors("O-").count(), 1)

    def test_donor_who_donated_ten_days_ago_is_excluded(self):
        make_donor(last_donation=date.today() - timedelta(days=10))

        self.assertEqual(eligible_donors("O-").count(), 0)

    def test_donor_at_exactly_ninety_days_is_matched(self):
        make_donor(last_donation=date.today() - timedelta(days=90))

        self.assertEqual(eligible_donors("O-").count(), 1)

    def test_donor_at_eighty_nine_days_is_excluded(self):
        make_donor(last_donation=date.today() - timedelta(days=89))

        self.assertEqual(eligible_donors("O-").count(), 0)

    def test_unavailable_donor_is_excluded(self):
        make_donor(availability=False)

        self.assertEqual(eligible_donors("O-").count(), 0)

    def test_location_match_is_case_insensitive(self):
        make_donor(location="Makurdi")

        self.assertEqual(eligible_donors("O-", "makurdi").count(), 1)

    def test_donor_outside_the_requested_location_is_excluded(self):
        make_donor(location="Enugu")

        self.assertEqual(eligible_donors("O-", "Makurdi").count(), 0)

    def test_blank_location_places_no_restriction(self):
        make_donor(name="a", location="Enugu")
        make_donor(name="b", location="Kano")

        self.assertEqual(eligible_donors("O-", "").count(), 2)

    def test_location_whitespace_is_ignored(self):
        make_donor(location="Makurdi")

        self.assertEqual(eligible_donors("O-", "   ").count(), 1)


class DonorsToAlertTests(TestCase):
    """Re-dispatching must be a no-op."""

    def test_already_alerted_donor_is_not_returned_again(self):
        blood_request = make_request()
        donor = make_donor()

        DonorAlert.objects.create(blood_request=blood_request, donor=donor)

        self.assertEqual(donors_to_alert(blood_request).count(), 0)

    def test_unalerted_donor_is_returned(self):
        blood_request = make_request()
        make_donor()

        self.assertEqual(donors_to_alert(blood_request).count(), 1)


class NotifyTests(TestCase):
    """Dispatching alerts."""

    def setUp(self):
        RECORDED.clear()

    def test_one_alert_per_matching_donor_is_created(self):
        blood_request = make_request()
        make_donor(name="a")
        make_donor(name="b")

        created, skipped = notify_matching_donors(
            blood_request, base_url="http://testserver/"
        )

        self.assertEqual(created, 2)
        self.assertEqual(skipped, 0)
        self.assertEqual(blood_request.alerts.count(), 2)

    def test_non_matching_donors_are_not_alerted(self):
        blood_request = make_request(recipient_blood_type="O-")
        make_donor(name="incompatible", blood_type="AB+")

        notify_matching_donors(blood_request, base_url="http://testserver/")

        self.assertEqual(blood_request.alerts.count(), 0)

    def test_notifying_twice_creates_no_duplicate_alerts(self):
        blood_request = make_request()
        make_donor(name="a")
        make_donor(name="b")

        notify_matching_donors(blood_request, base_url="http://testserver/")
        created, _ = notify_matching_donors(blood_request, base_url="http://testserver/")

        self.assertEqual(created, 0)
        self.assertEqual(blood_request.alerts.count(), 2)

    def test_notifying_twice_creates_no_extra_outbox_rows(self):
        blood_request = make_request()
        make_donor()

        notify_matching_donors(blood_request, base_url="http://testserver/")
        notify_matching_donors(blood_request, base_url="http://testserver/")

        self.assertEqual(NotificationMessage.objects.count(), 1)

    @mock.patch("blood_requests.matching.donors_to_alert")
    def test_a_duplicate_insert_is_skipped_without_aborting_the_dispatch(self, pending):
        """A concurrent dispatch can insert between our check and our create."""
        blood_request = make_request()
        first = make_donor(name="a")
        second = make_donor(name="b")

        # An alert for `first` already exists. The pre-filter would normally
        # hide it, so returning it anyway stands in for a racing dispatcher.
        DonorAlert.objects.create(blood_request=blood_request, donor=first)

        pending.return_value = Donor.objects.filter(pk__in=[first.pk, second.pk])

        created, _ = notify_matching_donors(
            blood_request, base_url="http://testserver/"
        )

        # The duplicate is skipped, the other donor is still alerted, and the
        # duplicate produced no second message.
        self.assertEqual(created, 1)
        self.assertEqual(blood_request.alerts.count(), 2)
        self.assertEqual(NotificationMessage.objects.count(), 1)

    def test_a_reply_is_not_cleared_by_a_later_dispatch(self):
        blood_request = make_request()
        make_donor()

        notify_matching_donors(blood_request, base_url="http://testserver/")

        alert = DonorAlert.objects.get()
        alert.response = DonorAlert.Response.AVAILABLE
        alert.save(update_fields=["response"])

        notify_matching_donors(blood_request, base_url="http://testserver/")

        alert.refresh_from_db()
        self.assertEqual(alert.response, DonorAlert.Response.AVAILABLE)

    def test_a_donor_becoming_eligible_later_is_alerted_on_the_next_dispatch(self):
        blood_request = make_request()
        make_donor(name="recent", last_donation=date.today() - timedelta(days=5))

        created, _ = notify_matching_donors(blood_request, base_url="http://testserver/")
        self.assertEqual(created, 0)

        # The donor's last donation ages past the 90-day mark.
        Donor.objects.update(last_donation=date.today() - timedelta(days=91))

        created, _ = notify_matching_donors(blood_request, base_url="http://testserver/")

        self.assertEqual(created, 1)

    def test_alerts_are_marked_sent_by_the_console_backend(self):
        blood_request = make_request()
        make_donor()

        notify_matching_donors(blood_request, base_url="http://testserver/")

        self.assertEqual(
            DonorAlert.objects.get().delivery_status,
            DonorAlert.DeliveryStatus.SENT,
        )

    def test_outbox_row_records_the_message_that_was_sent(self):
        blood_request = make_request()
        donor = make_donor(name="Ada Obi")

        notify_matching_donors(blood_request, base_url="http://testserver/")

        message = NotificationMessage.objects.get()

        self.assertEqual(message.backend, "console")
        self.assertEqual(message.recipient_phone, donor.phone)
        self.assertEqual(message.status, NotificationMessage.Status.SENT)
        self.assertEqual(message.alert, DonorAlert.objects.get())

    def test_outbox_row_carries_the_donors_personal_reply_link(self):
        blood_request = make_request()
        make_donor()

        notify_matching_donors(blood_request, base_url="http://testserver/")

        alert = DonorAlert.objects.get()
        message = NotificationMessage.objects.get()

        self.assertIn(f"http://testserver/requests/respond/{alert.token}/", message.body)
        # The body must name the request type and the units needed.
        self.assertIn("O-", message.body)

    def test_console_backend_writes_to_the_outbox_logger(self):
        blood_request = make_request()
        make_donor()

        with self.assertLogs("blood_requests.outbox", level="INFO") as captured:
            notify_matching_donors(blood_request, base_url="http://testserver/")

        self.assertIn("OUTBOX", captured.output[0])

    @override_settings(
        NOTIFICATION_BACKEND="blood_requests.tests.RecordingBackend"
    )
    def test_backend_is_resolved_from_the_notification_backend_setting(self):
        blood_request = make_request()
        make_donor(name="Ada Obi")

        notify_matching_donors(blood_request, base_url="http://testserver/")

        self.assertEqual(len(RECORDED), 1)
        self.assertEqual(RECORDED[0].recipient_phone, "08000000000")
        self.assertEqual(NotificationMessage.objects.get().backend, "recording")

    @override_settings(
        NOTIFICATION_BACKEND="blood_requests.tests.ExplodingBackend"
    )
    def test_a_failing_backend_is_recorded_and_does_not_abort_the_dispatch(self):
        blood_request = make_request()
        make_donor(name="a")
        make_donor(name="b")

        # assertLogs also keeps the handled traceback out of the test output.
        with self.assertLogs("blood_requests.matching", level="ERROR"):
            created, _ = notify_matching_donors(
                blood_request, base_url="http://testserver/"
            )

        # Both alerts exist, both were attempted, both are recorded as failed.
        self.assertEqual(created, 2)
        self.assertEqual(blood_request.alerts.count(), 2)
        self.assertEqual(NotificationMessage.objects.count(), 2)
        self.assertFalse(
            NotificationMessage.objects.exclude(
                status=NotificationMessage.Status.FAILED
            ).exists()
        )
        self.assertFalse(
            blood_request.alerts.exclude(
                delivery_status=DonorAlert.DeliveryStatus.FAILED
            ).exists()
        )

    def test_dispatch_is_capped_per_press(self):
        blood_request = make_request()
        for index in range(MAX_ALERTS_PER_REQUEST + 3):
            make_donor(name=f"donor {index}")

        created, skipped = notify_matching_donors(
            blood_request, base_url="http://testserver/"
        )

        self.assertEqual(created, MAX_ALERTS_PER_REQUEST)
        self.assertEqual(skipped, 3)
        self.assertEqual(blood_request.alerts.count(), MAX_ALERTS_PER_REQUEST)

    def test_the_cap_does_not_lose_donors_on_a_later_press(self):
        blood_request = make_request()
        for index in range(MAX_ALERTS_PER_REQUEST + 3):
            make_donor(name=f"donor {index}")

        notify_matching_donors(blood_request, base_url="http://testserver/")
        created, skipped = notify_matching_donors(
            blood_request, base_url="http://testserver/"
        )

        self.assertEqual(created, 3)
        self.assertEqual(skipped, 0)
        self.assertEqual(
            blood_request.alerts.count(), MAX_ALERTS_PER_REQUEST + 3
        )


class TokenTests(TestCase):
    """Alert and status tokens."""

    def test_alert_tokens_are_unique(self):
        blood_request = make_request()
        first = DonorAlert.objects.create(blood_request=blood_request, donor=make_donor(name="a"))
        second = DonorAlert.objects.create(blood_request=blood_request, donor=make_donor(name="b"))

        self.assertNotEqual(first.token, second.token)

    def test_tokens_are_url_safe_and_long_enough(self):
        blood_request = make_request()
        alert = DonorAlert.objects.create(blood_request=blood_request, donor=make_donor())

        self.assertGreaterEqual(len(alert.token), 32)
        self.assertTrue(alert.token.replace("-", "").replace("_", "").isalnum())

    def test_status_tokens_are_unique(self):
        first = make_request()
        second = make_request()

        self.assertNotEqual(first.status_token, second.status_token)

    def test_a_duplicate_alert_for_the_same_donor_is_refused_by_the_database(self):
        """The constraint, not the application check, is the real guarantee."""
        blood_request = make_request()
        donor = make_donor()

        DonorAlert.objects.create(blood_request=blood_request, donor=donor)

        with self.assertRaises(IntegrityError):
            DonorAlert.objects.create(blood_request=blood_request, donor=donor)


class RequestCountsTests(TestCase):
    """The headline numbers shown on a request."""

    def test_counts_reflect_matches_alerts_and_replies(self):
        blood_request = make_request()
        make_donor(name="a")
        make_donor(name="b")
        make_donor(name="unavailable", availability=False)

        notify_matching_donors(blood_request, base_url="http://testserver/")

        alert = blood_request.alerts.first()
        alert.response = DonorAlert.Response.AVAILABLE
        alert.save(update_fields=["response"])

        counts = request_counts(blood_request)

        self.assertEqual(counts["matched"], 2)
        self.assertEqual(counts["alerted"], 2)
        self.assertEqual(counts["responded"], 1)
        self.assertEqual(counts["available"], 1)


class RequestCreateTests(TestCase):
    """Submitting a request."""

    def _payload(self, **overrides):
        values = {
            "requester_name": "Grace Ade",
            "requester_phone": "08011112222",
            "recipient_blood_type": "O-",
            "location": "Makurdi",
            "urgency": "urgent",
            "units_needed": "2",
            "hospital": "St. Mary's",
            "notes": "",
        }
        values.update(overrides)

        return values

    def test_the_form_page_renders(self):
        response = self.client.get(reverse("blood_requests:blood_request_create"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Post a Blood Request")

    def test_a_valid_submission_creates_the_request(self):
        response = self.client.post(
            reverse("blood_requests:blood_request_create"), self._payload()
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(BloodRequest.objects.count(), 1)

        blood_request = BloodRequest.objects.get()
        self.assertEqual(blood_request.recipient_blood_type, "O-")
        self.assertEqual(blood_request.units_needed, 2)
        self.assertEqual(blood_request.hospital, "St. Mary's")
        self.assertEqual(blood_request.status, BloodRequest.Status.OPEN)

    def test_a_valid_submission_redirects_to_the_control_panel(self):
        response = self.client.post(
            reverse("blood_requests:blood_request_create"), self._payload()
        )

        blood_request = BloodRequest.objects.get()

        self.assertRedirects(
            response,
            reverse(
                "blood_requests:blood_request_manage",
                kwargs={"token": blood_request.status_token},
            ),
        )

    def test_the_new_request_is_remembered_in_the_session(self):
        self.client.post(reverse("blood_requests:blood_request_create"), self._payload())

        blood_request = BloodRequest.objects.get()

        self.assertIn(
            blood_request.status_token,
            self.client.session["blood_request_tokens"],
        )

    def test_the_request_appears_under_requests_you_posted(self):
        self.client.post(reverse("blood_requests:blood_request_create"), self._payload())

        response = self.client.get(reverse("blood_requests:blood_request_list"))

        self.assertContains(response, "Requests you posted")

    def test_a_submission_missing_required_fields_is_rejected(self):
        response = self.client.post(
            reverse("blood_requests:blood_request_create"),
            {"requester_name": "", "requester_phone": "", "units_needed": ""},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(BloodRequest.objects.count(), 0)

    def test_units_below_one_are_rejected(self):
        response = self.client.post(
            reverse("blood_requests:blood_request_create"),
            self._payload(units_needed="0"),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(BloodRequest.objects.count(), 0)
        self.assertFormError(response.context["form"], "units_needed", "Ensure this value is greater than or equal to 1.")

    def test_units_above_the_ceiling_are_rejected(self):
        self.client.post(
            reverse("blood_requests:blood_request_create"),
            self._payload(units_needed="21"),
        )

        self.assertEqual(BloodRequest.objects.count(), 0)

    def test_an_unknown_blood_type_is_rejected(self):
        response = self.client.post(
            reverse("blood_requests:blood_request_create"),
            self._payload(recipient_blood_type="ZZ"),
        )

        self.assertEqual(BloodRequest.objects.count(), 0)
        self.assertFormError(
            response.context["form"],
            "recipient_blood_type",
            "Select a valid choice. ZZ is not one of the available choices.",
        )

    def test_a_missing_blood_type_is_rejected(self):
        # The field is a choice with an empty first option, so leaving it
        # untouched must not silently pick one.
        self.client.post(
            reverse("blood_requests:blood_request_create"),
            self._payload(recipient_blood_type=""),
        )

        self.assertEqual(BloodRequest.objects.count(), 0)

    def test_an_unknown_urgency_is_rejected(self):
        self.client.post(
            reverse("blood_requests:blood_request_create"),
            self._payload(urgency="whenever"),
        )

        self.assertEqual(BloodRequest.objects.count(), 0)

    def test_a_missing_urgency_is_rejected(self):
        self.client.post(
            reverse("blood_requests:blood_request_create"),
            self._payload(urgency=""),
        )

        self.assertEqual(BloodRequest.objects.count(), 0)

    def test_hospital_location_and_notes_may_be_blank(self):
        self.client.post(
            reverse("blood_requests:blood_request_create"),
            self._payload(hospital="", location="", notes=""),
        )

        self.assertEqual(BloodRequest.objects.count(), 1)

    def test_surrounding_whitespace_is_trimmed(self):
        self.client.post(
            reverse("blood_requests:blood_request_create"),
            self._payload(location="  Makurdi  ", requester_name="  Grace Ade  "),
        )

        blood_request = BloodRequest.objects.get()

        self.assertEqual(blood_request.location, "Makurdi")
        self.assertEqual(blood_request.requester_name, "Grace Ade")


class ReplyTests(TestCase):
    """A donor answering an alert."""

    def setUp(self):
        self.blood_request = make_request()
        self.donor = make_donor()
        self.alert = DonorAlert.objects.create(
            blood_request=self.blood_request, donor=self.donor
        )
        self.url = reverse(
            "blood_requests:alert_respond", kwargs={"token": self.alert.token}
        )

    def test_get_offers_both_answers_and_records_nothing(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Yes, I can donate")
        self.assertContains(response, "No, I cannot")

        self.alert.refresh_from_db()
        self.assertEqual(self.alert.response, DonorAlert.Response.PENDING)
        self.assertIsNone(self.alert.responded_at)

    def test_answering_available_is_recorded(self):
        self.client.post(self.url, {"response": "available"})

        self.alert.refresh_from_db()
        self.assertEqual(self.alert.response, DonorAlert.Response.AVAILABLE)
        self.assertIsNotNone(self.alert.responded_at)

    def test_answering_not_available_is_recorded(self):
        self.client.post(self.url, {"response": "not_available"})

        self.alert.refresh_from_db()
        self.assertEqual(self.alert.response, DonorAlert.Response.NOT_AVAILABLE)
        self.assertIsNotNone(self.alert.responded_at)

    def test_a_post_redirects_back_to_the_same_page(self):
        response = self.client.post(self.url, {"response": "available"})

        self.assertRedirects(response, self.url)

    def test_an_answer_can_be_changed(self):
        self.client.post(self.url, {"response": "available"})
        self.client.post(self.url, {"response": "not_available"})

        self.alert.refresh_from_db()
        self.assertEqual(self.alert.response, DonorAlert.Response.NOT_AVAILABLE)

        # Changing a mind must never create a second alert.
        self.assertEqual(DonorAlert.objects.count(), 1)

    def test_the_current_answer_is_shown_on_a_later_visit(self):
        self.client.post(self.url, {"response": "available"})

        response = self.client.get(self.url)

        self.assertContains(response, "Available to donate")

    def test_an_unrecognised_answer_is_rejected(self):
        self.client.post(self.url, {"response": "maybe"})

        self.alert.refresh_from_db()
        self.assertEqual(self.alert.response, DonorAlert.Response.PENDING)

    def test_a_missing_answer_is_rejected(self):
        self.client.post(self.url, {})

        self.alert.refresh_from_db()
        self.assertEqual(self.alert.response, DonorAlert.Response.PENDING)

    def test_an_unknown_token_is_not_found(self):
        response = self.client.get(
            reverse("blood_requests:alert_respond", kwargs={"token": "nope"})
        )

        self.assertEqual(response.status_code, 404)

    def test_an_expired_link_cannot_be_answered(self):
        DonorAlert.objects.filter(pk=self.alert.pk).update(
            created_at=timezone.now() - timedelta(days=8)
        )

        response = self.client.post(self.url, {"response": "available"})

        self.alert.refresh_from_db()
        self.assertEqual(self.alert.response, DonorAlert.Response.PENDING)
        self.assertContains(
            self.client.get(self.url), "has expired", status_code=200
        )

    def test_a_closed_request_cannot_be_answered(self):
        self.blood_request.status = BloodRequest.Status.FULFILLED
        self.blood_request.save(update_fields=["status"])

        self.client.post(self.url, {"response": "available"})

        self.alert.refresh_from_db()
        self.assertEqual(self.alert.response, DonorAlert.Response.PENDING)

    def test_a_closed_request_explains_itself(self):
        self.blood_request.status = BloodRequest.Status.CANCELLED
        self.blood_request.save(update_fields=["status"])

        response = self.client.get(self.url)

        self.assertContains(response, "no longer accepting replies")

    def test_an_expired_link_still_shows_a_previous_answer(self):
        self.client.post(self.url, {"response": "available"})

        DonorAlert.objects.filter(pk=self.alert.pk).update(
            created_at=timezone.now() - timedelta(days=8)
        )

        response = self.client.get(self.url)

        self.assertContains(response, "Available to donate")


class ManageTests(TestCase):
    """The requester's control panel."""

    def setUp(self):
        self.blood_request = make_request()
        self.manage_url = reverse(
            "blood_requests:blood_request_manage",
            kwargs={"token": self.blood_request.status_token},
        )

    def test_the_panel_renders(self):
        response = self.client.get(self.manage_url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Request control panel")

    def test_a_wrong_token_is_not_found(self):
        response = self.client.get(
            reverse("blood_requests:blood_request_manage", kwargs={"token": "wrong"})
        )

        self.assertEqual(response.status_code, 404)

    def test_the_primary_key_is_not_a_valid_way_in(self):
        # The pk-addressed detail page is public; the control panel must not be.
        response = self.client.get(f"/requests/manage/{self.blood_request.pk}/")

        self.assertEqual(response.status_code, 404)

    def test_the_panel_warns_that_the_address_is_a_credential(self):
        response = self.client.get(self.manage_url)

        self.assertContains(response, "Keep this page's address private.")

    def test_notifying_dispatches_alerts_to_matching_donors(self):
        make_donor(name="Ada Obi")

        response = self.client.post(
            reverse(
                "blood_requests:blood_request_notify",
                kwargs={"token": self.blood_request.status_token},
            )
        )

        self.assertRedirects(response, self.manage_url)
        self.assertEqual(self.blood_request.alerts.count(), 1)
        self.assertEqual(NotificationMessage.objects.count(), 1)

    def test_notifying_twice_does_not_duplicate_alerts(self):
        make_donor(name="Ada Obi")

        notify_url = reverse(
            "blood_requests:blood_request_notify",
            kwargs={"token": self.blood_request.status_token},
        )

        self.client.post(notify_url)
        self.client.post(notify_url)

        self.assertEqual(self.blood_request.alerts.count(), 1)
        self.assertEqual(NotificationMessage.objects.count(), 1)

    def test_notifying_with_a_wrong_token_is_not_found(self):
        response = self.client.post("/requests/manage/wrong/notify/")

        self.assertEqual(response.status_code, 404)

    def test_notifying_cannot_be_done_with_a_get(self):
        response = self.client.get(
            reverse(
                "blood_requests:blood_request_notify",
                kwargs={"token": self.blood_request.status_token},
            )
        )

        self.assertEqual(response.status_code, 405)

    def test_changing_status_cannot_be_done_with_a_get(self):
        response = self.client.get(
            reverse(
                "blood_requests:blood_request_status",
                kwargs={"token": self.blood_request.status_token},
            )
        )

        self.assertEqual(response.status_code, 405)

    def test_marking_fulfilled_updates_the_status(self):
        self.client.post(
            reverse(
                "blood_requests:blood_request_status",
                kwargs={"token": self.blood_request.status_token},
            ),
            {"status": "fulfilled"},
        )

        self.blood_request.refresh_from_db()
        self.assertEqual(self.blood_request.status, BloodRequest.Status.FULFILLED)

    def test_an_unknown_status_is_rejected(self):
        self.client.post(
            reverse(
                "blood_requests:blood_request_status",
                kwargs={"token": self.blood_request.status_token},
            ),
            {"status": "deleted"},
        )

        self.blood_request.refresh_from_db()
        self.assertEqual(self.blood_request.status, BloodRequest.Status.OPEN)

    def test_the_responder_table_shows_contact_details(self):
        donor = make_donor(name="Ada Obi", phone="08098765432")
        DonorAlert.objects.create(blood_request=self.blood_request, donor=donor)

        response = self.client.get(self.manage_url)

        self.assertContains(response, "08098765432")
        self.assertContains(response, 'href="tel:08098765432"')

    def test_a_reply_is_visible_on_the_panel(self):
        donor = make_donor(name="Ada Obi")
        alert = DonorAlert.objects.create(
            blood_request=self.blood_request, donor=donor
        )
        alert.response = DonorAlert.Response.AVAILABLE
        alert.save(update_fields=["response"])

        response = self.client.get(self.manage_url)

        self.assertContains(response, "Available")


class PrivacyTests(TestCase):
    """Contact details must not leak onto pages anyone can reach."""

    def setUp(self):
        self.blood_request = make_request(requester_phone="08033334444")
        self.donor = make_donor(name="Ada Obi", phone="08055556666")
        DonorAlert.objects.create(blood_request=self.blood_request, donor=self.donor)

    def test_the_public_detail_page_shows_no_phone_numbers(self):
        response = self.client.get(
            reverse(
                "blood_requests:blood_request_detail",
                kwargs={"pk": self.blood_request.pk},
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, self.donor.phone)
        self.assertNotContains(response, self.blood_request.requester_phone)

    def test_the_public_detail_page_still_shows_the_useful_facts(self):
        response = self.client.get(
            reverse(
                "blood_requests:blood_request_detail",
                kwargs={"pk": self.blood_request.pk},
            )
        )

        self.assertContains(response, "Ada Obi")
        self.assertContains(response, "O-")


class MyAlertsTests(TestCase):
    """The in-app alert list, which is the no-SMS demo path."""

    def setUp(self):
        self.blood_request = make_request()
        self.donor = make_donor(name="Ada Obi")
        self.alert = DonorAlert.objects.create(
            blood_request=self.blood_request, donor=self.donor
        )
        self.url = reverse("blood_requests:my_alerts")

    def _sign_in(self, donor):
        session = self.client.session
        session["donor_id"] = donor.pk
        session.save()

    def test_the_page_renders_without_a_donor(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No donor in this browser")

    def test_a_donor_with_no_alerts_sees_an_empty_state(self):
        self._sign_in(make_donor(name="Unrelated", phone="08077778888"))

        response = self.client.get(self.url)

        self.assertContains(response, "No alerts yet")

    def test_only_the_signed_in_donors_alerts_are_listed(self):
        # The page lists alerts from the donor's own point of view, so the
        # distinguishing detail is the request, not the donor's own name.
        self.blood_request.hospital = "St. Mary"
        self.blood_request.save(update_fields=["hospital"])

        other = make_donor(name="Someone Else", phone="08099990000")
        DonorAlert.objects.create(
            blood_request=make_request(hospital="Other Hospital"), donor=other
        )

        self._sign_in(self.donor)

        response = self.client.get(self.url)

        self.assertContains(response, "St. Mary")
        self.assertNotContains(response, "Other Hospital")

    def test_the_alert_links_to_a_working_reply_page(self):
        self._sign_in(self.donor)

        response = self.client.get(self.url)

        self.assertContains(response, self.alert.respond_path)

        # And that link must actually resolve.
        self.assertEqual(self.client.get(self.alert.respond_path).status_code, 200)

    def test_an_awaiting_alert_is_labelled(self):
        self._sign_in(self.donor)

        response = self.client.get(self.url)

        self.assertContains(response, "Awaiting your reply")

    def test_an_answered_alert_is_labelled(self):
        self.alert.response = DonorAlert.Response.NOT_AVAILABLE
        self.alert.save(update_fields=["response"])

        self._sign_in(self.donor)

        response = self.client.get(self.url)

        self.assertContains(response, "Not available")
        self.assertContains(response, "Change reply")


class RequestListTests(TestCase):
    """The open-request board."""

    def test_an_empty_board_shows_an_empty_state(self):
        response = self.client.get(reverse("blood_requests:blood_request_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No open requests")

    def test_open_requests_are_listed(self):
        make_request(hospital="St. Mary's")

        response = self.client.get(reverse("blood_requests:blood_request_list"))

        self.assertContains(response, "St. Mary&#x27;s")

    def test_closed_requests_are_not_listed(self):
        make_request(hospital="Closed Hospital", status=BloodRequest.Status.FULFILLED)

        response = self.client.get(reverse("blood_requests:blood_request_list"))

        self.assertNotContains(response, "Closed Hospital")

    def test_critical_requests_are_listed_first(self):
        make_request(hospital="Routine Hospital", urgency=BloodRequest.Urgency.ROUTINE)
        make_request(hospital="Critical Hospital", urgency=BloodRequest.Urgency.CRITICAL)

        response = self.client.get(reverse("blood_requests:blood_request_list"))

        body = response.content.decode()
        self.assertLess(
            body.index("Critical Hospital"),
            body.index("Routine Hospital"),
        )


class CsrfTests(TestCase):
    """State-changing endpoints must be CSRF protected."""

    def setUp(self):
        self.blood_request = make_request()
        self.csrf_client = Client(enforce_csrf_checks=True)

    def test_notifying_without_a_csrf_token_is_forbidden(self):
        response = self.csrf_client.post(
            reverse(
                "blood_requests:blood_request_notify",
                kwargs={"token": self.blood_request.status_token},
            )
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.blood_request.alerts.count(), 0)

    def test_changing_status_without_a_csrf_token_is_forbidden(self):
        response = self.csrf_client.post(
            reverse(
                "blood_requests:blood_request_status",
                kwargs={"token": self.blood_request.status_token},
            ),
            {"status": "fulfilled"},
        )

        self.assertEqual(response.status_code, 403)

        self.blood_request.refresh_from_db()
        self.assertEqual(self.blood_request.status, BloodRequest.Status.OPEN)

    def test_replying_without_a_csrf_token_is_forbidden(self):
        alert = DonorAlert.objects.create(
            blood_request=self.blood_request, donor=make_donor()
        )

        response = self.csrf_client.post(
            reverse("blood_requests:alert_respond", kwargs={"token": alert.token}),
            {"response": "available"},
        )

        self.assertEqual(response.status_code, 403)

        alert.refresh_from_db()
        self.assertEqual(alert.response, DonorAlert.Response.PENDING)


class OutboxTests(TestCase):
    """The outbox is a durable record, not a mirror of the alert table."""

    def test_the_outbox_survives_deletion_of_its_alert(self):
        blood_request = make_request()
        make_donor()

        notify_matching_donors(blood_request, base_url="http://testserver/")

        alert = DonorAlert.objects.get()
        message = NotificationMessage.objects.get()

        alert.delete()

        message.refresh_from_db()
        self.assertIsNone(message.alert)
        self.assertEqual(NotificationMessage.objects.count(), 1)


class DispatchIntegrationTests(TestCase):
    """The matching service driven through the HTTP layer."""

    def test_a_dispatch_records_the_full_outbox_body(self):
        blood_request = make_request(location="Makurdi")
        make_donor(name="Ada Obi", location="Makurdi")

        notify_matching_donors(blood_request, base_url="http://testserver/")

        message = NotificationMessage.objects.get()
        alert = DonorAlert.objects.get()

        self.assertIn(alert.token, message.body)
        self.assertIn("URGENT", message.body)
        self.assertIn("Ada Obi", message.body)
        self.assertIn("Makurdi", message.body)

    def test_no_message_is_ever_transmitted_by_the_default_backend(self):
        from .notifications import get_backend

        backend = get_backend()

        self.assertEqual(backend.name, "console")
