from django.urls import path

from . import views

app_name = "blood_requests"

urlpatterns = [
    path("", views.blood_request_list, name="blood_request_list"),
    path("new/", views.blood_request_create, name="blood_request_create"),
    path("alerts/", views.my_alerts, name="my_alerts"),

    # A donor's personal reply link. This is what an alert carries, and it is
    # the one route here that is not login-gated: it is opened from a handset.
    path("respond/<str:token>/", views.alert_respond, name="alert_respond"),

    # The requester's control panel and its actions. Behind a login, and then
    # gated again by the request's status token, never by its primary key.
    path("manage/<str:token>/", views.blood_request_manage, name="blood_request_manage"),
    path("manage/<str:token>/notify/", views.blood_request_notify, name="blood_request_notify"),
    path("manage/<str:token>/status/", views.blood_request_status, name="blood_request_status"),

    # Read-only summary. Carries no donor contact details.
    path("<int:pk>/", views.blood_request_detail, name="blood_request_detail"),
]
