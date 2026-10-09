"""F4 donations, F5 expiry, F6 volunteering, F7 account ownership."""

from datetime import date, timedelta
from io import StringIO
from unittest import mock

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from donors.eligibility import ELIGIBILITY_DAYS
from donors.models import Donor
from donors.reminders import send_eligibility_reminders

from .lifecycle import expire_requests, send_expiry_reminders
from .matching import donors_to_alert, eligible_donors, request_counts, volunteer_for_request
from .models import BloodRequest, Donation, DonorAlert, NotificationMessage
from .tests import make_donor, make_request

BASE = "http://testserver/"


def at(moment):
    """Pin timezone.now() (and so every is_open / auto_now) to ``moment``."""
    return mock.patch("django.utils.timezone.now", return_value=moment)


class ExpiryTests(TestCase):
    def test_expiry_comes_from_urgency(self):
        now = timezone.now()
        critical = make_request(urgency="critical")
        urgent = make_request(urgency="urgent")
        routine = make_request(urgency="routine")

        for blood_request, days in ((critical, 3), (urgent, 7), (routine, 14)):
            delta = blood_request.expires_at - now
            self.assertAlmostEqual(delta.total_seconds(), days * 86400, delta=60)

    @override_settings(REQUEST_EXPIRY_DAYS={"critical": 1, "urgent": 2, "routine": 3})
    def test_the_defaults_are_settings(self):
        delta = make_request(urgency="critical").expires_at - timezone.now()

        self.assertAlmostEqual(delta.total_seconds(), 86400, delta=60)

    def test_a_lapsed_request_is_closed_even_before_the_job_runs(self):
        blood_request = make_request()
        later = blood_request.expires_at + timedelta(minutes=1)

        with at(later):
            self.assertFalse(blood_request.is_open)
            self.assertEqual(BloodRequest.objects.open_now().count(), 0)

        self.assertTrue(blood_request.is_open)

    def test_the_job_marks_only_lapsed_open_requests_and_is_safe_to_repeat(self):
        lapsed = make_request()
        fresh = make_request()
        BloodRequest.objects.filter(pk=lapsed.pk).update(expires_at=timezone.now() - timedelta(hours=1))
        done = make_request(status="fulfilled")
        BloodRequest.objects.filter(pk=done.pk).update(expires_at=timezone.now() - timedelta(hours=1))

        self.assertEqual(expire_requests(), 1)
        self.assertEqual(expire_requests(), 0)

        for blood_request, expected in ((lapsed, "expired"), (fresh, "open"), (done, "fulfilled")):
            blood_request.refresh_from_db()
            self.assertEqual(blood_request.status, expected)

    def test_an_expired_request_leaves_the_board_and_matching(self):
        blood_request = make_request()
        donor = make_donor()
        BloodRequest.objects.filter(pk=blood_request.pk).update(expires_at=timezone.now() - timedelta(hours=1))
        expire_requests()

        client = Client()
        client.force_login(User.objects.create_user(username="v", password="x"))
        shown = client.get(reverse("blood_requests:blood_request_list")).context["requests"]

        self.assertNotIn(blood_request, list(shown))
        from .matching import open_requests_for_donor

        self.assertNotIn(blood_request, open_requests_for_donor(donor))

    def test_an_expired_request_cannot_be_answered(self):
        blood_request = make_request()
        alert = DonorAlert.objects.create(blood_request=blood_request, donor=make_donor())
        BloodRequest.objects.filter(pk=blood_request.pk).update(expires_at=timezone.now() - timedelta(seconds=1))

        response = Client().post(reverse("blood_requests:alert_respond", args=[alert.token]), {"response": "available"})

        alert.refresh_from_db()
        self.assertEqual(alert.response, DonorAlert.Response.PENDING)
        self.assertEqual(response.status_code, 302)

    def test_extending_resets_the_clock_and_reopens(self):
        blood_request = make_request()
        BloodRequest.objects.filter(pk=blood_request.pk).update(
            expires_at=timezone.now() - timedelta(hours=1), status="expired"
        )
        blood_request.refresh_from_db()

        blood_request.extend()
        blood_request.refresh_from_db()

        self.assertEqual(blood_request.status, "open")
        self.assertTrue(blood_request.is_open)
        self.assertIsNone(blood_request.expiry_reminder_sent_at)

    def test_a_fulfilled_request_cannot_be_extended(self):
        owner = User.objects.create_user(username="o", password="x")
        blood_request = make_request(status="fulfilled", requester_user=owner)
        client = Client()
        client.force_login(owner)

        client.post(reverse("blood_requests:blood_request_extend", kwargs={"token": blood_request.status_token}))

        blood_request.refresh_from_db()
        self.assertEqual(blood_request.status, "fulfilled")

    def test_reminder_goes_out_once_inside_the_24_hour_window(self):
        blood_request = make_request()
        BloodRequest.objects.filter(pk=blood_request.pk).update(expires_at=timezone.now() + timedelta(hours=10))

        self.assertEqual(send_expiry_reminders(base_url="https://x.test"), 1)
        self.assertEqual(send_expiry_reminders(base_url="https://x.test"), 0)

        message = NotificationMessage.objects.get()
        self.assertIn(blood_request.status_token, message.body)
        self.assertEqual(message.recipient_phone, blood_request.requester_phone)

    def test_no_reminder_outside_the_window_or_for_unverified_requesters(self):
        far = make_request()
        unverified = make_request(requester_phone_verified_at=None)
        BloodRequest.objects.filter(pk=unverified.pk).update(expires_at=timezone.now() + timedelta(hours=5))

        self.assertEqual(send_expiry_reminders(), 0)
        self.assertEqual(far.expiry_reminder_sent_at, None)

    def test_the_management_command_runs_both_jobs(self):
        blood_request = make_request()
        BloodRequest.objects.filter(pk=blood_request.pk).update(expires_at=timezone.now() - timedelta(minutes=1))
        out = StringIO()

        call_command("expire_requests", stdout=out)

        self.assertIn("expired: 1", out.getvalue())


class VolunteerTests(TestCase):
    def setUp(self):
        self.blood_request = make_request(location="Makurdi")
        self.donor = make_donor(location="Makurdi")

    def test_a_matching_donor_can_volunteer(self):
        alert, outcome = volunteer_for_request(self.donor, self.blood_request)

        self.assertEqual(outcome, "created")
        self.assertEqual(alert.source, "volunteer")
        self.assertEqual(alert.response, "available")
        self.assertEqual(alert.delivery_status, "not_sent")
        self.assertIsNotNone(alert.responded_at)

    def test_volunteering_twice_is_a_no_op(self):
        volunteer_for_request(self.donor, self.blood_request)
        _, outcome = volunteer_for_request(self.donor, self.blood_request)

        self.assertEqual(outcome, "already")
        self.assertEqual(DonorAlert.objects.count(), 1)

    def test_a_non_matching_donor_is_refused(self):
        wrong_type = make_donor(phone="08000000011", blood_type="A+")
        wrong_place = make_donor(phone="08000000012", location="Ikeja, Lagos")
        unverified = make_donor(phone="08000000013", phone_verified_at=None)
        recent = make_donor(phone="08000000014", last_donation=date.today())

        for donor in (wrong_type, wrong_place, unverified, recent):
            self.assertEqual(volunteer_for_request(donor, self.blood_request), (None, "refused"))

        self.assertEqual(DonorAlert.objects.count(), 0)

    def test_a_closed_request_refuses_volunteers(self):
        BloodRequest.objects.filter(pk=self.blood_request.pk).update(status="fulfilled")
        self.blood_request.refresh_from_db()

        self.assertEqual(volunteer_for_request(self.donor, self.blood_request)[1], "refused")

    def test_you_cannot_volunteer_for_your_own_request(self):
        own = make_request(requester_phone=self.donor.phone)

        self.assertEqual(volunteer_for_request(self.donor, own)[1], "refused")

    def test_a_later_dispatch_skips_volunteers(self):
        volunteer_for_request(self.donor, self.blood_request)

        self.assertNotIn(self.donor, donors_to_alert(self.blood_request))

    def test_counts_report_alerted_and_volunteered_separately(self):
        volunteer_for_request(self.donor, self.blood_request)
        other = make_donor(phone="08000000020")
        DonorAlert.objects.create(blood_request=self.blood_request, donor=other)

        counts = request_counts(self.blood_request)

        self.assertEqual((counts["alerted"], counts["volunteered"], counts["available"]), (1, 1, 1))

    def test_the_view_creates_the_volunteer_alert_for_the_logged_in_donor(self):
        user = User.objects.create_user(username=self.donor.phone, password="x")
        Donor.objects.filter(pk=self.donor.pk).update(user=user)
        client = Client()
        client.force_login(user)

        client.post(reverse("blood_requests:volunteer", kwargs={"pk": self.blood_request.pk}))

        self.assertEqual(DonorAlert.objects.get().source, "volunteer")

    def test_the_view_refuses_a_user_without_a_donor_profile(self):
        client = Client()
        client.force_login(User.objects.create_user(username="nodonor", password="x"))

        client.post(reverse("blood_requests:volunteer", kwargs={"pk": self.blood_request.pk}))

        self.assertEqual(DonorAlert.objects.count(), 0)


class DonationTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="owner", password="x")
        self.blood_request = make_request(requester_user=self.owner)
        self.user = User.objects.create_user(username="08000000000", password="x")
        self.donor = make_donor(user=self.user)
        self.alert = DonorAlert.objects.create(
            blood_request=self.blood_request, donor=self.donor, response="available"
        )
        self.requester = Client()
        self.requester.force_login(self.owner)
        self.donor_client = Client()
        self.donor_client.force_login(self.user)

    def _record(self):
        return self.requester.post(reverse(
            "blood_requests:record_donation",
            kwargs={"token": self.blood_request.status_token, "alert_id": self.alert.pk},
        ))

    def test_recording_does_not_change_the_waiting_period(self):
        self._record()

        donation = Donation.objects.get()
        self.assertFalse(donation.is_confirmed)
        self.donor.refresh_from_db()
        self.assertIsNone(self.donor.last_donation)
        self.assertIn(self.donor, eligible_donors("O-"))

    def test_recording_twice_makes_one_record(self):
        self._record()
        self._record()

        self.assertEqual(Donation.objects.count(), 1)

    def test_only_a_responding_available_donor_can_be_recorded(self):
        self.alert.response = "not_available"
        self.alert.save()

        self._record()

        self.assertEqual(Donation.objects.count(), 0)

    def test_confirming_starts_the_wait_and_removes_the_donor_from_matching(self):
        self._record()

        self.donor_client.post(reverse("blood_requests:confirm_donation", kwargs={"pk": Donation.objects.get().pk}),
                               {"decision": "confirm"})

        self.donor.refresh_from_db()
        self.assertEqual(self.donor.last_donation, timezone.localdate())
        self.assertNotIn(self.donor, eligible_donors("O-"))

    def test_the_donor_returns_on_the_correct_day(self):
        self._record()
        self.donor_client.post(reverse("blood_requests:confirm_donation", kwargs={"pk": Donation.objects.get().pk}),
                               {"decision": "confirm"})
        donated = timezone.localdate()

        before = donated + timedelta(days=ELIGIBILITY_DAYS - 1)
        on_day = donated + timedelta(days=ELIGIBILITY_DAYS)

        self.assertNotIn(self.donor, eligible_donors("O-", as_of=before))
        self.assertIn(self.donor, eligible_donors("O-", as_of=on_day))

    def test_a_confirmation_never_moves_last_donation_backwards(self):
        Donor.objects.filter(pk=self.donor.pk).update(last_donation=date.today())
        old = Donation.objects.create(
            donor=Donor.objects.get(pk=self.donor.pk), donated_on=date.today() - timedelta(days=30)
        )

        old.confirm()

        self.donor.refresh_from_db()
        self.assertEqual(self.donor.last_donation, date.today())

    def test_another_donor_cannot_confirm_it(self):
        self._record()
        stranger_user = User.objects.create_user(username="08000000077", password="x")
        make_donor(phone="08000000077", user=stranger_user)
        stranger = Client()
        stranger.force_login(stranger_user)

        response = stranger.post(reverse("blood_requests:confirm_donation", kwargs={"pk": Donation.objects.get().pk}),
                                 {"decision": "confirm"})

        self.assertEqual(response.status_code, 404)
        self.assertFalse(Donation.objects.get().is_confirmed)

    def test_rejecting_removes_the_claim_and_changes_nothing(self):
        self._record()

        self.donor_client.post(reverse("blood_requests:confirm_donation", kwargs={"pk": Donation.objects.get().pk}),
                               {"decision": "reject"})

        self.assertEqual(Donation.objects.count(), 0)
        self.donor.refresh_from_db()
        self.assertIsNone(self.donor.last_donation)

    def test_the_dashboard_shows_history_and_pending_confirmations(self):
        self._record()
        self.assertContains(self.donor_client.get(reverse("donor_dashboard")), "Confirm your donation")

        Donation.objects.get().confirm()
        page = self.donor_client.get(reverse("donor_dashboard"))

        self.assertContains(page, "Donation history")
        self.assertNotContains(page, "Confirm your donation")


class EligibilityReminderTests(TestCase):
    def test_a_reminder_is_sent_once_on_the_day_the_wait_ends(self):
        today = date(2026, 10, 9)
        donor = make_donor(last_donation=today - timedelta(days=ELIGIBILITY_DAYS))

        self.assertEqual(send_eligibility_reminders(today=today, base_url="https://x.test"), 1)
        self.assertEqual(send_eligibility_reminders(today=today, base_url="https://x.test"), 0)
        self.assertEqual(send_eligibility_reminders(today=today + timedelta(days=1)), 0)

        donor.refresh_from_db()
        self.assertEqual(donor.eligible_reminder_sent_for, donor.last_donation)

    def test_not_yet_eligible_and_long_ago_donors_are_skipped(self):
        today = date(2026, 10, 9)
        make_donor(phone="08000000031", last_donation=today - timedelta(days=ELIGIBILITY_DAYS - 1))
        make_donor(phone="08000000032", last_donation=today - timedelta(days=ELIGIBILITY_DAYS + 200))

        self.assertEqual(send_eligibility_reminders(today=today), 0)

    def test_a_missed_run_is_caught_up_within_the_window(self):
        today = date(2026, 10, 9)
        make_donor(last_donation=today - timedelta(days=ELIGIBILITY_DAYS + 3))

        self.assertEqual(send_eligibility_reminders(today=today), 1)

    def test_a_new_donation_starts_a_new_cycle(self):
        today = date(2026, 10, 9)
        donor = make_donor(last_donation=today - timedelta(days=ELIGIBILITY_DAYS))
        send_eligibility_reminders(today=today)

        later = today + timedelta(days=ELIGIBILITY_DAYS)
        Donor.objects.filter(pk=donor.pk).update(last_donation=today)

        self.assertEqual(send_eligibility_reminders(today=later), 1)

    def test_unavailable_unverified_and_opted_out_donors_are_not_texted(self):
        today = date(2026, 10, 9)
        last = today - timedelta(days=ELIGIBILITY_DAYS)
        make_donor(phone="08000000041", last_donation=last, availability=False)
        make_donor(phone="08000000042", last_donation=last, phone_verified_at=None)
        make_donor(phone="08000000043", last_donation=last, sms_opt_out=True)

        self.assertEqual(send_eligibility_reminders(today=today), 0)

    def test_the_command_runs(self):
        out = StringIO()

        call_command("send_eligibility_reminders", stdout=out)

        self.assertIn("Reminders sent: 0", out.getvalue())


class OwnershipTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="x")
        self.bob = User.objects.create_user(username="bob", password="x")

    def _post_request(self, client):
        return client.post(reverse("blood_requests:blood_request_create"), {
            "requester_name": "Alice", "requester_phone": "08011112222",
            "recipient_blood_type": "O-", "urgency": "urgent", "units_needed": 1,
            "state": "Benue", "lga": "Makurdi",
        })

    def test_a_new_request_belongs_to_the_account_that_posted_it(self):
        client = Client()
        client.force_login(self.alice)

        self._post_request(client)

        self.assertEqual(BloodRequest.objects.get().requester_user, self.alice)

    def test_logging_in_elsewhere_shows_your_requests(self):
        first = Client()
        first.force_login(self.alice)
        self._post_request(first)

        other_device = Client()  # fresh session, no remembered tokens
        other_device.force_login(self.alice)
        mine = other_device.get(reverse("blood_requests:blood_request_list")).context["mine"]

        self.assertEqual(list(mine), list(BloodRequest.objects.all()))

    def test_another_users_request_is_not_in_your_list(self):
        make_request(requester_user=self.alice)
        client = Client()
        client.force_login(self.bob)

        self.assertEqual(list(client.get(reverse("blood_requests:blood_request_list")).context["mine"]), [])

    def test_the_owner_can_open_it_by_id_and_others_cannot(self):
        blood_request = make_request(requester_user=self.alice)
        owner, other = Client(), Client()
        owner.force_login(self.alice)
        other.force_login(self.bob)
        url = reverse("blood_requests:blood_request_manage_by_id", kwargs={"pk": blood_request.pk})

        self.assertEqual(owner.get(url).status_code, 302)
        self.assertEqual(other.get(url).status_code, 404)

    def test_staff_can_open_any_request_by_id(self):
        blood_request = make_request(requester_user=self.alice)
        staff = Client()
        staff.force_login(User.objects.create_user(username="s", password="x", is_staff=True))

        url = reverse("blood_requests:blood_request_manage_by_id", kwargs={"pk": blood_request.pk})

        self.assertEqual(staff.get(url).status_code, 302)

    def test_a_token_holder_can_still_open_it_by_default(self):
        blood_request = make_request(requester_user=self.alice)
        client = Client()
        client.force_login(self.bob)

        url = reverse("blood_requests:blood_request_manage", kwargs={"token": blood_request.status_token})

        self.assertEqual(client.get(url).status_code, 200)

    @override_settings(MANAGE_TOKEN_LINK_ENABLED=False)
    def test_with_token_links_off_only_the_owner_and_staff_get_in(self):
        blood_request = make_request(requester_user=self.alice)
        url = reverse("blood_requests:blood_request_manage", kwargs={"token": blood_request.status_token})
        bob, alice = Client(), Client()
        bob.force_login(self.bob)
        alice.force_login(self.alice)

        self.assertEqual(bob.get(url).status_code, 404)
        self.assertEqual(alice.get(url).status_code, 200)

    @override_settings(MANAGE_TOKEN_LINK_ENABLED=False)
    def test_old_ownerless_requests_still_open_by_token(self):
        blood_request = make_request()  # requester_user is None
        client = Client()
        client.force_login(self.bob)

        url = reverse("blood_requests:blood_request_manage", kwargs={"token": blood_request.status_token})

        self.assertEqual(client.get(url).status_code, 200)
