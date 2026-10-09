from datetime import date, timedelta

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .forms import DonorSearchForm, RegisterForm
from .locations import NIGERIA, compose_location, lgas_for, split_location
from .models import Donor

# The official number of local government areas in each state and the FCT.
OFFICIAL_LGA_COUNTS = {
    "Abia": 17, "Abuja": 6, "Adamawa": 21, "Akwa Ibom": 31, "Anambra": 21,
    "Bauchi": 20, "Bayelsa": 8, "Benue": 23, "Borno": 27, "Cross River": 18,
    "Delta": 25, "Ebonyi": 13, "Edo": 18, "Ekiti": 16, "Enugu": 17,
    "Gombe": 11, "Imo": 27, "Jigawa": 27, "Kaduna": 23, "Kano": 44,
    "Katsina": 34, "Kebbi": 21, "Kogi": 21, "Kwara": 16, "Lagos": 20,
    "Nasarawa": 13, "Niger": 25, "Ogun": 20, "Ondo": 18, "Osun": 30,
    "Oyo": 33, "Plateau": 17, "Rivers": 23, "Sokoto": 23, "Taraba": 16,
    "Yobe": 17, "Zamfara": 14,
}


class LocationDataTests(TestCase):
    def test_every_state_has_the_official_number_of_lgas(self):
        self.assertEqual(
            {state: len(lgas) for state, lgas in NIGERIA.items()},
            OFFICIAL_LGA_COUNTS,
        )

    def test_there_are_774_lgas_in_37_places(self):
        self.assertEqual(len(NIGERIA), 37)
        self.assertEqual(sum(len(lgas) for lgas in NIGERIA.values()), 774)

    def test_no_lga_is_listed_twice_within_a_state(self):
        for state, lgas in NIGERIA.items():
            self.assertEqual(len(lgas), len(set(lgas)), state)

    def test_no_name_contains_a_comma(self):
        # The stored form is "<LGA>, <State>", so a comma would break parsing.
        for state, lgas in NIGERIA.items():
            for name in [state, *lgas]:
                self.assertNotIn(",", name)

    def test_compose_location(self):
        self.assertEqual(compose_location("Lagos", "Ikeja"), "Ikeja, Lagos")
        self.assertEqual(compose_location("Lagos", ""), "Lagos")
        self.assertEqual(compose_location("", ""), "")

    def test_split_reads_back_what_compose_wrote(self):
        for state, lgas in NIGERIA.items():
            for lga in lgas:
                self.assertEqual(
                    split_location(compose_location(state, lga)), (state, lga)
                )

    def test_split_understands_older_free_text(self):
        self.assertEqual(split_location("Otukpo"), ("Benue", "Otukpo"))
        self.assertEqual(split_location("Benue Otukpo"), ("Benue", "Otukpo"))
        self.assertEqual(split_location("Enugu"), ("Enugu", ""))

    def test_split_will_not_guess_an_ambiguous_lga(self):
        # Surulere exists in both Lagos and Oyo.
        self.assertEqual(split_location("Surulere"), ("", ""))
        self.assertEqual(split_location("nowhere in particular"), ("", ""))

    def test_unknown_state_has_no_lgas(self):
        self.assertEqual(lgas_for("Atlantis"), [])


class RegisterTests(TestCase):
    def _payload(self, **overrides):
        values = {
            "name": "Test Donor",
            "phone": "08031234567",
            "state": "Lagos",
            "lga": "Ikeja",
            "blood_type": "O+",
            "genotype": "AA",
            "donation_status": "never",
            "last_donation": "",
            "availability": "True",
            "password1": "Sup3rSecret!x9",
            "password2": "Sup3rSecret!x9",
        }
        values.update(overrides)
        return values

    def test_signing_up_as_a_first_time_donor(self):
        response = self.client.post(reverse("register_donor"), self._payload())

        self.assertRedirects(response, reverse("verify_phone"))
        donor = Donor.objects.get()
        self.assertEqual(donor.location, "Ikeja, Lagos")
        self.assertIsNone(donor.last_donation)

    def test_signing_up_having_donated_before(self):
        # The reported bug: choosing "donated before" made sign-up impossible.
        past = (date.today() - timedelta(days=200)).isoformat()

        response = self.client.post(
            reverse("register_donor"),
            self._payload(donation_status="before", last_donation=past),
        )

        self.assertRedirects(response, reverse("verify_phone"))
        self.assertEqual(Donor.objects.get().last_donation.isoformat(), past)

    def test_donated_before_still_needs_a_date(self):
        response = self.client.post(
            reverse("register_donor"),
            self._payload(donation_status="before", last_donation=""),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Please enter your last donation date.")
        self.assertEqual(Donor.objects.count(), 0)

    def test_a_leftover_date_is_ignored_when_never_donated(self):
        self.client.post(
            reverse("register_donor"),
            self._payload(donation_status="never", last_donation="2025-01-01"),
        )

        self.assertIsNone(Donor.objects.get().last_donation)

    def test_the_page_offers_state_and_lga_dropdowns_not_a_text_box(self):
        response = self.client.get(reverse("register_donor"))

        self.assertContains(response, '<select name="state"')
        self.assertContains(response, '<select name="lga"')
        self.assertNotContains(response, 'name="location"')
        self.assertContains(response, 'id="ng-locations"')

    def test_the_date_toggle_reads_the_radio_group_not_its_wrapper(self):
        # The radios sit inside a <div id="id_donation_status">; reading
        # .value off that div hid the date field permanently.
        response = self.client.get(reverse("register_donor"))

        self.assertContains(response, 'input[name="donation_status"]:checked')
        self.assertNotContains(
            response, 'document.getElementById("id_donation_status")'
        )

    def test_state_is_required(self):
        response = self.client.post(
            reverse("register_donor"), self._payload(state="", lga="")
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Donor.objects.count(), 0)

    def test_lga_is_required(self):
        response = self.client.post(
            reverse("register_donor"), self._payload(lga="")
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Donor.objects.count(), 0)

    def test_an_lga_from_a_different_state_is_rejected(self):
        response = self.client.post(
            reverse("register_donor"), self._payload(state="Lagos", lga="Makurdi")
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Choose a local government area from the list.")
        self.assertEqual(Donor.objects.count(), 0)

    def test_an_invented_state_is_rejected(self):
        form = RegisterForm(self._payload(state="Atlantis", lga="Ikeja"))

        self.assertFalse(form.is_valid())
        self.assertIn("state", form.errors)

    def test_a_failed_submit_keeps_the_chosen_lga_list(self):
        response = self.client.post(
            reverse("register_donor"), self._payload(phone="bad", state="Benue", lga="Makurdi")
        )

        self.assertContains(response, '<option value="Makurdi" selected>')
        self.assertContains(response, '<option value="Otukpo">')
        self.assertNotContains(response, '<option value="Ikeja">')


class EditProfileLocationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="08031234567", password="x")
        self.donor = Donor.objects.create(
            user=self.user, name="Ada", blood_type="B+", genotype="AA",
            location="Otukpo", phone="08031234567",
            phone_verified_at=timezone.now(),
        )
        self.client.force_login(self.user)

    def test_older_free_text_is_preselected_in_the_dropdowns(self):
        response = self.client.get(reverse("edit_donor"))

        self.assertContains(response, '<option value="Benue" selected>')
        self.assertContains(response, '<option value="Otukpo" selected>')

    def test_saving_rewrites_the_location_in_the_standard_form(self):
        response = self.client.post(reverse("edit_donor"), {
            "name": "Ada", "phone": "08031234567", "state": "Benue",
            "lga": "Otukpo", "blood_type": "B+", "genotype": "AA",
            "donation_status": "never", "availability": "True",
        })

        self.assertRedirects(response, reverse("donor_dashboard"))
        self.donor.refresh_from_db()
        self.assertEqual(self.donor.location, "Otukpo, Benue")


class SearchLocationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="08000000001", password="x")
        self.client.force_login(self.user)
        for i, (name, location) in enumerate([
            ("Ikeja Ada", "Ikeja, Lagos"),
            ("Yaba Bola", "Yaba, Lagos"),  # legacy free-text style
            ("Makurdi Chi", "Makurdi, Benue"),
        ]):
            Donor.objects.create(
                name=name, blood_type="O+", genotype="AA", location=location,
                phone=f"0800000010{i}",
                phone_verified_at=timezone.now(),
            )

    def _names(self, **params):
        response = self.client.get(reverse("search_donors"), params)
        return {d.name for d in response.context["donors"]}

    def test_search_by_state_finds_every_donor_in_it(self):
        self.assertEqual(self._names(state="Lagos"), {"Ikeja Ada", "Yaba Bola"})

    def test_search_by_state_and_lga_narrows_it(self):
        self.assertEqual(self._names(state="Lagos", lga="Ikeja"), {"Ikeja Ada"})

    def test_search_without_a_location_returns_everyone_eligible(self):
        self.assertEqual(len(self._names()), 3)

    def test_the_location_filter_is_optional(self):
        form = DonorSearchForm({"state": "", "lga": ""})

        self.assertTrue(form.is_valid())
        self.assertEqual(form.cleaned_data["location"], "")

    def test_the_search_page_has_dropdowns(self):
        response = self.client.get(reverse("search_donors"))

        self.assertContains(response, '<select name="state"')
        self.assertContains(response, "Any LGA in this state")
