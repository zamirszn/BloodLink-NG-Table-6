"""TEMPORARY verification test for blood-type compatibility search.

Covers the compatibility helper, the search view integration, and that
availability / location / 90-day filters still compose with it.
"""

from datetime import date, timedelta

from django.test import TestCase

from .compatibility import ALL_BLOOD_TYPES, compatible_donor_types
from .models import Donor


class CompatibilityMappingTests(TestCase):

    EXPECTED = {
        "A+": {"A+", "A-", "O+", "O-"},
        "A-": {"A-", "O-"},
        "B+": {"B+", "B-", "O+", "O-"},
        "B-": {"B-", "O-"},
        "AB+": {"A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-"},
        "AB-": {"AB-", "A-", "B-", "O-"},
        "O+": {"O+", "O-"},
        "O-": {"O-"},
    }

    def test_mapping_matches_abo_rh_rules(self):
        for recipient, expected in self.EXPECTED.items():
            with self.subTest(recipient=recipient):
                self.assertEqual(
                    set(compatible_donor_types(recipient)), expected
                )

    def test_every_blood_type_is_mapable(self):
        self.assertEqual(sorted(self.EXPECTED), sorted(ALL_BLOOD_TYPES))

    def test_recipient_is_always_compatible_with_self(self):
        for recipient in ALL_BLOOD_TYPES:
            self.assertIn(recipient, compatible_donor_types(recipient))

    def test_ab_plus_is_universal_recipient(self):
        self.assertEqual(
            set(compatible_donor_types("AB+")), set(ALL_BLOOD_TYPES)
        )

    def test_o_negative_recipient_only_receives_o_negative(self):
        self.assertEqual(compatible_donor_types("O-"), ["O-"])

    def test_unknown_and_empty_input_return_nothing(self):
        self.assertEqual(compatible_donor_types("XX"), [])
        self.assertEqual(compatible_donor_types(""), [])
        self.assertEqual(compatible_donor_types(None), [])


class CompatibilitySearchViewTests(TestCase):

    def setUp(self):
        # One eligible donor of every blood type, all in Enugu, all available.
        for index, blood_type in enumerate(ALL_BLOOD_TYPES):
            Donor.objects.create(
                name=f"Donor {blood_type}",
                blood_type=blood_type,
                genotype="AA",
                location="Enugu",
                phone=f"080{index}",
                availability=True,
            )

    def _found(self, response):
        return {
            blood_type for blood_type in ALL_BLOOD_TYPES
            if f"Donor {blood_type}" in response.content.decode()
        }

    def _search(self, **params):
        response = self.client.get("/search/", params)
        self.assertEqual(response.status_code, 200)
        return self._found(response)

    def test_search_returns_only_compatible_donors(self):
        for recipient, expected in CompatibilityMappingTests.EXPECTED.items():
            with self.subTest(recipient=recipient):
                self.assertEqual(self._search(blood_type=recipient), expected)

    def test_any_blood_type_applies_no_restriction(self):
        self.assertEqual(self._search(blood_type=""), set(ALL_BLOOD_TYPES))

    def test_recipient_type_is_labeled_on_the_result(self):
        response = self.client.get("/search/", {"blood_type": "A+"})
        self.assertContains(response, "<strong>Compatible with recipient:</strong> A+")
        self.assertContains(
            response,
            "<strong>Location:</strong> Enugu",
        )
        self.assertContains(response, "<strong>Available:</strong> Yes")
        self.assertContains(response, "<strong>Last Donation:</strong> Never donated")

    def test_no_compatibility_line_when_any_blood_type(self):
        response = self.client.get("/search/")
        self.assertNotContains(response, "Compatible with recipient:")

    def test_search_form_label_is_recipient_blood_type(self):
        response = self.client.get("/search/")
        self.assertContains(response, "Recipient Blood Type")
        self.assertContains(response, "Any blood type")

    # ---- compatibility composes with the existing filters ----

    def test_availability_filter_composes_with_compatibility(self):
        Donor.objects.filter(blood_type="O+").update(availability=False)

        found = self._search(blood_type="A+", availability="True")
        self.assertEqual(found, {"A+", "A-", "O-"})

        found = self._search(blood_type="A+", availability="False")
        self.assertEqual(found, {"O+"})

    def test_location_filter_composes_with_compatibility(self):
        Donor.objects.filter(blood_type__in=["O+", "O-"]).update(
            location="Lagos"
        )

        found = self._search(blood_type="A+", location="Lagos")
        self.assertEqual(found, {"O+", "O-"})

        found = self._search(blood_type="A+", location="Enugu")
        self.assertEqual(found, {"A+", "A-"})

    def test_90_day_rule_composes_with_compatibility(self):
        Donor.objects.filter(blood_type="O-").update(
            last_donation=date.today() - timedelta(days=10)
        )
        Donor.objects.filter(blood_type="A-").update(
            last_donation=date.today() - timedelta(days=200)
        )

        # O- donated 10 days ago -> excluded. A- donated 200 days ago -> kept.
        found = self._search(blood_type="A+")
        self.assertEqual(found, {"A+", "A-", "O+"})

        # Same rule with no blood type selected.
        self.assertEqual(self._search(), set(ALL_BLOOD_TYPES) - {"O-"})

    def test_recently_donated_compatible_donor_is_excluded(self):
        Donor.objects.filter(blood_type="A-").update(
            last_donation=date.today() - timedelta(days=5)
        )
        # A+ recipient matches A+, A-, O+ and O-; A- is ineligible.
        found = self._search(blood_type="A+")
        self.assertEqual(found, {"A+", "O+", "O-"})

    def test_recently_donated_donor_leaves_no_match(self):
        Donor.objects.filter(blood_type__in=["A-", "O-"]).update(
            last_donation=date.today() - timedelta(days=5)
        )
        # A- recipient can only receive from A- and O-, both ineligible now.
        found = self._search(blood_type="A-")
        self.assertEqual(found, set())

    def test_all_three_filters_together(self):
        Donor.objects.filter(blood_type="O-").update(availability=False)

        found = self._search(
            blood_type="B+", location="Enugu", availability="True"
        )
        self.assertEqual(found, {"B+", "B-", "O+"})
