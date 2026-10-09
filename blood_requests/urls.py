from django.urls import path

from . import views, webhooks

app_name = "blood_requests"

urlpatterns = [
    path("", views.blood_request_list, name="blood_request_list"),
    path("new/", views.blood_request_create, name="blood_request_create"),
    path("alerts/", views.my_alerts, name="my_alerts"),

    # Provider callbacks (secret-token authenticated, no login or CSRF).
    path("webhooks/sms/receipt/", webhooks.delivery_receipt, name="sms_receipt"),
    path("webhooks/sms/inbound/", webhooks.inbound_sms, name="sms_inbound"),

    # A donor's personal reply link. This is what an alert carries, and it is
    # the one route here that is not login-gated: it is opened from a handset.
    path("respond/<str:token>/", views.alert_respond, name="alert_respond"),

    # The requester's control panel and its actions. Behind a login, and then
    # gated again by the request's status token, never by its primary key.
    path("manage/<str:token>/", views.blood_request_manage, name="blood_request_manage"),
    path("manage/<str:token>/notify/", views.blood_request_notify, name="blood_request_notify"),
    path("manage/<str:token>/extend/", views.blood_request_extend, name="blood_request_extend"),
    path("manage/<str:token>/donated/<int:alert_id>/", views.record_donation, name="record_donation"),
    path("mine/<int:pk>/", views.blood_request_manage_by_id, name="blood_request_manage_by_id"),
    path("donations/<int:pk>/confirm/", views.confirm_donation, name="confirm_donation"),
    path("<int:pk>/volunteer/", views.volunteer, name="volunteer"),
    path("manage/<str:token>/retry/", views.blood_request_retry, name="blood_request_retry"),
    path("manage/<str:token>/verify/", views.requester_verify, name="requester_verify"),
    path("manage/<str:token>/status/", views.blood_request_status, name="blood_request_status"),

    # Read-only summary. Carries no donor contact details.
    path("<int:pk>/", views.blood_request_detail, name="blood_request_detail"),
]
