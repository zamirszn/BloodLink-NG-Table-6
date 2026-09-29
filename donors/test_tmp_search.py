"""TEMPORARY verification test for the /search/ fixes. Delete after running."""

from datetime import date, timedelta

from django.test import TestCase

from .models import Donor


class SearchEligibilityTests(TestCase):

    def setUp(self):
        self.never_donated = Donor.objects.create(
            name="Never Donated", blood_type="O+", genotype="AA",
            location="Enugu", phone="0801", availability=True,
        )
        self.long_ago = Donor.objects.create(
            name="Donated 200 Days Ago", blood_type="O+", genotype="AA",
            location="Enugu", phone="0802", availability=True,
            last_donation=date.today() - timedelta(days=200),
        )
        self.recent = Donor.objects.create(
            name="Donated 10 Days Ago", blood_type="O+", genotype="AA",
            location="Enugu", phone="0803", availability=True,
            last_donation=date.today() - timedelta(days=10),
        )
        self.exactly_90 = Donor.objects.create(
            name="Donated Exactly 90 Days Ago", blood_type="O+", genotype="AA",
            location="Enugu", phone="0804", availability=True,
            last_donation=date.today() - timedelta(days=90),
        )

    # ---- 1 & 2: no-parameter search still applies the 90-day rule ----

    def test_bare_search_excludes_recent_donor(self):
        response = self.client.get("/search/")
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Donated 10 Days Ago")

    def test_bare_search_includes_never_donated_and_old_donors(self):
        response = self.client.get("/search/")
        self.assertContains(response, "Never Donated")
        self.assertContains(response, "Donated 200 Days Ago")

    def test_bare_search_includes_exactly_90_days_boundary(self):
        response = self.client.get("/search/")
        self.assertContains(response, "Donated Exactly 90 Days Ago")

    def test_bare_search_count_is_three(self):
        response = self.client.get("/search/")
        self.assertContains(response, "3 eligible donors found.")

    # ---- 2: eligibility also holds when filters ARE supplied ----

    def test_eligibility_still_applies_with_filters(self):
        response = self.client.get("/search/", {"blood_type": "O+"})
        self.assertContains(response, "Never Donated")
        self.assertNotContains(response, "Donated 10 Days Ago")

    def test_eligibility_applies_with_location_filter(self):
        response = self.client.get("/search/", {"location": "Enugu"})
        self.assertContains(response, "Never Donated")
        self.assertNotContains(response, "Donated 10 Days Ago")

    # ---- 3: availability filter still works ----

    def test_availability_filter_true(self):
        Donor.objects.create(
            name="Unavailable Donor", blood_type="A+", genotype="AA",
            location="Lagos", phone="0805", availability=False,
        )
        response = self.client.get("/search/", {"availability": "True"})
        self.assertContains(response, "Never Donated")
        self.assertNotContains(response, "Unavailable Donor")

    def test_availability_filter_false(self):
        Donor.objects.create(
            name="Unavailable Donor", blood_type="A+", genotype="AA",
            location="Lagos", phone="0805", availability=False,
        )
        response = self.client.get("/search/", {"availability": "False"})
        self.assertNotContains(response, "Never Donated")
        self.assertContains(response, "Unavailable Donor")

    # ---- 4: location icontains still works ----

    def test_location_is_case_insensitive_icontains(self):
        response = self.client.get("/search/", {"location": "enu"})
        self.assertContains(response, "Never Donated")

    def test_location_excludes_non_matching(self):
        response = self.client.get("/search/", {"location": "Kano"})
        self.assertNotContains(response, "Never Donated")
        self.assertContains(response, "No eligible donors matched your search.")

    # ---- 5: availability shown from the real value ----

    def test_unavailable_donor_renders_no(self):
        Donor.objects.create(
            name="Unavailable Donor", blood_type="A+", genotype="AA",
            location="Lagos", phone="0805", availability=False,
        )
        response = self.client.get("/search/")
        self.assertContains(response, "<strong>Available:</strong> No")
        self.assertContains(response, "<strong>Available:</strong> Yes")

    # ---- 6: empty message ----

    def test_empty_result_message(self):
        response = self.client.get("/search/", {"blood_type": "AB-"})
        self.assertContains(response, "No eligible donors matched your search.")

    def test_no_result_count_line_when_empty(self):
        response = self.client.get("/search/", {"blood_type": "AB-"})
        self.assertNotContains(response, "eligible donors found.")
        self.assertNotContains(response, "eligible donor found.")

    # ---- 7: singular result count ----

    def test_singular_count_wording(self):
        Donor.objects.exclude(pk=self.never_donated.pk).delete()
        response = self.client.get("/search/")
        self.assertContains(response, "1 eligible donor found.")
