from django.urls import path

from .views import register_donor, search_donors

urlpatterns = [

    path("", register_donor, name="register_donor"),
    path("search/", search_donors, name="search_donors"),

]