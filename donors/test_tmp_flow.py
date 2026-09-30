"""TEMPORARY verification test for the registration -> dashboard flow.

Delete this file after running.
"""

from django.test import TestCase
from django.urls import reverse

from .models import Donor


class RegistrationToDashboardFlowTests(TestCase):

    def _payload(self, **overrides):
        data = {
            "name": "Ada Obi",
            "blood_type": "O+",
            "genotype": "AA",
            "location": "Enugu",
            "phone": "08012345678",
            "donation_status": "never",
            "last_donation": "",
            "availability": "True",
        }
        data.update(overrides)
        return data

    def test_register_redirects_to_dashboard_and_shows_that_donor(self):
        response = self.client.post("/", self._payload())

        # 1. redirect to /dashboard/
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/dashboard/")

        # 2. donor was actually saved
        self.assertEqual(Donor.objects.count(), 1)
        donor = Donor.objects.get()

        # 3. dashboard shows THIS donor
        dashboard = self.client.get("/dashboard/")
        self.assertEqual(dashboard.status_code, 200)
        self.assertContains(dashboard, "Ada Obi")
        self.assertContains(dashboard, "O+")
        self.assertContains(dashboard, "Enugu")

        # 4. session holds the saved donor's pk
        self.assertEqual(self.client.session["donor_id"], donor.pk)

    def test_dashboard_shows_only_the_most_recent_donor(self):
        Donor.objects.create(
            name="Earlier Donor", blood_type="A+", genotype="AS",
            location="Lagos", phone="08000000000", availability=True,
        )

        self.client.post("/", self._payload(name="Newest Donor", location="Kano"))

        dashboard = self.client.get("/dashboard/")
        self.assertContains(dashboard, "Newest Donor")
        self.assertNotContains(dashboard, "Earlier Donor")

    def test_refresh_after_registration_does_not_resubmit(self):
        self.client.post("/", self._payload())

        # Simulate the browser refreshing the landing page (a GET).
        self.client.get("/dashboard/")
        self.client.get("/dashboard/")

        self.assertEqual(Donor.objects.count(), 1)

    def test_dashboard_without_prior_registration_is_empty(self):
        response = self.client.get("/dashboard/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No donor has been registered")

    def test_invalid_submission_does_not_save_or_redirect(self):
        # "before" donation status requires a last_donation date.
        response = self.client.post(
            "/", self._payload(donation_status="before", last_donation="")
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Donor.objects.count(), 0)

    def test_availability_is_saved_as_a_real_boolean(self):
        self.client.post("/", self._payload(availability="False"))
        donor = Donor.objects.get()
        self.assertIs(donor.availability, False)
