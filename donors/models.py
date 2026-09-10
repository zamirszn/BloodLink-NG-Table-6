from django.db import models

class Donor(models.Model):
    name = models.CharField(max_length=100)
    blood_type = models.CharField(max_length=3)
    genotype = models.CharField(max_length=2)
    location = models.CharField(max_length=200)
    phone = models.CharField(max_length=20)
    last_donation = models.DateField(null=True, blank=True)
    availability = models.BooleanField(default=True)
# Create your models here.
