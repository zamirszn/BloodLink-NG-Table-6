from django.urls import path

from .views import (
    donor_dashboard,
    edit_donor,
    login_view,
    logout_view,
    register_donor,
    search_donors,
    home,
    password_reset_confirm,
    password_reset_request,
    verify_phone,
)

urlpatterns = [

    path("", home, name="home"),
    path("register/", register_donor, name="register_donor"),
    path("login/", login_view, name="login"),
    path("logout/", logout_view, name="logout"),
    path("search/", search_donors, name="search_donors"),
    path("dashboard/", donor_dashboard, name="donor_dashboard"),
    path("dashboard/edit/", edit_donor, name="edit_donor"),
    path("verify/", verify_phone, name="verify_phone"),
    path("password-reset/", password_reset_request, name="password_reset_request"),
    path("password-reset/confirm/", password_reset_confirm, name="password_reset_confirm"),

]