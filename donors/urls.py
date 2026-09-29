from django.urls import path

from .views import donor_dashboard, register_donor, search_donors

urlpatterns = [

    path("", register_donor, name="register_donor"),
    path("search/", search_donors, name="search_donors"),
    path("dashboard/", donor_dashboard, name="donor_dashboard"),

]