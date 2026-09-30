from django.urls import path

from .views import (
    donor_dashboard,
    edit_donor,
    login_view,
    logout_view,
    register_donor,
    search_donors,
    home,
)

urlpatterns = [

    path("", home, name="home"),
    path("register/", register_donor, name="register_donor"),
    path("login/", login_view, name="login"),
    path("logout/", logout_view, name="logout"),
    path("search/", search_donors, name="search_donors"),
    path("dashboard/", donor_dashboard, name="donor_dashboard"),
    path("dashboard/edit/", edit_donor, name="edit_donor"),

]