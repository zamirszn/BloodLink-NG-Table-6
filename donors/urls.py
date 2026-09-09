from django.urls import path
from .views import register_donor

urlpatterns = [
    path("", register_donor, name="register_donor"),
]