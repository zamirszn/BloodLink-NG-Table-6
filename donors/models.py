import re

from django.db import models

class Donor(models.Model):
    name = models.CharField(max_length=100)
    blood_type = models.CharField(max_length=3)
    genotype = models.CharField(max_length=2)
    location = models.CharField(max_length=200)
    phone = models.CharField(max_length=20)
    last_donation = models.DateField(null=True, blank=True)
    availability = models.BooleanField(default=True)

    @property
    def tel_phone(self):
        """The phone field reduced to a safe `tel:` link target.

        Only digits are kept, with an optional single leading "+" preserved
        for international numbers. Returns "" when there is no usable number,
        so the template can skip the link instead of rendering a broken one.
        """
        raw = (self.phone or "").strip()

        if not raw:
            return ""

        digits = re.sub(r"\D", "", raw)

        if not digits:
            return ""

        return f"+{digits}" if raw.startswith("+") else digits

    def __str__(self):
        return self.name

# Create your models here.

