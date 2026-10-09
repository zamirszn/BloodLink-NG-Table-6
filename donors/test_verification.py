"""F1 and F2: one-time codes, phone verification, SMS password reset."""

from datetime import timedelta
from unittest import mock

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from blood_requests.matching import eligible_donors
from blood_requests.models import BloodRequest

from . import otp
from .models import Donor, PhoneOTP
from .verification import verified_q

CODE = "123456"
PHONE = "08031234567"
GOOD_PASSWORD = "Sup3rSecret!x9"


def fixed_code():
    return mock.patch("donors.otp._generate_code", return_value=CODE)


class OTPServiceTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_issuing_stores_a_hash_never_the_code(self):
        with fixed_code():
            self.assertEqual(otp.issue_code(PHONE, PhoneOTP.Purpose.SIGNUP), otp.SENT)

        row = PhoneOTP.objects.get()
        self.assertNotIn(CODE, row.code_hash)
        self.assertEqual(len(row.code_hash), 64)

    def test_a_correct_code_verifies_once_only(self):
        with fixed_code():
            otp.issue_code(PHONE, PhoneOTP.Purpose.SIGNUP)

        self.assertEqual(otp.verify_code(PHONE, PhoneOTP.Purpose.SIGNUP, CODE), otp.OK)
        self.assertEqual(otp.verify_code(PHONE, PhoneOTP.Purpose.SIGNUP, CODE), otp.NONE)

    def test_a_code_is_bound_to_its_purpose_and_phone(self):
        with fixed_code():
            otp.issue_code(PHONE, PhoneOTP.Purpose.SIGNUP)

        self.assertEqual(otp.verify_code(PHONE, PhoneOTP.Purpose.RESET, CODE), otp.NONE)
        self.assertEqual(otp.verify_code("08099999999", PhoneOTP.Purpose.SIGNUP, CODE), otp.NONE)

    def test_five_wrong_guesses_lock_the_code_even_for_the_right_one(self):
        with fixed_code():
            otp.issue_code(PHONE, PhoneOTP.Purpose.SIGNUP)

        for _ in range(4):
            self.assertEqual(otp.verify_code(PHONE, PhoneOTP.Purpose.SIGNUP, "000000"), otp.WRONG)

        self.assertEqual(otp.verify_code(PHONE, PhoneOTP.Purpose.SIGNUP, "000000"), otp.LOCKED)
        self.assertEqual(otp.verify_code(PHONE, PhoneOTP.Purpose.SIGNUP, CODE), otp.LOCKED)

    def test_a_code_expires_after_ten_minutes(self):
        start = timezone.now()

        with fixed_code():
            otp.issue_code(PHONE, PhoneOTP.Purpose.SIGNUP, now=start)

        late = start + timedelta(minutes=10, seconds=1)
        self.assertEqual(otp.verify_code(PHONE, PhoneOTP.Purpose.SIGNUP, CODE, now=late), otp.EXPIRED)

        early = start + timedelta(minutes=9)
        self.assertEqual(otp.verify_code(PHONE, PhoneOTP.Purpose.SIGNUP, CODE, now=early), otp.OK)

    def test_resend_is_blocked_for_sixty_seconds(self):
        start = timezone.now()

        with fixed_code():
            self.assertEqual(otp.issue_code(PHONE, PhoneOTP.Purpose.SIGNUP, now=start), otp.SENT)
            self.assertEqual(
                otp.issue_code(PHONE, PhoneOTP.Purpose.SIGNUP, now=start + timedelta(seconds=30)),
                otp.COOLDOWN,
            )
            self.assertEqual(
                otp.issue_code(PHONE, PhoneOTP.Purpose.SIGNUP, now=start + timedelta(seconds=61)),
                otp.SENT,
            )

    def test_a_resent_code_invalidates_the_previous_one(self):
        start = timezone.now()

        with mock.patch("donors.otp._generate_code", return_value="111111"):
            otp.issue_code(PHONE, PhoneOTP.Purpose.SIGNUP, now=start)
        with mock.patch("donors.otp._generate_code", return_value="222222"):
            otp.issue_code(PHONE, PhoneOTP.Purpose.SIGNUP, now=start + timedelta(seconds=61))

        self.assertEqual(PhoneOTP.objects.count(), 1)
        self.assertEqual(otp.verify_code(PHONE, PhoneOTP.Purpose.SIGNUP, "111111"), otp.WRONG)
        self.assertEqual(otp.verify_code(PHONE, PhoneOTP.Purpose.SIGNUP, "222222"), otp.OK)

    @override_settings(OTP_MAX_SENDS_PER_PHONE_PER_HOUR=2, OTP_RESEND_COOLDOWN_SECONDS=0)
    def test_sends_are_throttled_per_phone(self):
        results = [otp.issue_code(PHONE, PhoneOTP.Purpose.SIGNUP) for _ in range(3)]

        self.assertEqual(results, [otp.SENT, otp.SENT, otp.THROTTLED])

    @override_settings(OTP_MAX_SENDS_PER_IP_PER_HOUR=1, OTP_RESEND_COOLDOWN_SECONDS=0)
    def test_sends_are_throttled_per_ip(self):
        first = otp.issue_code("08031111111", PhoneOTP.Purpose.SIGNUP, ip="1.2.3.4")
        second = otp.issue_code("08032222222", PhoneOTP.Purpose.SIGNUP, ip="1.2.3.4")

        self.assertEqual((first, second), (otp.SENT, otp.THROTTLED))


class SignupVerificationTests(TestCase):
    def setUp(self):
        cache.clear()

    def _register(self, client=None):
        client = client or self.client

        with fixed_code():
            return client.post(reverse("register_donor"), {
                "name": "Test Donor", "phone": PHONE, "state": "Lagos", "lga": "Ikeja",
                "blood_type": "O+", "genotype": "AA", "donation_status": "never",
                "last_donation": "", "availability": "True",
                "password1": GOOD_PASSWORD, "password2": GOOD_PASSWORD,
            })

    def test_registering_sends_a_code_and_leaves_the_donor_unverified(self):
        response = self._register()

        self.assertRedirects(response, reverse("verify_phone"))
        donor = Donor.objects.get()
        self.assertIsNone(donor.phone_verified_at)
        self.assertEqual(PhoneOTP.objects.filter(phone=PHONE).count(), 1)

    def test_an_unverified_donor_is_not_matched_or_searchable(self):
        self._register()
        donor = Donor.objects.get()

        self.assertNotIn(donor, eligible_donors("O+"))

        viewer = User.objects.create_user(username="08000000009", password="x")
        other = Client()
        other.force_login(viewer)
        shown = other.get(reverse("search_donors")).context["donors"]
        self.assertNotIn(donor, shown)

    def test_entering_the_code_verifies_and_makes_the_donor_visible(self):
        self._register()

        response = self.client.post(reverse("verify_phone"), {"code": CODE})

        self.assertRedirects(response, reverse("donor_dashboard"))
        donor = Donor.objects.get()
        self.assertIsNotNone(donor.phone_verified_at)
        self.assertIn(donor, eligible_donors("O+"))

    def test_a_wrong_code_does_not_verify(self):
        self._register()

        response = self.client.post(reverse("verify_phone"), {"code": "000000"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "not correct")
        self.assertIsNone(Donor.objects.get().phone_verified_at)

    def test_the_dashboard_prompts_an_unverified_donor(self):
        self._register()

        self.assertContains(self.client.get(reverse("donor_dashboard")), "Verify your phone number")

    def test_changing_the_phone_number_requires_verifying_again(self):
        self._register()
        self.client.post(reverse("verify_phone"), {"code": CODE})

        response = self.client.post(reverse("edit_donor"), {
            "name": "Test Donor", "phone": "08035550000", "state": "Lagos", "lga": "Ikeja",
            "blood_type": "O+", "genotype": "AA", "donation_status": "never",
            "availability": "True",
        })

        self.assertRedirects(response, reverse("verify_phone"))
        self.assertIsNone(Donor.objects.get().phone_verified_at)


class GracePeriodTests(TestCase):
    def _donor(self, **kw):
        values = dict(name="Old", blood_type="O+", genotype="AA", location="Ikeja, Lagos", phone="08030000001")
        values.update(kw)
        return Donor.objects.create(**values)

    def test_legacy_donors_stay_visible_until_the_grace_period_ends(self):
        legacy = self._donor(verification_grace=True)
        fresh = self._donor(phone="08030000002")

        with override_settings(PHONE_VERIFICATION_GRACE_ENDS=""):
            ids = set(Donor.objects.filter(verified_q()).values_list("pk", flat=True))
        self.assertEqual(ids, {legacy.pk})

        yesterday = (timezone.localdate() - timedelta(days=1)).isoformat()
        with override_settings(PHONE_VERIFICATION_GRACE_ENDS=yesterday):
            self.assertFalse(Donor.objects.filter(verified_q()).exists())

        self.assertNotIn(fresh.pk, ids)


class PasswordResetTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username=PHONE, password="Old-Passw0rd!x")
        Donor.objects.create(
            user=self.user, name="Ada", blood_type="O+", genotype="AA",
            location="Ikeja, Lagos", phone=PHONE,
        )

    def _request(self, phone, client=None):
        client = client or self.client

        with fixed_code():
            return client.post(reverse("password_reset_request"), {"phone": phone})

    def test_known_and_unknown_numbers_get_the_same_response(self):
        known = self._request(PHONE, Client())
        unknown = self._request("08037777777", Client())

        self.assertEqual(known.status_code, unknown.status_code)
        self.assertEqual(known["Location"], unknown["Location"])
        self.assertEqual(PhoneOTP.objects.filter(purpose="reset").count(), 1)

    def test_the_whole_flow_changes_the_password(self):
        self._request(PHONE)

        response = self.client.post(reverse("password_reset_confirm"), {
            "code": CODE, "password1": GOOD_PASSWORD, "password2": GOOD_PASSWORD,
        })

        self.assertRedirects(response, reverse("login"))
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(GOOD_PASSWORD))

    def test_a_code_cannot_be_reused(self):
        self._request(PHONE)
        data = {"code": CODE, "password1": GOOD_PASSWORD, "password2": GOOD_PASSWORD}
        self.client.post(reverse("password_reset_confirm"), data)

        # The session phone is still set; the code is gone.
        second = self.client.post(reverse("password_reset_confirm"), {
            **data, "password1": "An0ther-Passw0rd!", "password2": "An0ther-Passw0rd!",
        })

        self.assertEqual(second.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(GOOD_PASSWORD))

    def test_a_weak_password_is_rejected_without_burning_the_code(self):
        self._request(PHONE)

        weak = self.client.post(reverse("password_reset_confirm"), {
            "code": CODE, "password1": "12345678", "password2": "12345678",
        })
        self.assertEqual(weak.status_code, 200)

        good = self.client.post(reverse("password_reset_confirm"), {
            "code": CODE, "password1": GOOD_PASSWORD, "password2": GOOD_PASSWORD,
        })
        self.assertRedirects(good, reverse("login"))

    def test_a_wrong_code_for_an_unknown_number_looks_like_one_for_a_known_number(self):
        self._request("08037777777")
        unknown = self.client.post(reverse("password_reset_confirm"), {
            "code": "000000", "password1": GOOD_PASSWORD, "password2": GOOD_PASSWORD,
        })

        other = Client()
        self._request(PHONE, other)
        known = other.post(reverse("password_reset_confirm"), {
            "code": "000000", "password1": GOOD_PASSWORD, "password2": GOOD_PASSWORD,
        })

        self.assertContains(unknown, "incorrect or has expired")
        self.assertContains(known, "incorrect or has expired")

    def test_other_sessions_are_ended_after_a_reset(self):
        old_session = Client()
        old_session.login(username=PHONE, password="Old-Passw0rd!x")
        self.assertEqual(old_session.get(reverse("donor_dashboard")).status_code, 200)

        self._request(PHONE)
        self.client.post(reverse("password_reset_confirm"), {
            "code": CODE, "password1": GOOD_PASSWORD, "password2": GOOD_PASSWORD,
        })

        response = old_session.get(reverse("donor_dashboard"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response["Location"])

    def test_confirm_without_a_started_reset_goes_back_to_the_start(self):
        response = Client().get(reverse("password_reset_confirm"))

        self.assertRedirects(response, reverse("password_reset_request"))

    @override_settings(OTP_RESEND_COOLDOWN_SECONDS=0)
    def test_the_reset_request_is_throttled_by_phone(self):
        for _ in range(6):
            self._request(PHONE, Client())

        self.assertLessEqual(PhoneOTP.objects.filter(purpose="reset").count(), 1)
        # Six asks produced at most five send attempts; the sixth was silent.
        self.assertEqual(self._request(PHONE, Client()).status_code, 302)

    def test_the_login_page_links_to_the_reset(self):
        self.assertContains(self.client.get(reverse("login")), reverse("password_reset_request"))


class RequesterVerificationTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username="08060000000", password="x")
        self.client.force_login(self.user)
        self.blood_request = BloodRequest.objects.create(
            requester_name="Grace", requester_phone="08011112222",
            recipient_blood_type="O-", location="Makurdi",
        )

    def test_an_unverified_requester_cannot_alert_donors(self):
        response = self.client.post(reverse(
            "blood_requests:blood_request_notify",
            kwargs={"token": self.blood_request.status_token},
        ))

        self.assertRedirects(
            response,
            reverse("blood_requests:requester_verify", kwargs={"token": self.blood_request.status_token}),
        )
        self.assertEqual(self.blood_request.alerts.count(), 0)

    def test_verifying_unlocks_alerting(self):
        url = reverse("blood_requests:requester_verify", kwargs={"token": self.blood_request.status_token})

        with fixed_code():
            self.client.post(url, {"action": "send"})
        self.client.post(url, {"code": CODE})

        self.blood_request.refresh_from_db()
        self.assertIsNotNone(self.blood_request.requester_phone_verified_at)

    def test_a_verified_donor_posting_their_own_number_is_verified_at_once(self):
        Donor.objects.create(
            user=self.user, name="Me", blood_type="A+", genotype="AA", location="Ikeja, Lagos",
            phone="08060000000", phone_verified_at=timezone.now(),
        )

        self.client.post(reverse("blood_requests:blood_request_create"), {
            "requester_name": "Me", "requester_phone": "08060000000",
            "recipient_blood_type": "A+", "urgency": "urgent", "units_needed": 1,
            "state": "Lagos", "lga": "Ikeja",
        })

        created = BloodRequest.objects.get(requester_phone="08060000000")
        self.assertIsNotNone(created.requester_phone_verified_at)
