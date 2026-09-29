"""TEMPORARY verification test for the Edit Profile feature.

Delete this file after running.
"""

from datetime import date, timedelta

from django.test import TestCase
from django.urls import reverse

from .models import Donor


class EditProfileTests(TestCase):

    def setUp(self):
        self.donor = Donor.objects.create(
            name="Ada Obi",
            blood_type="O+",
            genotype="AA",
            location="Enugu",
            phone="08012345678",
            last_donation=None,
            availability=True,
        )
        self.other = Donor.objects.create(
            name="Bola Eze",
            blood_type="A+",
            genotype="AS",
            location="Lagos",
            phone="08099999999",
            last_donation=None,
            availability=True,
        )

        session = self.client.session
        session["donor_id"] = self.donor.pk
        session.save()

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

    def test_dashboard_links_to_edit_page(self):
        response = self.client.get(reverse("donor_dashboard"))
        self.assertContains(response, reverse("edit_donor"))
        self.assertContains(response, "Edit Profile")

    def test_get_edit_page_is_prefilled_from_the_session_donor(self):
        response = self.client.get(reverse("edit_donor"))

        self.assertEqual(response.status_code, 200)

        form = response.context["form"]
        self.assertEqual(form.instance.pk, self.donor.pk)
        self.assertEqual(form.initial["name"], "Ada Obi")
        self.assertEqual(form.initial["location"], "Enugu")
        self.assertEqual(form.initial["donation_status"], "never")

    def test_get_edit_page_preselects_the_date_for_previous_donors(self):
        last_donation = date.today() - timedelta(days=200)
        self.donor.last_donation = last_donation
        self.donor.save()

        response = self.client.get(reverse("edit_donor"))

        self.assertEqual(response.context["form"].initial["donation_status"], "before")
        self.assertContains(
            response,
            f'<input type="date" name="last_donation" value="{last_donation.isoformat()}"',
        )
        self.assertContains(
            response,
            '<option value="before" selected>I have donated before</option>',
        )

    def test_get_edit_page_preselects_not_available(self):
        self.donor.availability = False
        self.donor.save()

        response = self.client.get(reverse("edit_donor"))

        self.assertContains(
            response,
            '<option value="False" selected>No</option>',
            html=False,
        )

    def test_post_updates_existing_record_and_does_not_create_a_new_one(self):
        response = self.client.post(reverse("edit_donor"), self._payload(
            name="Ada Obi-Nwosu",
            blood_type="A-",
            genotype="AS",
            location="Abuja",
            phone="07011112222",
            donation_status="before",
            last_donation=(date.today() - timedelta(days=10)).isoformat(),
            availability="False",
        ))

        self.assertRedirects(response, reverse("donor_dashboard"))
        self.assertEqual(Donor.objects.count(), 2)

        self.donor.refresh_from_db()
        self.assertEqual(self.donor.name, "Ada Obi-Nwosu")
        self.assertEqual(self.donor.blood_type, "A-")
        self.assertEqual(self.donor.genotype, "AS")
        self.assertEqual(self.donor.location, "Abuja")
        self.assertEqual(self.donor.phone, "07011112222")
        self.assertEqual(self.donor.last_donation, date.today() - timedelta(days=10))
        self.assertFalse(self.donor.availability)

    def test_other_donor_is_never_touched(self):
        self.client.post(reverse("edit_donor"), self._payload(name="Changed"))

        self.other.refresh_from_db()
        self.assertEqual(self.other.name, "Bola Eze")
        self.assertEqual(self.other.location, "Lagos")

    def test_session_donor_id_is_unchanged_after_edit(self):
        self.client.post(reverse("edit_donor"), self._payload(name="Changed"))

        self.assertEqual(self.client.session["donor_id"], self.donor.pk)

    def test_success_message_is_shown_on_the_dashboard(self):
        response = self.client.post(
            reverse("edit_donor"),
            self._payload(name="Ada Obi-Nwosu"),
            follow=True,
        )

        self.assertContains(response, "Your profile has been updated.")
        self.assertContains(response, "Ada Obi-Nwosu")

    def test_donation_status_validation_is_still_enforced(self):
        response = self.client.post(reverse("edit_donor"), self._payload(
            donation_status="before",
            last_donation="",
        ))

        self.assertEqual(response.status_code, 200)
        self.assertFormError(
            response.context["form"],
            "last_donation",
            "Please enter your last donation date.",
        )

        self.donor.refresh_from_db()
        self.assertEqual(self.donor.name, "Ada Obi")

    def test_invalid_blood_type_is_rejected(self):
        response = self.client.post(reverse("edit_donor"), self._payload(
            blood_type="Z+",
        ))

        self.assertEqual(response.status_code, 200)
        self.assertFormError(
            response.context["form"],
            "blood_type",
            "Select a valid choice. Z+ is not one of the available choices.",
        )

    def test_edit_without_a_donor_in_session_redirects_to_dashboard(self):
        session = self.client.session
        del session["donor_id"]
        session.save()

        response = self.client.get(reverse("edit_donor"))

        self.assertRedirects(response, reverse("donor_dashboard"))

    def test_edit_without_a_donor_in_session_cannot_create_a_record(self):
        session = self.client.session
        del session["donor_id"]
        session.save()

        response = self.client.post(reverse("edit_donor"), self._payload())

        self.assertRedirects(response, reverse("donor_dashboard"))
        self.assertEqual(Donor.objects.count(), 2)
        self.donor.refresh_from_db()
        self.assertEqual(self.donor.name, "Ada Obi")

    def test_edit_when_the_session_donor_was_deleted_does_not_edit_others(self):
        self.donor.delete()

        response = self.client.get(reverse("edit_donor"))

        self.assertRedirects(response, reverse("donor_dashboard"))

    def test_dashboard_still_shows_the_90_day_eligibility_after_edit(self):
        self.client.post(reverse("edit_donor"), self._payload(
            donation_status="before",
            last_donation=(date.today() - timedelta(days=10)).isoformat(),
        ))

        response = self.client.get(reverse("donor_dashboard"))

        self.assertFalse(response.context["is_eligible"])
        self.assertEqual(
            response.context["next_eligible_date"],
            date.today() + timedelta(days=80),
        )
        self.assertContains(response, "Not yet eligible")

    def test_90_day_wait_is_over_after_an_old_donation_date(self):
        self.client.post(reverse("edit_donor"), self._payload(
            donation_status="before",
            last_donation=(date.today() - timedelta(days=100)).isoformat(),
        ))

        response = self.client.get(reverse("donor_dashboard"))

        self.assertTrue(response.context["is_eligible"])
        self.assertContains(response, "Eligible to donate")

    def test_edited_donor_is_found_by_search_with_the_new_details(self):
        self.client.post(reverse("edit_donor"), self._payload(
            location="Abuja",
            blood_type="O-",
        ))

        response = self.client.get(reverse("search_donors"), {"location": "Abuja"})

        self.assertContains(response, "Ada Obi")
        self.assertEqual(
            [d.pk for d in response.context["donors"]],
            [self.donor.pk],
        )

    def test_registration_flow_is_unchanged(self):
        response = self.client.post(reverse("register_donor"), {
            "name": "Chidi Umeh",
            "blood_type": "B+",
            "genotype": "AA",
            "location": "Kano",
            "phone": "08033334444",
            "donation_status": "never",
            "last_donation": "",
            "availability": "True",
        })

        self.assertRedirects(response, reverse("donor_dashboard"))
        self.assertEqual(Donor.objects.count(), 3)

        new_donor = Donor.objects.get(name="Chidi Umeh")
        self.assertEqual(self.client.session["donor_id"], new_donor.pk)
